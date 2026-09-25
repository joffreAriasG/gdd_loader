from gdd_loader.cli import _listar_archivos


def test_listar_archivos_encuentra_los_xlsx_de_la_carpeta(tmp_path):
    (tmp_path / "DominioA.xlsx").write_bytes(b"")
    (tmp_path / "DominioB.xlsx").write_bytes(b"")
    (tmp_path / "notas.txt").write_bytes(b"")

    archivos = _listar_archivos(tmp_path)

    assert [a.name for a in archivos] == ["DominioA.xlsx", "DominioB.xlsx"]


def test_listar_archivos_excluye_los_archivos_de_bloqueo_de_excel(tmp_path):
    (tmp_path / "DominioA.xlsx").write_bytes(b"")
    (tmp_path / "~$DominioA.xlsx").write_bytes(b"")

    archivos = _listar_archivos(tmp_path)

    assert [a.name for a in archivos] == ["DominioA.xlsx"]


def test_listar_archivos_devuelve_vacio_si_no_hay_xlsx(tmp_path):
    assert _listar_archivos(tmp_path) == []
