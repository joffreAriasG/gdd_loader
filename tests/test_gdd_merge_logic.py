import datetime

from gdd_loader.domain.gdd_merge_logic import (
    Accion,
    ExistenteVersionado,
    calcular_plan_merge,
)

FECHA_1 = datetime.date(2025, 1, 31)
FECHA_2 = datetime.date(2026, 6, 5)


def test_clave_nueva_se_inserta():
    plan = calcular_plan_merge(existentes={}, entrantes={"A": ("obj-a", FECHA_1)})

    assert len(plan) == 1
    assert plan[0].accion == Accion.INSERTAR
    assert plan[0].clave == "A"
    assert plan[0].entrante == "obj-a"


def test_misma_clave_misma_fecha_se_actualiza():
    existentes = {"A": ExistenteVersionado(id=10, fecha_aprobacion=FECHA_1)}
    entrantes = {"A": ("obj-a-nuevo", FECHA_1)}

    plan = calcular_plan_merge(existentes, entrantes)

    assert len(plan) == 1
    assert plan[0].accion == Accion.ACTUALIZAR
    assert plan[0].id_existente == 10
    assert plan[0].entrante == "obj-a-nuevo"


def test_misma_clave_fecha_mas_reciente_se_reemplaza():
    existentes = {"A": ExistenteVersionado(id=10, fecha_aprobacion=FECHA_1)}
    entrantes = {"A": ("obj-a-v2", FECHA_2)}

    plan = calcular_plan_merge(existentes, entrantes)

    assert len(plan) == 1
    assert plan[0].accion == Accion.REEMPLAZAR
    assert plan[0].id_existente == 10


def test_misma_clave_fecha_mas_antigua_se_omite_sin_tocar_la_fila():
    existentes = {"A": ExistenteVersionado(id=10, fecha_aprobacion=FECHA_2)}
    entrantes = {"A": ("obj-a-viejo", FECHA_1)}

    plan = calcular_plan_merge(existentes, entrantes)

    assert len(plan) == 1
    assert plan[0].accion == Accion.OMITIR_FECHA_RETROCEDE
    assert plan[0].id_existente == 10


def test_clave_ausente_en_entrantes_se_elimina():
    existentes = {"A": ExistenteVersionado(id=10, fecha_aprobacion=FECHA_1)}

    plan = calcular_plan_merge(existentes, entrantes={})

    assert len(plan) == 1
    assert plan[0].accion == Accion.ELIMINAR
    assert plan[0].id_existente == 10
    assert plan[0].entrante is None


def test_sin_fecha_entrante_o_existente_se_actualiza_por_defecto():
    existentes = {"A": ExistenteVersionado(id=10, fecha_aprobacion=None)}
    entrantes = {"A": ("obj-a", FECHA_1)}

    plan = calcular_plan_merge(existentes, entrantes)

    assert plan[0].accion == Accion.ACTUALIZAR


def test_combina_varias_claves_en_un_solo_plan():
    existentes = {
        "insertar_no": ExistenteVersionado(id=1, fecha_aprobacion=FECHA_1),  # se elimina
        "actualizar": ExistenteVersionado(id=2, fecha_aprobacion=FECHA_1),
        "reemplazar": ExistenteVersionado(id=3, fecha_aprobacion=FECHA_1),
    }
    entrantes = {
        "nueva": ("obj-nueva", FECHA_1),
        "actualizar": ("obj-act", FECHA_1),
        "reemplazar": ("obj-reem", FECHA_2),
    }

    plan = calcular_plan_merge(existentes, entrantes)
    acciones_por_clave = {op.clave: op.accion for op in plan}

    assert acciones_por_clave == {
        "insertar_no": Accion.ELIMINAR,
        "actualizar": Accion.ACTUALIZAR,
        "reemplazar": Accion.REEMPLAZAR,
        "nueva": Accion.INSERTAR,
    }
