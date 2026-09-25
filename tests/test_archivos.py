import os
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from gdd_loader.pipeline.archivos import archivo_estable, listar_pendientes, mover, nombre_archivado
from gdd_loader.pipeline.purga import purgar


def test_listar_pendientes_excluye_bloqueos_y_otros_tipos(tmp_path):
    for n in ("b.xlsx", "a.xlsx", "~$a.xlsx", "notas.txt"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "sub.xlsx").mkdir()
    assert [p.name for p in listar_pendientes(tmp_path)] == ["a.xlsx", "b.xlsx"]


def test_archivo_estable_segun_antiguedad_y_tamano(tmp_path):
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"x")
    ahora = time.time()
    os.utime(f, (ahora - 120, ahora - 120))
    assert archivo_estable(f, 60, ahora)
    assert not archivo_estable(f, 300, ahora)
    vacio = tmp_path / "v.xlsx"
    vacio.write_bytes(b"")
    os.utime(vacio, (ahora - 999, ahora - 999))
    assert not archivo_estable(vacio, 60, ahora)
    assert not archivo_estable(tmp_path / "no_existe.xlsx", 0, ahora)


def test_nombre_archivado():
    assert nombre_archivado("ADS", 42, "abcdef1234") == "ADS_000042_abcdef12.xlsx"
    assert nombre_archivado(None, 7, "ff00ff00ff") == "SIN_DOMINIO_000007_ff00ff00.xlsx"


def test_mover_crea_carpeta_y_no_sobrescribe(tmp_path):
    destino = tmp_path / "Archivo"
    a = tmp_path / "uno.xlsx"
    a.write_bytes(b"1")
    b = tmp_path / "dos.xlsx"
    b.write_bytes(b"2")
    r1 = mover(a, destino, "X.xlsx")
    r2 = mover(b, destino, "X.xlsx")
    assert r1.name == "X.xlsx" and r2.name == "X_1.xlsx"
    assert r1.read_bytes() == b"1" and r2.read_bytes() == b"2"
    assert not a.exists() and not b.exists()


def test_purgar_borra_solo_dentro_de_carpetas_permitidas(tmp_path):
    archivo_dir = tmp_path / "Archivo"
    archivo_dir.mkdir()
    viejo = archivo_dir / "ADS_000001_aa.xlsx"
    viejo.write_bytes(b"x")
    fuera = tmp_path / "otro.xlsx"
    fuera.write_bytes(b"x")
    repo = MagicMock()
    repo.archivos_a_purgar.return_value = [
        (1, str(viejo)), (2, str(fuera)), (3, str(archivo_dir / "ya_no_existe.xlsx")),
    ]
    ahora = datetime(2026, 9, 25)

    n = purgar(repo, 365, ahora, carpetas_permitidas=(archivo_dir,))

    assert n == 2
    assert not viejo.exists() and fuera.exists()
    repo.archivos_a_purgar.assert_called_once_with(ahora - timedelta(days=365))
    marcados = [c.args[0] for c in repo.actualizar_carga.call_args_list]
    assert marcados == [1, 3]


def test_purgar_desactivada_con_cero_dias():
    repo = MagicMock()
    assert purgar(repo, 0) == 0
    repo.archivos_a_purgar.assert_not_called()
