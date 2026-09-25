from unittest.mock import MagicMock

import pandas as pd

from gdd_loader.domain.sheet_config import SheetConfig
from gdd_loader.pipeline.carga_staging import ejecutar


def _escribir_excel(path, hojas: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for nombre, df in hojas.items():
            df.to_excel(writer, sheet_name=nombre, index=False)


def test_ejecutar_marca_el_nombre_del_archivo_en_cada_resultado(tmp_path):
    archivo = tmp_path / "DominioA.xlsx"
    # Bajo estrategia DOMINIO, ejecutar() siempre ancla el dominio del
    # archivo en una hoja "DetalleAtributos" (ver docstring de
    # pipeline/carga_staging.ejecutar) -- se nombra asi tambien aqui para
    # que el escenario sea realista.
    _escribir_excel(
        archivo, {"DetalleAtributos": pd.DataFrame({"codigo_dominio": ["A"], "valor": ["x"]})}
    )
    cfg = SheetConfig(
        nombre_hoja="DetalleAtributos",
        tabla_staging="staging.tabla1",
        columnas=["codigo_dominio", "valor"],
        columna_clave="codigo_dominio",
    )
    repo = MagicMock()
    repo.cargar.return_value = 1

    resultados = ejecutar(archivo, [cfg], repo, "DOMINIO")

    assert len(resultados) == 1
    assert resultados[0].archivo == "DominioA.xlsx"
    assert resultados[0].error is None
    assert resultados[0].filas_cargadas == 1


def test_ejecutar_sigue_con_la_siguiente_hoja_si_una_falla(tmp_path):
    archivo = tmp_path / "DominioB.xlsx"
    # "HojaFalla" no se crea a proposito -> leer_hoja fallara al no
    # encontrarla. "DetalleAtributos" SI esta presente -- sirve de ancla
    # del dominio del archivo (ver nota de ejecutar()) ademas de ser la
    # hoja "OK" del escenario.
    _escribir_excel(archivo, {"DetalleAtributos": pd.DataFrame({"codigo_dominio": ["B"]})})
    cfg_falla = SheetConfig(
        nombre_hoja="HojaFalla",
        tabla_staging="staging.tabla_falla",
        columnas=["codigo_dominio"],
        columna_clave="codigo_dominio",
    )
    cfg_ok = SheetConfig(
        nombre_hoja="DetalleAtributos",
        tabla_staging="staging.tabla_ok",
        columnas=["codigo_dominio"],
        columna_clave="codigo_dominio",
    )
    repo = MagicMock()
    repo.cargar.return_value = 1

    resultados = ejecutar(archivo, [cfg_falla, cfg_ok], repo, "DOMINIO")

    assert len(resultados) == 2
    assert resultados[0].hoja == "HojaFalla"
    assert resultados[0].error is not None
    assert resultados[1].hoja == "DetalleAtributos"
    assert resultados[1].error is None
    assert resultados[1].filas_cargadas == 1


def test_ejecutar_hereda_codigo_dominio_de_otra_hoja(tmp_path):
    archivo = tmp_path / "DominioC.xlsx"
    _escribir_excel(
        archivo,
        {
            "DetalleAtributos": pd.DataFrame({"codigo_dominio": ["ADS", "ADS"]}),
            "Investigacion": pd.DataFrame({"clasificacion": ["Término"], "nombre": ["X"]}),
        },
    )
    cfg_detalle = SheetConfig(
        nombre_hoja="DetalleAtributos",
        tabla_staging="staging.detalle_atributos",
        columnas=["codigo_dominio"],
        columna_clave="codigo_dominio",
    )
    cfg_investigacion = SheetConfig(
        nombre_hoja="Investigacion",
        tabla_staging="staging.investigacion",
        columnas=["clasificacion", "nombre"],
        columna_clave="codigo_dominio",
        hereda_codigo_dominio_de="DetalleAtributos",
    )
    repo = MagicMock()
    repo.cargar.return_value = 1

    resultados = ejecutar(archivo, [cfg_detalle, cfg_investigacion], repo, "DOMINIO")

    assert all(r.error is None for r in resultados)
    # segunda llamada a repo.cargar es la de Investigacion -- el df pasado
    # debe traer codigo_dominio agregado con el valor heredado de DetalleAtributos.
    df_investigacion = repo.cargar.call_args_list[1].args[0]
    assert (df_investigacion["codigo_dominio"] == "ADS").all()


def test_ejecutar_falla_si_la_hoja_fuente_trae_mas_de_un_codigo_dominio(tmp_path):
    archivo = tmp_path / "DominioD.xlsx"
    _escribir_excel(
        archivo,
        {
            "DetalleAtributos": pd.DataFrame({"codigo_dominio": ["ADS", "OTRO"]}),
            "Investigacion": pd.DataFrame({"clasificacion": ["Término"], "nombre": ["X"]}),
        },
    )
    cfg_detalle = SheetConfig(
        nombre_hoja="DetalleAtributos",
        tabla_staging="staging.detalle_atributos",
        columnas=["codigo_dominio"],
        columna_clave="codigo_dominio",
    )
    cfg_investigacion = SheetConfig(
        nombre_hoja="Investigacion",
        tabla_staging="staging.investigacion",
        columnas=["clasificacion", "nombre"],
        columna_clave="codigo_dominio",
        hereda_codigo_dominio_de="DetalleAtributos",
    )
    repo = MagicMock()
    repo.cargar.return_value = 1

    resultados = ejecutar(archivo, [cfg_detalle, cfg_investigacion], repo, "DOMINIO")

    resultado_investigacion = next(r for r in resultados if r.hoja == "Investigacion")
    assert resultado_investigacion.error is not None
    assert "codigo_dominio" in resultado_investigacion.error


def test_ejecutar_pasa_codigo_dominio_archivo_aunque_la_hoja_este_vacia(tmp_path):
    """Regresion del bug real reportado por el usuario 2026-09-17: vacio la
    hoja PlanDeRemediacion (0 filas) como prueba y el borrado de staging
    nunca se disparaba porque se derivaba del contenido de esa misma hoja.
    codigo_dominio_archivo debe llegar a repo.cargar tomado de
    DetalleAtributos, sin importar que la otra hoja este vacia.
    """
    archivo = tmp_path / "DominioE.xlsx"
    _escribir_excel(
        archivo,
        {
            "DetalleAtributos": pd.DataFrame({"codigo_dominio": ["ADS", "ADS"]}),
            "PlanDeRemediacion": pd.DataFrame({"codigo_dominio_atributo": pd.Series(dtype="object")}),
        },
    )
    cfg_detalle = SheetConfig(
        nombre_hoja="DetalleAtributos",
        tabla_staging="staging.detalle_atributos",
        columnas=["codigo_dominio"],
        columna_clave="codigo_dominio",
    )
    cfg_plan = SheetConfig(
        nombre_hoja="PlanDeRemediacion",
        tabla_staging="staging.plan_remediacion",
        columnas=["codigo_dominio_atributo"],
        columna_clave="codigo_dominio_atributo",
        columna_clave_prefijo_dominio=True,
    )
    repo = MagicMock()
    repo.cargar.return_value = 0

    resultados = ejecutar(archivo, [cfg_detalle, cfg_plan], repo, "DOMINIO")

    assert all(r.error is None for r in resultados)
    assert repo.cargar.call_count == 2
    for llamada in repo.cargar.call_args_list:
        assert llamada.kwargs["codigo_dominio_archivo"] == "ADS"


def test_ejecutar_aborta_todo_el_archivo_si_falta_detalleatributos_con_estrategia_dominio(tmp_path):
    """Sin una hoja DetalleAtributos no hay forma segura de acotar el
    borrado para NINGUNA hoja bajo estrategia DOMINIO -- se aborta el
    archivo completo (todas las hojas quedan con error) en vez de cargar
    staging con un borrado a medias o silenciosamente omitido.
    """
    archivo = tmp_path / "DominioF.xlsx"
    _escribir_excel(archivo, {"Investigacion": pd.DataFrame({"clasificacion": ["Término"]})})
    cfg_investigacion = SheetConfig(
        nombre_hoja="Investigacion",
        tabla_staging="staging.investigacion",
        columnas=["clasificacion"],
        columna_clave="codigo_dominio",
        hereda_codigo_dominio_de="DetalleAtributos",
    )
    repo = MagicMock()

    resultados = ejecutar(archivo, [cfg_investigacion], repo, "DOMINIO")

    assert len(resultados) == 1
    assert resultados[0].error is not None
    assert "DetalleAtributos" in resultados[0].error
    repo.cargar.assert_not_called()


def test_ejecutar_con_truncate_no_requiere_hoja_detalleatributos(tmp_path):
    """TRUNCATE no acota por dominio (vacia toda la tabla) -- no deberia
    depender de que exista una hoja DetalleAtributos en absoluto.
    """
    archivo = tmp_path / "DominioG.xlsx"
    _escribir_excel(archivo, {"Hoja1": pd.DataFrame({"codigo_dominio": ["A"]})})
    cfg = SheetConfig(
        nombre_hoja="Hoja1",
        tabla_staging="staging.tabla1",
        columnas=["codigo_dominio"],
        columna_clave="codigo_dominio",
    )
    repo = MagicMock()
    repo.cargar.return_value = 1

    resultados = ejecutar(archivo, [cfg], repo, "TRUNCATE")

    assert resultados[0].error is None
    assert repo.cargar.call_args.kwargs["codigo_dominio_archivo"] is None
