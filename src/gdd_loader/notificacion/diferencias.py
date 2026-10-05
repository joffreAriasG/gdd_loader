"""
Comparacion de dos instantaneas (antes / despues del merge). Funcion pura.

- NUEVO      : la clave natural no existia antes y ahora si.
- MODIFICADO : la clave existia en ambas y cambio al menos un campo. Se lista
               cada campo con su valor anterior y nuevo.
- BAJA       : la clave existia antes y ya no esta activa (baja logica).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gdd_loader.notificacion.instantanea import ENTIDADES, Instantanea

NUEVO = "NUEVO"
MODIFICADO = "MODIFICADO"
BAJA = "BAJA"


@dataclass(frozen=True)
class CambioCampo:
    campo: str
    anterior: str
    nuevo: str


@dataclass
class Cambio:
    tipo: str               # NUEVO | MODIFICADO | BAJA
    etiqueta: str
    campos: list[CambioCampo] = field(default_factory=list)  # solo en MODIFICADO
    fila: dict[str, str] = field(default_factory=dict)       # NUEVO/BAJA: fila de la tabla


@dataclass
class CambiosEntidad:
    entidad: str
    columnas: list[str] = field(default_factory=list)        # encabezados de la tabla de nuevos/bajas
    nuevos: list[Cambio] = field(default_factory=list)
    modificados: list[Cambio] = field(default_factory=list)
    bajas: list[Cambio] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.nuevos) + len(self.modificados) + len(self.bajas)


def _orden(clave: tuple) -> tuple:
    # Codigos numericos ("2", "10") en orden numerico, el resto alfabetico.
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in clave)


def comparar(antes: Instantanea, despues: Instantanea) -> list[CambiosEntidad]:
    """Una entrada por entidad, en el orden de la minuta (incluye las que no
    tuvieron cambios, con listas vacias)."""
    resultado = []
    columnas_por_entidad = {e.nombre: list(e.columnas_tabla.values()) for e in ENTIDADES}
    nombres = [e.nombre for e in ENTIDADES] + [
        n for n in list(antes) + list(despues) if n not in {e.nombre for e in ENTIDADES}]
    for nombre in dict.fromkeys(nombres):
        a = antes.get(nombre, {})
        d = despues.get(nombre, {})
        cambios = CambiosEntidad(nombre, columnas=columnas_por_entidad.get(nombre, []))
        for clave in sorted(set(a) | set(d), key=_orden):
            if clave not in a:
                cambios.nuevos.append(Cambio(NUEVO, d[clave].etiqueta, fila=dict(d[clave].tabla)))
            elif clave not in d:
                cambios.bajas.append(Cambio(BAJA, a[clave].etiqueta, fila=dict(a[clave].tabla)))
            else:
                campos = [
                    CambioCampo(campo, a[clave].campos.get(campo, ""), valor)
                    for campo, valor in d[clave].campos.items()
                    if a[clave].campos.get(campo, "") != valor
                ]
                if campos:
                    cambios.modificados.append(Cambio(MODIFICADO, d[clave].etiqueta, campos))
        resultado.append(cambios)
    return resultado


def resumen(cambios: list[CambiosEntidad]) -> dict[str, dict[str, int]]:
    return {
        c.entidad: {"nuevos": len(c.nuevos), "modificados": len(c.modificados),
                    "dados_de_baja": len(c.bajas)}
        for c in cambios
    }
