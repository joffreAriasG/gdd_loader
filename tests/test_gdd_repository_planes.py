"""
Regresion BL-04 / BL-05 (v0.14.1): sentencias que gdd_repository manda a
gdd.plan_remediacion al dar de baja o reemplazar un atributo. No tocan SQL
Server: la conexion es un MagicMock y se revisa el texto SQL y los
parametros de cada `conn.execute`.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from gdd_loader.load import gdd_repository as repo


def _sentencias(conn: MagicMock) -> list[tuple[str, dict]]:
    """(sql normalizado, parametros) de cada conn.execute, en orden."""
    salida = []
    for llamada in conn.execute.call_args_list:
        sql = re.sub(r"\s+", " ", str(llamada.args[0])).strip()
        parametros = llamada.args[1] if len(llamada.args) > 1 else {}
        salida.append((sql, parametros))
    return salida


def _tablas_actualizadas(conn: MagicMock) -> list[str]:
    return [re.match(r"UPDATE (\S+)", sql).group(1) for sql, _ in _sentencias(conn)]


def test_eliminar_atributo_da_de_baja_sus_planes_activos():
    """BL-05: la baja del atributo va en cascada a sus planes (antes solo a
    la fuente oficial y al consumo)."""
    conn = MagicMock()

    repo.eliminar_atributo(conn, 42)

    tablas = _tablas_actualizadas(conn)
    assert tablas == [
        "gdd.plan_remediacion",
        "gdd.atributo_fuente_oficial",
        "gdd.atributo_fuente_consumo",
        "gdd.atributo",
    ]
    sql, parametros = _sentencias(conn)[0]
    assert sql == (
        "UPDATE gdd.plan_remediacion SET activo = 0, fecha_baja = :hoy "
        "WHERE id_atributo = :id AND activo = 1"
    )
    assert parametros["id"] == 42


def test_eliminar_atributo_en_reemplazo_no_toca_los_planes():
    """REEMPLAZAR llama con baja_planes=False: los planes se mueven, no se
    dan de baja. El resto de la cascada sigue igual."""
    conn = MagicMock()

    repo.eliminar_atributo(conn, 42, baja_planes=False)

    assert _tablas_actualizadas(conn) == [
        "gdd.atributo_fuente_oficial",
        "gdd.atributo_fuente_consumo",
        "gdd.atributo",
    ]


def test_mover_planes_remediacion_religa_solo_los_activos():
    """BL-04: los planes activos pasan a la version nueva; los inactivos
    quedan como historial en la anterior. Devuelve cuantos movio."""
    conn = MagicMock()
    conn.execute.return_value.rowcount = 25

    movidos = repo.mover_planes_remediacion(conn, 100, 321)

    assert movidos == 25
    sentencias = _sentencias(conn)
    assert len(sentencias) == 1
    sql, parametros = sentencias[0]
    assert sql == (
        "UPDATE gdd.plan_remediacion SET id_atributo = :nuevo "
        "WHERE id_atributo = :anterior AND activo = 1"
    )
    assert parametros == {"nuevo": 321, "anterior": 100}
