"""Notificacion de cargas: instantanea legible, diferencias, minuta y evento JSON.
Sin SQL Server: las consultas se simulan con filas en memoria."""

import datetime
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from gdd_loader.notificacion import instantanea as ins
from gdd_loader.notificacion.diferencias import comparar, resumen
from gdd_loader.notificacion.evento import Notificador, armar_asunto, escribir_evento
from gdd_loader.notificacion.instantanea import Registro
from gdd_loader.notificacion.minuta import DatosMinuta, generar_minuta_html

# --- a_texto ------------------------------------------------------------------


@pytest.mark.parametrize("valor,esperado", [
    (None, ""), ("", ""), ("  hola ", "hola"), (True, "Sí"), (False, "No"),
    (4, "4"), (Decimal("12.500"), "12.5"), (Decimal("30"), "30"), (2.0, "2"),
    (datetime.date(2026, 1, 31), "2026-01-31"),
    (datetime.datetime(2026, 1, 31, 0, 0), "2026-01-31"),
    (datetime.datetime(2026, 1, 31, 8, 5, 3), "2026-01-31 08:05:03"),
])
def test_a_texto_normaliza_valores_de_sql_server(valor, esperado):
    assert ins.a_texto(valor) == esperado


# --- construir_registros --------------------------------------------------------


def _fila_atributo(codigo=4, nombre="Numero de poliza", criticidad="Alta", version="1", **extra):
    fila = {c: None for c in ins.ATRIBUTOS.campos}
    fila.update(codigo_atributo=codigo, nombre_atributo=nombre, criticidad=criticidad,
                version=version, es_dato_personal=False)
    fila.update(extra)
    return fila


def test_construir_registros_usa_clave_natural_y_etiqueta_legible():
    registros = ins.construir_registros(ins.ATRIBUTOS, [_fila_atributo()])
    assert list(registros) == [("4",)]
    reg = registros[("4",)]
    assert reg.etiqueta == "4 – Numero de poliza"
    assert reg.campos["Criticidad"] == "Alta"
    assert reg.campos["Dato personal"] == "No"
    assert reg.campos["Descripción"] == ""


def test_fuente_oficial_clave_incluye_bdd_y_marca_primaria():
    fila = {c: None for c in ins.FUENTES_OFICIALES.campos}
    fila.update(codigo_atributo=4, nombre_atributo="x", servidor="SRV1", base_datos="NOVA", tabla="POLIZAS",
                clase="CLI", nombre_campo="NUMPOL", es_fuente_primaria=True, longitud_campo=10)
    registros = ins.construir_registros(ins.FUENTES_OFICIALES, [fila])
    clave = ("4", "SRV1", "NOVA", "POLIZAS", "CLI", "NUMPOL", "Sí")
    assert list(registros) == [clave]
    assert registros[clave].etiqueta == "Atributo 4 – SRV1/NOVA/POLIZAS/CLI/NUMPOL (primaria)"
    assert registros[clave].tabla["Tabla / archivo"] == "POLIZAS"
    assert registros[clave].campos["Longitud"] == "10"


def test_fuente_oficial_informa_cambio_de_version():
    def fuente(version):
        fila = {c: None for c in ins.FUENTES_OFICIALES.campos}
        fila.update(codigo_atributo=4, nombre_atributo="x", servidor="SRV1", base_datos="NOVA", tabla="POLIZAS",
                    clase="CLI", nombre_campo="NUMPOL", es_fuente_primaria=True, version=version)
        return {"Fuentes oficiales": ins.construir_registros(ins.FUENTES_OFICIALES, [fila])}

    fuentes = comparar(fuente("3"), fuente("1"))[1]
    assert fuentes.entidad == "Fuentes oficiales"
    [mod] = fuentes.modificados
    assert [(c.campo, c.anterior, c.nuevo) for c in mod.campos] == [("Versión", "3", "1")]


def test_todas_las_entidades_tienen_clave_en_su_select():
    for e in ins.ENTIDADES:
        for columna in e.clave + tuple(e.campos):
            assert columna in e.sql, f"{e.nombre}: {columna} no aparece en el SELECT"


class ConexionFalsa:
    """Devuelve filas por consulta segun un texto que aparezca en el SQL."""

    def __init__(self, filas_por_tabla):
        self.filas_por_tabla = filas_por_tabla
        self.parametros = []

    def execute(self, sentencia, parametros=None):
        self.parametros.append(parametros)
        sql = str(sentencia)
        filas = next((f for t, f in self.filas_por_tabla.items() if t in sql), [])
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: filas),
                               all=lambda: [tuple(f.values()) for f in filas],
                               first=lambda: tuple(filas[0].values()) if filas else None)


def test_tomar_instantanea_consulta_las_7_entidades_con_el_dominio():
    conn = ConexionFalsa({"FROM gdd.atributo a\n": [_fila_atributo()]})
    foto = ins.tomar_instantanea(conn, "ADS")
    assert list(foto) == [e.nombre for e in ins.ENTIDADES]
    assert all(p == {"cod": "ADS"} for p in conn.parametros)
    assert ("4",) in foto["Atributos"]


def test_responsables_a_notificar_sin_duplicados_ni_vacios():
    conn = ConexionFalsa({"dominio_responsable": [{"n": "Ana Perez"}, {"n": " "}, {"n": "Ana Perez"}]})
    assert ins.responsables_a_notificar(conn, "ADS", 1) == ["Ana Perez"]
    assert conn.parametros[-1] == {"cod": "ADS", "rol": 1}


# --- comparar -------------------------------------------------------------------


def _foto(**entidades):
    base = {e.nombre: {} for e in ins.ENTIDADES}
    base.update({k.replace("_", " "): v for k, v in entidades.items()})
    return base


def test_comparar_detecta_nuevo_modificado_y_baja():
    antes = _foto(Atributos={
        ("1",): Registro("1 – Poliza", {"Nombre": "Poliza", "Versión": "1"}),
        ("2",): Registro("2 – Ramo", {"Nombre": "Ramo", "Versión": "1"}),
    })
    despues = _foto(Atributos={
        ("1",): Registro("1 – Numero de poliza", {"Nombre": "Numero de poliza", "Versión": "2"}),
        ("3",): Registro("3 – Prima", {"Nombre": "Prima", "Versión": "1"}),
    })
    atributos = comparar(antes, despues)[0]
    assert atributos.entidad == "Atributos"
    assert [c.etiqueta for c in atributos.nuevos] == ["3 – Prima"]
    assert [c.etiqueta for c in atributos.bajas] == ["2 – Ramo"]
    [mod] = atributos.modificados
    assert mod.etiqueta == "1 – Numero de poliza"
    assert [(c.campo, c.anterior, c.nuevo) for c in mod.campos] == [
        ("Nombre", "Poliza", "Numero de poliza"), ("Versión", "1", "2")]


def test_comparar_sin_diferencias_no_reporta_nada():
    foto = _foto(Respaldos={("Correo", "http://x"): Registro("Correo: http://x", {"Observaciones": "ok"})})
    cambios = comparar(foto, foto)
    assert sum(c.total for c in cambios) == 0
    assert resumen(cambios)["Respaldos"] == {"nuevos": 0, "modificados": 0, "dados_de_baja": 0}


def test_comparar_ordena_codigos_numericos_como_numeros():
    despues = _foto(Atributos={(str(n),): Registro(str(n)) for n in (10, 2, 1)})
    assert [c.etiqueta for c in comparar(_foto(), despues)[0].nuevos] == ["1", "2", "10"]


def test_comparar_devuelve_las_7_secciones_en_orden():
    assert [c.entidad for c in comparar(_foto(), _foto())] == [e.nombre for e in ins.ENTIDADES]


# --- minuta ---------------------------------------------------------------------


def _datos(**kw):
    base = dict(estado="MERGE_OK", archivo="ADS carga.xlsx", id_carga=15,
                fecha_proceso="2026-09-29T14:30:00-05:00", codigo_dominio="ADS",
                nombre_dominio="Administración De Seguros", version_plantilla="1.0.0",
                ambiente="PRUEBAS", version_loader="0.5.0")
    base.update(kw)
    return DatosMinuta(**base)


def test_minuta_lista_cambios_y_pide_aprobacion():
    antes = _foto(Atributos={("1",): Registro("1 – Poliza", {"Nombre": "Poliza"})})
    despues = _foto(Atributos={("1",): Registro("1 – Póliza", {"Nombre": "Póliza"}),
                               ("2",): Registro("2 – Ramo", {"Nombre": "Ramo"})})
    html = generar_minuta_html(_datos(cambios=comparar(antes, despues)))
    assert "Minuta de carga · ADS – Administración De Seguros" in html
    assert "Aplicada completa" in html
    assert "Nuevos (1)" in html and "2 – Ramo" in html
    assert "Modificados (1)" in html and ">Poliza<" in html and ">Póliza<" in html
    assert "Para aprobación" in html
    assert "Ambiente de PRUEBAS" in html
    assert "Fuentes oficiales" not in html  # seccion sin cambios no aparece


def test_minuta_nuevos_y_bajas_en_tabla_con_columnas_de_la_seccion():
    def resp(plaza, nombre):
        return Registro(f"Plaza {plaza} – {nombre}", {"Responsable": nombre},
                        tabla={"Código de plaza": plaza, "Plaza": "Analista", "Responsable": nombre,
                               "Rol": "Data Steward", "Área": "Seguros", "Fecha aprobada": ""})
    antes = _foto(Responsables={("P-1",): resp("P-1", "Ana Perez")})
    despues = _foto(Responsables={("P-2",): resp("P-2", "Juan Torres")})
    cambios = comparar(antes, despues)
    responsables = next(c for c in cambios if c.entidad == "Responsables")
    assert responsables.columnas == list(ins.RESPONSABLES.columnas_tabla.values())
    assert responsables.nuevos[0].fila["Responsable"] == "Juan Torres"
    assert responsables.bajas[0].fila["Responsable"] == "Ana Perez"
    html = generar_minuta_html(_datos(cambios=cambios))
    assert "Nuevos (1)" in html and "Dados de baja (1)" in html
    for encabezado in ("Código de plaza", "Responsable", "Rol", "Área"):
        assert f">{encabezado}</th>" in html
    assert ">Juan Torres</td>" in html and ">Ana Perez</td>" in html
    assert "<ul" not in html  # ya no hay listas con viñetas


def test_construir_registros_llena_la_fila_de_tabla():
    registros = ins.construir_registros(ins.ATRIBUTOS, [_fila_atributo(descripcion_atributo="Nro. poliza")])
    assert registros[("4",)].tabla == {
        "Código": "4", "Nombre": "Numero de poliza", "Descripción": "Nro. poliza",
        "Criticidad": "Alta", "Tipo": "", "Dato personal": "No",
        "Fecha de aprobación": "", "Versión": "1"}


def test_todas_las_columnas_de_tabla_estan_en_el_select():
    for e in ins.ENTIDADES:
        assert e.columnas_tabla, e.nombre
        for columna in e.columnas_tabla:
            assert columna in e.sql, f"{e.nombre}: {columna} no aparece en el SELECT"


def test_minuta_escapa_html_del_excel():
    despues = _foto(Investigación={("<script>",): Registro("<script>alert(1)</script>")})
    html = generar_minuta_html(_datos(cambios=comparar(_foto(), despues)))
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_minuta_rechazada_sin_detalle_es_informativa():
    html = generar_minuta_html(_datos(estado="RECHAZADA_DESACTUALIZADA", cambios=None,
                                      mensaje="La plantilla se genero sobre la carga 3"))
    assert "Rechazada: plantilla desactualizada" in html
    assert "no llegó a aplicar cambios" in html
    assert "no requiere aprobación" in html


def test_minuta_sin_cambios_reales():
    html = generar_minuta_html(_datos(cambios=comparar(_foto(), _foto())))
    assert "no generó cambios" in html and "no requiere aprobación" in html


def test_minuta_limita_filas_por_lista():
    despues = _foto(Atributos={(str(n),): Registro(f"atr {n}") for n in range(1, 8)})
    html = generar_minuta_html(_datos(cambios=comparar(_foto(), despues), max_filas=5))
    assert "atr 5" in html and "atr 6" not in html and "y 2 más" in html


def test_minuta_produccion_sin_aviso_de_pruebas():
    html = generar_minuta_html(_datos(ambiente="PRODUCCION", cambios=None))
    assert "este correo es de prueba" not in html


# --- evento ---------------------------------------------------------------------


def test_asunto_con_prefijo_de_ambiente():
    assert armar_asunto("PRUEBAS", "ADS", "MERGE_OK", "a.xlsx") == "[PRUEBAS] GDD | ADS | Aplicada completa | a.xlsx"
    assert armar_asunto("PRODUCCION", None, "RECHAZADA_VERSION", "b.xlsx") == "GDD | SIN DOMINIO | Rechazada | b.xlsx"


def test_escribir_evento_es_atomico_y_json_valido(tmp_path):
    evento = {"id_evento": "abcdef123456", "codigo_dominio": "ADS", "id_carga": 7, "texto": "Póliza"}
    ruta = escribir_evento(tmp_path / "Notificaciones", evento)
    assert ruta.name == "resultado_ADS_7_abcdef12.json"
    assert json.loads(ruta.read_text(encoding="utf-8"))["texto"] == "Póliza"
    assert not list((tmp_path / "Notificaciones").glob("*.tmp"))


def test_escribir_evento_deja_html_hermano_con_la_minuta(tmp_path):
    evento = {"id_evento": "abcdef123456", "codigo_dominio": "ADS", "id_carga": 7,
              "asunto": "[PRUEBAS] GDD | ADS | <Aplicada>", "minuta_html": "<table><tr><td>Póliza</td></tr></table>"}
    carpeta = tmp_path / "Notificaciones"
    ruta_json = escribir_evento(carpeta, evento)
    ruta_html = carpeta / "resultado_ADS_7_abcdef12.html"
    assert json.loads(ruta_json.read_text(encoding="utf-8"))["archivo_html"] == ruta_html.name
    html = ruta_html.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") and '<meta charset="utf-8">' in html
    assert "<title>[PRUEBAS] GDD | ADS | &lt;Aplicada&gt;</title>" in html
    assert "<table><tr><td>Póliza</td></tr></table>" in html
    assert sorted(p.name for p in carpeta.iterdir()) == [ruta_html.name, ruta_json.name]
    assert "archivo_html" not in evento  # no modifica el dict recibido


class EngineFalso:
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        conn = self.conn

        class _Ctx:
            def __enter__(self):
                return conn

            def __exit__(self, *a):
                return False
        return _Ctx()


def _resultado(**kw):
    base = dict(archivo_original="ADS carga.xlsx", id_carga=15, estado="MERGE_OK",
                codigo_dominio="ADS", mensaje="Carga aplicada completa.", advertencias=[],
                version_plantilla="1.0.0", subido_por="Ana Perez", cambios=None,
                motivo_sin_detalle=None, exitosa=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_notificador_escribe_evento_con_responsables_y_minuta(tmp_path):
    conn = ConexionFalsa({
        "nombre_dominio FROM": [{"n": "Administración De Seguros"}],
        "DISTINCT r.nombre_responsable": [{"n": "Ana Perez"}],
    })
    notif = Notificador(EngineFalso(conn), tmp_path, id_rol=1, ambiente="PRUEBAS",
                        version_loader="0.5.0",
                        reloj=lambda: datetime.datetime(2026, 9, 29, 14, 30).astimezone())
    despues = _foto(Atributos={("1",): Registro("1 – Poliza", {"Nombre": "Poliza"})})
    ruta = notif.notificar(_resultado(cambios=comparar(_foto(), despues)))
    evento = json.loads(Path(ruta).read_text(encoding="utf-8"))
    assert evento["version_contrato"] == "1.2"
    assert (Path(ruta).with_suffix(".html")).exists()
    assert evento["responsables"] == ["Ana Perez"]
    assert evento["nombre_dominio"] == "Administración De Seguros"
    assert evento["asunto"].startswith("[PRUEBAS] GDD | ADS | Aplicada completa")
    assert evento["resumen"]["Atributos"] == {"nuevos": 1, "modificados": 0, "dados_de_baja": 0}
    assert "1 – Poliza" in evento["minuta_html"]
    assert evento["exitosa"] is True


def test_notificador_sin_dominio_no_consulta_responsables(tmp_path):
    conn = ConexionFalsa({})
    notif = Notificador(EngineFalso(conn), tmp_path, 1, "PRUEBAS", "0.5.0")
    ruta = notif.notificar(_resultado(codigo_dominio=None, estado="RECHAZADA_ESTRUCTURA",
                                      exitosa=False))
    evento = json.loads(Path(ruta).read_text(encoding="utf-8"))
    assert evento["responsables"] == [] and evento["resumen"] == {}
    assert conn.parametros == []
