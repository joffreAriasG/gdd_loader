from datetime import date

import pytest

from gdd_loader.domain import plantilla as pl

CONTRATO = {
    "DetalleAtributos": ["codigo_dominio", "codigo_atributo", "atributo"],
    "Respaldos": ["tipo_respaldo", "enlace_respaldo"],
}


def _version(estado="VIGENTE", version="1.0.0", minima="0.2.0", gracia=None, columnas=None):
    columnas = columnas or CONTRATO
    return pl.VersionPlantilla(
        id_plantilla="GDD-DOMINIO", version=version, estado=estado,
        hash_estructura=pl.calcular_hash_estructura(columnas),
        version_loader_minima=minima, columnas=columnas, fecha_fin_gracia=gracia,
    )


HOY = date(2026, 9, 25)


# --- Huella -------------------------------------------------------------------


def test_hash_no_depende_del_orden_de_las_hojas():
    invertido = dict(reversed(list(CONTRATO.items())))
    assert pl.calcular_hash_estructura(CONTRATO) == pl.calcular_hash_estructura(invertido)


def test_hash_cambia_si_cambia_el_orden_de_las_columnas():
    movido = {**CONTRATO, "Respaldos": ["enlace_respaldo", "tipo_respaldo"]}
    assert pl.calcular_hash_estructura(CONTRATO) != pl.calcular_hash_estructura(movido)


def test_hash_normaliza_espacios_y_tildes_compuestas():
    descompuesta = "raíz"  # "raíz" con tilde combinada
    a = {"Hoja": [" causa_raíz "]}
    b = {"Hoja": [f"causa_{descompuesta}"]}
    assert pl.calcular_hash_estructura(a) == pl.calcular_hash_estructura(b)


def test_limpiar_encabezados_quita_vacios_al_final_pero_no_en_medio():
    assert pl.limpiar_encabezados(["a", None, "b", None, "  "]) == ["a", "", "b"]


def test_restringir_ignora_hojas_auxiliares():
    archivo = {**CONTRATO, "Lista De Referencia": ["id", "vals"]}
    assert pl.restringir_a_contrato(archivo, CONTRATO) == CONTRATO


def test_diferencias_describe_faltantes_sobrantes_y_orden():
    real = {
        "DetalleAtributos": ["codigo_dominio", "atributo", "extra"],
    }
    difs = pl.diferencias_estructura(CONTRATO, real)
    assert "falta la hoja 'Respaldos'" in difs
    assert any("faltan columnas ['codigo_atributo']" in d for d in difs)
    assert any("columnas no esperadas ['extra']" in d for d in difs)

    orden = {**CONTRATO, "Respaldos": ["enlace_respaldo", "tipo_respaldo"]}
    assert pl.diferencias_estructura(CONTRATO, orden) == [
        "hoja 'Respaldos': mismas columnas en distinto orden"
    ]


# --- Versiones ------------------------------------------------------------------


def test_parsear_version_valida_semver():
    assert pl.parsear_version("1.10.2") == (1, 10, 2)
    with pytest.raises(ValueError):
        pl.parsear_version("1.2")


def test_version_minima_compara_numericamente():
    assert pl.version_minima_cumplida("0.10.0", "0.2.0")
    assert not pl.version_minima_cumplida("0.1.9", "0.2.0")


def test_control_desde_dict_limpia_valores():
    c = pl.ControlPlantilla.desde_dict(
        {"id_plantilla": " GDD-DOMINIO ", "version_plantilla": "1.0.0", "id_carga_base": "12", "codigo_dominio": ""}
    )
    assert c.completo and c.id_plantilla == "GDD-DOMINIO" and c.id_carga_base == 12
    assert c.codigo_dominio is None
    assert not pl.ControlPlantilla.desde_dict({"id_plantilla": "X"}).completo


def test_identificar_por_huella_prefiere_vigente_y_nunca_borrador():
    otra = {**CONTRATO, "Respaldos": ["tipo_respaldo", "enlace_respaldo", "nueva"]}
    v1 = _version(estado="DEPRECADA", version="1.0.0")
    v1b = _version(estado="VIGENTE", version="1.0.1")  # misma huella (PATCH)
    borrador = _version(estado="BORRADOR", version="1.1.0", columnas=otra)
    archivo = {**CONTRATO, "Aux": ["x"]}

    assert pl.identificar_por_huella(archivo, [v1, v1b, borrador]).version == "1.0.1"
    assert pl.identificar_por_huella(otra, [v1, v1b, borrador]) is None


# --- evaluar_version ----------------------------------------------------------


def test_acepta_version_vigente_con_estructura_correcta():
    d = pl.evaluar_version(_version(), {**CONTRATO, "Aux": ["x"]}, "0.2.0", HOY)
    assert d.aceptada and d.estado_rechazo is None and d.advertencias == []


def test_rechaza_version_no_registrada():
    d = pl.evaluar_version(None, CONTRATO, "0.2.0", HOY, version_declarada="9.9.9")
    assert d.estado_rechazo == pl.RECHAZADA_VERSION and "9.9.9" in d.mensaje


@pytest.mark.parametrize("estado", ["BORRADOR", "RETIRADA"])
def test_rechaza_borrador_y_retirada(estado):
    d = pl.evaluar_version(_version(estado=estado), CONTRATO, "0.2.0", HOY)
    assert not d.aceptada and d.estado_rechazo == pl.RECHAZADA_VERSION


def test_deprecada_dentro_de_gracia_se_acepta_con_advertencia():
    d = pl.evaluar_version(_version(estado="DEPRECADA", gracia=HOY), CONTRATO, "0.2.0", HOY)
    assert d.aceptada and d.advertencias


def test_deprecada_vencida_o_sin_fecha_se_rechaza():
    vencida = _version(estado="DEPRECADA", gracia=date(2026, 9, 24))
    sin_fecha = _version(estado="DEPRECADA", gracia=None)
    assert pl.evaluar_version(vencida, CONTRATO, "0.2.0", HOY).estado_rechazo == pl.RECHAZADA_VERSION
    assert pl.evaluar_version(sin_fecha, CONTRATO, "0.2.0", HOY).estado_rechazo == pl.RECHAZADA_VERSION


def test_rechaza_loader_desactualizado():
    d = pl.evaluar_version(_version(minima="0.3.0"), CONTRATO, "0.2.0", HOY)
    assert d.estado_rechazo == pl.RECHAZADA_LOADER and "git pull" in d.mensaje


def test_rechaza_estructura_distinta_con_detalle():
    real = {**CONTRATO, "Respaldos": ["tipo_respaldo", "enlace", "extra"]}
    d = pl.evaluar_version(_version(), real, "0.2.0", HOY)
    assert d.estado_rechazo == pl.RECHAZADA_ESTRUCTURA
    assert "enlace_respaldo" in d.mensaje and "extra" in d.mensaje


def test_rechaza_registro_inconsistente():
    v = pl.VersionPlantilla("GDD-DOMINIO", "1.0.0", "VIGENTE", "0" * 64, "0.2.0", CONTRATO)
    d = pl.evaluar_version(v, CONTRATO, "0.2.0", HOY)
    assert d.estado_rechazo == pl.RECHAZADA_VERSION and "inconsistente" in d.mensaje
