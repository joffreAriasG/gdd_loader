"""
Sitio de consulta por dominio (v0.10.0): un HTML autocontenido (sin internet ni
servidor) con menu lateral y tres secciones por dominio:

  1. Dominio y estructura      -> datos del dominio, responsables (hoja
                                  Estructura) y respaldos.
  2. Atributos y fuentes       -> atributos con sus fuentes oficiales y sus
                                  fuentes de consumo (MetadataTecnica /
                                  MetadataRepositorio).
  3. Investigacion             -> normas, procesos, proyectos, modelos y demas
                                  registros de la hoja Investigacion (sin los
                                  terminos).
  4. Glosario de terminos      -> registros de Investigacion con clasificacion
                                  "Término", de todos los dominios en una sola
                                  pagina (o del dominio elegido), con buscador e
                                  indice A-Z.
  5. Relaciones entre dominios -> grafo (vis-network incrustado) de los
                                  atributos Compartido / Similar: completo, o el
                                  dominio elegido con sus vecinos. Reutiliza el
                                  modelo de exportar/grafo_relaciones.py (v0.9.0),
                                  que lee la vista gdd.v_relacion_atributo. Si la
                                  vista no existe, la seccion lo informa y el resto
                                  del sitio funciona igual.
  6. Indicadores (v0.11.0)     -> puntaje de fuente oficial por dominio (ver
                                  PENALIZACION), atributos criticos / no criticos con
                                  y sin fuente oficial, estructura completa (roles
                                  ROLES_REQUERIDOS), fuentes de consumo, respaldos,
                                  glosario y vinculos. Graficos en HTML segun el tipo
                                  de variable, cada uno con su vista de tabla.

Solo lectura: se arma con lo que load/sitio_repository.leer_todo devuelve de
gdd.* (registros activos). construir_modelo es pura (testeable sin base).

El HTML contiene los VALORES de los registros (incluidos nombres de
responsables): se entrega solo al equipo de gobierno de datos, igual que la
minuta. No va al repositorio.
"""

from __future__ import annotations

import json
import re
import unicodedata
from html import escape

from gdd_loader.exportar.grafo_relaciones import ARCHIVO_VIS, RECURSOS
from gdd_loader.notificacion.instantanea import a_texto


def _textos(fila: dict) -> dict[str, str]:
    return {k: a_texto(v) for k, v in fila.items()}


def es_termino(clasificacion: str | None) -> bool:
    """Clasificacion de investigacion 'Término' (cat_clasificacion_investigacion), sin importar
    tildes ni mayusculas. Esos registros van a la seccion 'Glosario de términos'."""
    base = unicodedata.normalize("NFD", clasificacion or "")
    return "".join(c for c in base if unicodedata.category(c) != "Mn").strip().lower() == "termino"


# --- Indicadores (pestana "Indicadores", 2026-11-10) -------------------------------------------
# Puntaje de fuente oficial del dominio: parte de 100 y cada atributo activo SIN fuente oficial
# activa resta puntos (regla definida por el usuario el 2026-11-10):
#   critico y marcado en "Estructura" -> 5 | solo critico -> 3 | cualquier otro caso -> 1
# [SUPUESTO] "otro caso" incluye al no critico marcado en Estructura. Piso 0.
PENALIZACION = {"critico_estructura": 5, "critico": 3, "otro": 1}
# Estructura completa: el dominio tiene responsables activos con estos roles (id de cat_rol, regla del
# usuario). OJO: los id pueden diferir entre ambientes; el sitio muestra el nombre de cada rol.
ROLES_REQUERIDOS = (1, 2, 3, 4)
# Estados de v_relacion_atributo que cuentan como vinculo resuelto (MISMO_DOMINIO tambien tiene destino).
ESTADOS_RESUELTOS = ("RESUELTO", "DESTINO_INACTIVO", "MISMO_DOMINIO")


def _sin_tildes(texto: str | None) -> str:
    base = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in base if unicodedata.category(c) != "Mn").strip().lower()


def es_critico(criticidad: str | None) -> bool:
    """'Crítico' de cat_criticidad (sin importar tildes ni mayusculas); 'No crítico' no lo es."""
    return _sin_tildes(criticidad) == "critico"


def categoria_penalizacion(atributo: dict) -> str | None:
    """None si el atributo tiene al menos una fuente oficial activa; si no, la clave de PENALIZACION."""
    if atributo.get("fuentes_oficiales"):
        return None
    if es_critico(atributo.get("criticidad")):
        return "critico_estructura" if atributo.get("estructura") == "Sí" else "critico"
    return "otro"


def calcular_indicadores(dominio: dict, vinculos: dict | None) -> dict:
    """Indicadores de un dominio del modelo (atributos ya con sus fuentes). Pura, testeable.

    `vinculos`: {"resueltos": n, "pendientes": n} del dominio, o None si no hay modelo de relaciones.
    """
    atributos = dominio["atributos"]
    criticos = [a for a in atributos if es_critico(a.get("criticidad"))]
    no_criticos = [a for a in atributos if not es_critico(a.get("criticidad"))]
    detalle = {k: {"n": 0, "puntos": 0} for k in PENALIZACION}
    sin_fuente = []
    for a in atributos:
        cat = categoria_penalizacion(a)
        if cat is None:
            continue
        detalle[cat]["n"] += 1
        detalle[cat]["puntos"] += PENALIZACION[cat]
        sin_fuente.append({"codigo_atributo": a["codigo_atributo"], "nombre_atributo": a.get("nombre_atributo", ""),
                           "criticidad": a.get("criticidad", ""), "estructura": a.get("estructura", ""),
                           "penalizacion": PENALIZACION[cat]})
    sin_fuente.sort(key=lambda x: (-x["penalizacion"], _orden_natural(x["codigo_atributo"])))
    penalizacion = sum(x["puntos"] for x in detalle.values())
    con_fo = sum(1 for a in atributos if a["fuentes_oficiales"])

    roles = set()
    for r in dominio["estructura"]:
        try:
            roles.add(int(r.get("id_rol") or 0))
        except ValueError:
            pass
    faltantes = [r for r in ROLES_REQUERIDOS if r not in roles]

    return {
        # sin atributos no hay nada que medir: el puntaje queda en None (no 100)
        "puntaje": max(0, 100 - penalizacion) if atributos else None,
        "penalizacion": penalizacion,
        "detalle_penalizacion": detalle,
        "atributos": len(atributos),
        "criticos": len(criticos),
        "no_criticos": len(no_criticos),
        "criticos_con_fo": sum(1 for a in criticos if a["fuentes_oficiales"]),
        "no_criticos_con_fo": sum(1 for a in no_criticos if a["fuentes_oficiales"]),
        "con_fo": con_fo,
        "pct_con_fo": round(100 * con_fo / len(atributos), 1) if atributos else None,
        "fuentes_consumo": sum(len(a["fuentes_consumo"]) for a in atributos),
        "atributos_con_consumo": sum(1 for a in atributos if a["fuentes_consumo"]),
        "tiene_consumo": any(a["fuentes_consumo"] for a in atributos),
        "roles_faltantes": faltantes,
        "estructura_completa": not faltantes,
        "respaldos": len(dominio["respaldos"]),
        "terminos": sum(1 for r in dominio["investigacion"] if es_termino(r.get("clasificacion"))),
        "vinculos_resueltos": vinculos["resueltos"] if vinculos else None,
        "vinculos_pendientes": vinculos["pendientes"] if vinculos else None,
        "sin_fuente": sin_fuente,
    }


def vinculos_por_dominio(relaciones: dict | None) -> dict[str, dict]:
    """{codigo_dominio: {"resueltos": n, "pendientes": n}} desde el modelo de grafo_relaciones."""
    if not relaciones:
        return {}
    conteo: dict[str, dict] = {}
    filas = [x for a in relaciones.get("aristas", []) for x in a.get("atributos", [])] + relaciones.get("problemas", [])
    for x in filas:
        c = conteo.setdefault(x.get("dominio_origen", ""), {"resueltos": 0, "pendientes": 0})
        c["resueltos" if x.get("estado") in ESTADOS_RESUELTOS else "pendientes"] += 1
    return conteo


def _orden_natural(valor: str):
    """'10' despues de '9'; textos no numericos al final, en orden alfabetico."""
    return (0, int(valor), "") if re.fullmatch(r"\d+", valor or "") else (1, 0, (valor or "").lower())


def construir_modelo(datos: dict[str, list[dict]], relaciones: dict | None = None) -> dict:
    """{entidad: filas} (de sitio_repository.leer_todo) -> modelo del sitio.

    `relaciones`: modelo de grafo_relaciones.construir_modelo (seccion 4), o
    None si la vista gdd.v_relacion_atributo no esta disponible.

    Las filas cuyo codigo_dominio no esta en gdd.dominio_dato se cuentan como
    huerfanas en el resumen (no se muestran en ningun dominio).
    """
    dominios: dict[str, dict] = {}
    for fila in datos.get("dominios", []):
        t = _textos(fila)
        codigo = t.pop("codigo_dominio")
        if not codigo:
            continue
        dominios[codigo] = {
            "codigo": codigo,
            "nombre": t.pop("nombre_dominio", ""),
            "info": t,
            "estructura": [],
            "respaldos": [],
            "atributos": [],
            "investigacion": [],
        }

    huerfanos: dict[str, int] = {}

    def _destino(t: dict, entidad: str):
        d = dominios.get(t.get("codigo_dominio", ""))
        if d is None:
            huerfanos[entidad] = huerfanos.get(entidad, 0) + 1
        return d

    for entidad, lista in (("responsables", "estructura"), ("respaldos", "respaldos"),
                           ("investigacion", "investigacion")):
        for fila in datos.get(entidad, []):
            t = _textos(fila)
            d = _destino(t, entidad)
            if d is not None:
                t.pop("codigo_dominio", None)
                d[lista].append(t)

    atributos: dict[tuple[str, str], dict] = {}
    for fila in datos.get("atributos", []):
        t = _textos(fila)
        d = _destino(t, "atributos")
        if d is None:
            continue
        codigo = t.pop("codigo_dominio")
        t["fuentes_oficiales"] = []
        t["fuentes_consumo"] = []
        d["atributos"].append(t)
        atributos[(codigo, t["codigo_atributo"])] = t

    for entidad in ("fuentes_oficiales", "fuentes_consumo"):
        for fila in datos.get(entidad, []):
            t = _textos(fila)
            a = atributos.get((t.get("codigo_dominio", ""), t.get("codigo_atributo", "")))
            if a is None:
                huerfanos[entidad] = huerfanos.get(entidad, 0) + 1
                continue
            t.pop("codigo_dominio", None)
            t.pop("codigo_atributo", None)
            a[entidad].append(t)

    for d in dominios.values():
        d["atributos"].sort(key=lambda a: _orden_natural(a["codigo_atributo"]))
        for a in d["atributos"]:
            a["fuentes_oficiales"].sort(
                key=lambda f: (f.get("es_fuente_primaria") != "Sí", f.get("servidor", ""),
                               f.get("base_datos", ""), f.get("tabla", ""), f.get("nombre_campo", "")))
            a["fuentes_consumo"].sort(
                key=lambda f: (f.get("clase", ""), f.get("servidor", ""), f.get("base_datos", ""),
                               f.get("tabla", ""), f.get("nombre_campo", "")))
        d["estructura"].sort(key=lambda r: (r.get("rol", ""), r.get("area", ""), r.get("codigo_plaza", "")))
        d["respaldos"].sort(key=lambda r: (r.get("tipo_respaldo", ""), r.get("enlace_respaldo", "")))
        d["investigacion"].sort(key=lambda r: (r.get("clasificacion", ""), r.get("nombre", "").lower()))
        d["conteos"] = {
            "estructura": len(d["estructura"]),
            "respaldos": len(d["respaldos"]),
            "atributos": len(d["atributos"]),
            "fuentes_oficiales": sum(len(a["fuentes_oficiales"]) for a in d["atributos"]),
            "fuentes_consumo": sum(len(a["fuentes_consumo"]) for a in d["atributos"]),
            "investigacion": sum(1 for r in d["investigacion"] if not es_termino(r.get("clasificacion"))),
            "terminos": sum(1 for r in d["investigacion"] if es_termino(r.get("clasificacion"))),
        }

    vinculos = vinculos_por_dominio(relaciones)
    for d in dominios.values():
        d["indicadores"] = calcular_indicadores(
            d, vinculos.get(d["codigo"], {"resueltos": 0, "pendientes": 0}) if relaciones else None)

    lista = sorted(dominios.values(), key=lambda d: d["codigo"])
    totales = {k: sum(d["conteos"][k] for d in lista) for k in
               ("estructura", "respaldos", "atributos", "fuentes_oficiales", "fuentes_consumo", "investigacion",
                "terminos")}
    nombres_rol = {}
    for r in datos.get("roles", []):
        try:
            nombres_rol[int(r["id"])] = a_texto(r.get("nombre_rol"))
        except (TypeError, ValueError, KeyError):
            pass
    evaluados = [d["indicadores"] for d in lista if d["indicadores"]["puntaje"] is not None]
    return {
        "dominios": lista,
        "relaciones": relaciones,
        "resumen": {
            "n_dominios": len(lista),
            "n_dominios_con_atributos": sum(1 for d in lista if d["atributos"]),
            "totales": totales,
            "huerfanos": huerfanos,
            "indicadores": {
                "penalizacion": PENALIZACION,
                "roles_requeridos": [{"id": i, "nombre": nombres_rol.get(i, f"Rol {i}")} for i in ROLES_REQUERIDOS],
                "dominios_evaluados": len(evaluados),
                "puntaje_promedio": round(sum(i["puntaje"] for i in evaluados) / len(evaluados), 1) if evaluados else None,
                "atributos": sum(i["atributos"] for i in evaluados),
                "con_fo": sum(i["con_fo"] for i in evaluados),
                "criticos": sum(i["criticos"] for i in evaluados),
                "criticos_con_fo": sum(i["criticos_con_fo"] for i in evaluados),
                "dominios_estructura_completa": sum(1 for d in lista if d["indicadores"]["estructura_completa"]),
                "dominios_con_consumo": sum(1 for d in lista if d["indicadores"]["tiene_consumo"]),
                "dominios_con_respaldos": sum(1 for d in lista if d["indicadores"]["respaldos"]),
                "dominios_con_terminos": sum(1 for d in lista if d["indicadores"]["terminos"]),
            },
        },
    }


def _json_para_script(obj) -> str:
    # "</" dentro de un <script> cerraria la etiqueta: se escapa.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def generar_html(modelo: dict, generado_en: str, titulo: str = "Glosario de datos por dominio") -> str:
    # vis-network (~690 KB) solo se incrusta si hay modelo de relaciones.
    vis_js = (RECURSOS / ARCHIVO_VIS).read_text(encoding="utf-8") if modelo.get("relaciones") else ""
    return (
        _PLANTILLA
        .replace("__TITULO__", escape(titulo))
        .replace("__GENERADO__", escape(generado_en))
        .replace("__VIS_JS__", vis_js)
        .replace("__DATOS__", _json_para_script(modelo))  # al final: los datos no se reinterpretan
    )


_PLANTILLA = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__</title>
<style>
/* Paleta tomada del tablero de referencia (2026-11-10): amarillo de las barras de
   titulo, amarillo de las barras del grafico, azul marino y fondo gris claro. */
:root{
  --amarillo:#FDE338; --amarillo-fuerte:#FCCF33; --amarillo-suave:#FFF7CC;
  --marino:#14245C; --marino-2:#1E3270; --marino-3:#2A3F82; --marino-suave:#E8ECF6;
  --bg:#F6F8FB; --panel:#FFFFFF; --text:#1B2547; --muted:#6B7690; --line:#DDE3EC; --chip:#EEF1F6;
  --verde:#1E9E4A; --verde-suave:#E4F4EA; --rojo:#D93A3E; --rojo-suave:#FBE6E7;
  --side:var(--marino); --side-text:#E3E8F5; --side-muted:#9FAACB; --side-hover:var(--marino-2);
  --side-active:var(--marino-3); --banda-texto:var(--marino); --th-bg:var(--marino); --th-text:#FFFFFF;
  --acento:var(--marino);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0C1330; --panel:#131C40; --text:#E6EAF5; --muted:#A3ADC8; --line:#26325E; --chip:#1C2650;
    --marino-suave:#1C2650; --amarillo-suave:#3A3416; --verde:#5FD08A; --verde-suave:#14321F;
    --rojo:#F08386; --rojo-suave:#3A1A1C; --side:#0A1029; --side-hover:#16204A; --side-active:#1E2A5C;
    --th-bg:#1E2A5C; --acento:var(--amarillo);
  }
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font:14px/1.45 "Segoe UI",system-ui,-apple-system,Arial,sans-serif;background:var(--bg);color:var(--text);
  display:grid;grid-template-columns:290px 1fr;min-height:100vh}
a{color:var(--acento)}
aside{background:var(--side);color:var(--side-text);display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.marca{padding:18px 18px 12px;border-bottom:4px solid var(--amarillo)}
.marca h1{font-size:16px;margin:0 0 2px;color:#fff}
.marca small{color:var(--side-muted)}
nav.secciones{padding:10px 10px;border-bottom:1px solid rgba(255,255,255,.12)}
nav.secciones button{display:flex;gap:10px;align-items:center;width:100%;text-align:left;background:none;border:0;
  color:var(--side-text);padding:9px 10px;border-radius:7px;cursor:pointer;font:inherit;margin-bottom:2px}
nav.secciones button:hover{background:var(--side-hover)}
nav.secciones button.activo{background:var(--amarillo);color:var(--marino);font-weight:700}
nav.secciones .num{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;
  border:1.5px solid var(--amarillo);color:var(--amarillo);font-size:12px;font-weight:700;flex:none}
nav.secciones button.activo .num{background:var(--marino);border-color:var(--marino);color:var(--amarillo)}
.buscador{padding:12px 14px 6px}
.buscador input{width:100%;padding:8px 10px;border-radius:7px;border:1px solid transparent;background:#fff;color:var(--marino);font:inherit}
.buscador input:focus{outline:2px solid var(--amarillo);outline-offset:0}
.etq-lista{padding:6px 18px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--side-muted)}
ul.dominios{list-style:none;margin:0;padding:0 8px 16px;overflow:auto;flex:1}
ul.dominios li button{width:100%;text-align:left;background:none;border:0;border-left:3px solid transparent;color:var(--side-text);
  padding:7px 10px;border-radius:6px;cursor:pointer;font:inherit;display:flex;gap:8px;align-items:baseline}
ul.dominios li button:hover{background:var(--side-hover)}
ul.dominios li button.activo{background:var(--side-active);color:#fff;border-left-color:var(--amarillo)}
ul.dominios .cod{font-weight:700;min-width:46px;font-variant-numeric:tabular-nums}
ul.dominios li button.activo .cod{color:var(--amarillo)}
ul.dominios .nom{color:var(--side-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
ul.dominios li button.activo .nom{color:#D5DCEE}
main{padding:24px 28px 48px;min-width:0}
.cabecera{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap;margin-bottom:18px;
  padding-bottom:12px;border-bottom:3px solid var(--amarillo)}
.cabecera h2{margin:0;font-size:22px;color:var(--acento)}
.cabecera .sub{color:var(--muted);margin-top:4px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
  box-shadow:0 1px 2px rgba(20,36,92,.06);border-top:4px solid var(--amarillo-fuerte)}
.kpi b{display:block;font-size:24px;font-variant-numeric:tabular-nums;color:var(--acento)}
.kpi span{color:var(--muted);font-size:12px}
.tarjeta{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin-bottom:18px;overflow:hidden;
  box-shadow:0 1px 2px rgba(20,36,92,.06)}
.tarjeta>h3{margin:0;font-size:14px;display:flex;gap:8px;align-items:center;justify-content:center;
  background:var(--amarillo);color:var(--banda-texto);padding:6px 12px;font-weight:700}
.tarjeta>h3 .cuenta{background:var(--marino);color:#fff}
.tarjeta>.tc{padding:14px 18px 16px}
.cuenta{background:var(--chip);color:var(--muted);border-radius:999px;padding:1px 8px;font-size:12px;font-weight:600}
dl.ficha{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 24px;margin:0}
dl.ficha div{border-bottom:1px dashed var(--line);padding-bottom:6px}
dl.ficha dt{color:var(--muted);font-size:12px}
dl.ficha dd{margin:2px 0 0;word-break:break-word}
.tabla-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:var(--th-bg);color:var(--th-text);font-weight:600;position:sticky;top:0;white-space:nowrap}
tbody tr:nth-child(even) td{background:rgba(20,36,92,.025)}
tr:last-child td{border-bottom:0}
td.largo{min-width:260px;max-width:520px;white-space:pre-wrap;word-break:break-word}
.vacio{color:var(--muted);font-style:italic;padding:8px 0}
.herramientas{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.herramientas input,.herramientas select{padding:7px 10px;border:1px solid var(--line);border-radius:7px;
  background:var(--panel);color:var(--text);font:inherit;min-width:220px}
.herramientas input:focus{outline:2px solid var(--amarillo-fuerte);outline-offset:0}
.herramientas button{background:var(--marino);color:#fff;border:0;border-radius:7px;padding:8px 14px;font:inherit;
  font-weight:600;cursor:pointer}
.herramientas button:hover{background:var(--marino-3)}
.herramientas .nota{color:var(--muted);font-size:12px}
.herramientas button.btn-excel{background:var(--amarillo);color:var(--marino);border:1px solid var(--amarillo-fuerte)}
.herramientas button.btn-excel:hover{background:var(--amarillo-fuerte)}
.herramientas button:disabled{opacity:.5;cursor:not-allowed}
.atributo{border:1px solid var(--line);border-left:4px solid var(--line);border-radius:9px;margin-bottom:10px;background:var(--panel)}
.atributo:hover{border-left-color:var(--amarillo-fuerte)}
.atributo[open]{border-left-color:var(--amarillo-fuerte);box-shadow:0 2px 6px rgba(20,36,92,.08)}
.atributo>summary{list-style:none;cursor:pointer;padding:10px 14px;display:grid;
  grid-template-columns:70px 1fr auto;gap:12px;align-items:center}
.atributo>summary::-webkit-details-marker{display:none}
.atributo>summary .cod{font-weight:700;font-variant-numeric:tabular-nums;color:var(--acento)}
.atributo>summary .nom{font-weight:600}
.atributo>summary .desc{color:var(--muted);font-size:12px;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.chips{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.chip{background:var(--chip);border-radius:999px;padding:2px 8px;font-size:12px;white-space:nowrap}
.chip.of{background:var(--marino);color:#fff}
.chip.co{background:var(--amarillo-fuerte);color:var(--marino);font-weight:600}
.chip.pers{background:var(--rojo-suave);color:var(--rojo);font-weight:600}
.atributo[open]>summary{border-bottom:1px solid var(--line);background:var(--amarillo-suave)}
.atributo .cuerpo{padding:12px 14px 14px}
/* --- indicadores: colores de serie validados con validate_palette (2026-11-10). Son tonos del azul
   marino y del amarillo de la marca ajustados a la banda de luminosidad de graficos: el #14245C y el
   #FCCF33 puros fallan esa banda. Claro: OK, el amarillo pide alivio de contraste (etiquetas + tabla).
   Oscuro: todo OK. Etiqueta dentro del segmento: blanco/marino en claro, casi negro en oscuro. --- */
:root{--serie-1:#2B4A9E;--serie-2:#D9A300;--grid:#E6EAF1;--pista-medidor:#DCE3F3}
@media (prefers-color-scheme: dark){
  :root{--serie-1:#6F89DB;--serie-2:#BD8B00;--grid:#26325E;--pista-medidor:#26325E}
  .seg-etq,.seg-etq.sobre-claro{color:#0B1230}
  body .btn-detalle{color:var(--amarillo)}
  body .btn-detalle:hover,body .btn-detalle[aria-expanded="true"]{color:var(--marino)}
}
.fila-hero{display:grid;grid-template-columns:minmax(260px,1fr) 2fr;gap:16px;align-items:stretch;margin-bottom:18px}
.fila-hero .tarjeta{margin-bottom:0}
.fila-hero .kpis{margin-bottom:0;align-content:stretch}
.hero{display:flex;flex-direction:column;gap:6px}
.hero-num{font-size:56px;font-weight:700;line-height:1;color:var(--acento)}
.hero-sub{color:var(--muted);font-size:12.5px}
.viz-acciones{display:flex;justify-content:flex-end;margin:-4px 0 8px}
.barras{display:flex;flex-direction:column;gap:6px}
.fila-barra{display:grid;grid-template-columns:minmax(120px,190px) 1fr;gap:12px;align-items:center;border-radius:6px;padding:2px 4px}
.fila-barra.clic{cursor:pointer}
.fila-barra.clic:hover{background:var(--amarillo-suave)}
.etq-barra{display:flex;flex-direction:column;min-width:0;line-height:1.25}
.etq-barra b{color:var(--acento);font-size:13px}
.etq-barra span{color:var(--muted);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pista{position:relative;display:flex;align-items:center;gap:6px;min-height:22px;padding-right:48px;
  background-image:linear-gradient(var(--grid),var(--grid));background-size:1px 100%;background-repeat:no-repeat}
.barra{height:20px;background:var(--serie-1);border-radius:0 4px 4px 0;min-width:2px}
.barra:hover,.barra:focus,.seg:hover,.seg:focus{filter:brightness(1.15);outline:none}
.valor-barra{font-size:12.5px;font-weight:600;color:var(--text);white-space:nowrap;font-variant-numeric:tabular-nums}
.valor-barra.sin-valor{font-weight:400;color:var(--muted);font-style:italic}
.ticks{min-height:16px;background:none;padding-right:0;margin-right:48px}
.ticks span{position:absolute;transform:translateX(-50%);font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.apilada{display:flex;gap:2px;height:20px;flex:none;min-width:2px}
.seg{position:relative;height:20px;display:flex;align-items:center;min-width:2px;overflow:visible}
.seg:last-child{border-radius:0 4px 4px 0}
.seg-etq{padding-left:6px;font-size:11.5px;font-weight:700;color:#fff;white-space:nowrap;visibility:hidden}
.seg-etq.sobre-claro{color:#14245C}
.leyenda{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:10px;font-size:12.5px;color:var(--text)}
.leyenda > span{display:inline-flex;align-items:center;gap:6px}
.sw-rect{display:inline-block;width:12px;height:12px;border-radius:3px}
.titulo-multiplo{margin:0 0 8px;font-size:13px;color:var(--acento)}
.medidores{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px 20px;margin-bottom:16px}
.medidor{outline:none}
.medidor-cab{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;margin-bottom:5px}
.medidor-cab b{color:var(--acento)}
.medidor-pista{height:8px;border-radius:999px;background:var(--pista-medidor);overflow:hidden}
.medidor-pista.grande{height:10px;margin-top:6px}
.medidor-relleno{height:100%;background:var(--serie-1);border-radius:999px}
.estado{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:1px 9px 1px 6px;font-size:12px;font-weight:600;white-space:nowrap}
.estado .icono{font-weight:800}
.estado.ok{background:var(--verde-suave);color:var(--verde)}
.estado.no{background:var(--rojo-suave);color:var(--rojo)}
.matriz td{vertical-align:middle}
.lista-cumple{display:grid;gap:8px;margin-bottom:12px}
.item-cumple{display:grid;grid-template-columns:auto 170px 1fr;gap:10px;align-items:center}
#tip-viz{position:fixed;z-index:50;pointer-events:none;background:var(--panel);color:var(--text);border:1px solid var(--line);
  border-radius:8px;box-shadow:0 4px 14px rgba(20,36,92,.18);padding:7px 10px;font-size:12px;max-width:320px}
#tip-viz .tip-fila{display:flex;gap:6px;align-items:center}
#tip-viz .tip-fila b{font-size:13px}
#tip-viz .tip-fila span{color:var(--muted)}
#tip-viz .tip-clave{display:inline-block;width:12px;height:3px;border-radius:2px}
@media (max-width: 860px){ .fila-hero{grid-template-columns:1fr} .item-cumple{grid-template-columns:auto 1fr} .item-cumple .nota{grid-column:1/-1} }
.indice-letras{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:16px}
.indice-letras button{min-width:32px;padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);
  color:var(--acento);font:inherit;font-weight:700;cursor:pointer}
.indice-letras button:hover{background:var(--amarillo-suave)}
.indice-letras button.activa{background:var(--amarillo);border-color:var(--amarillo-fuerte);color:var(--marino)}
.letra{margin-bottom:22px}
.letra-cab{display:flex;align-items:center;gap:10px;margin-bottom:10px;padding-bottom:6px;border-bottom:2px solid var(--amarillo)}
.letra-marca{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:8px;background:var(--marino);
  color:var(--amarillo);font-weight:800;font-size:17px}
.terminos{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.termino{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--amarillo-fuerte);border-radius:9px;
  padding:12px 14px;box-shadow:0 1px 2px rgba(20,36,92,.06)}
.termino-cab{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:6px}
.termino h4{margin:0;font-size:15px;color:var(--acento)}
.termino p{margin:0;white-space:pre-wrap;word-break:break-word;line-height:1.55}
.termino-ref{margin-top:8px;color:var(--muted);font-size:12.5px;word-break:break-word}
.chip-dominio{flex:none;background:var(--marino);color:#fff;border:0;border-radius:999px;padding:2px 9px;font:inherit;
  font-size:12px;font-weight:700;cursor:pointer}
.chip-dominio:hover{background:var(--marino-3)}
.grafo{height:540px;border:1px solid var(--line);border-radius:8px;background:var(--panel);margin-bottom:10px}
.controles-red{gap:16px}
.controles-red input[type=checkbox]{min-width:0;width:auto;padding:0;margin:0;accent-color:var(--marino)}
label.opcion{display:inline-flex;gap:6px;align-items:center;cursor:pointer;font-size:13px}
.sw{display:inline-block;width:20px;height:4px;border-radius:2px}
.nota-red{color:var(--muted);font-size:12px;margin:4px 0 8px}
.avisos.aviso-red{background:var(--amarillo-suave);color:var(--text);border-left-color:var(--amarillo-fuerte)}
.detalle-arista:not(:empty){margin-top:12px;padding-top:10px;border-top:1px dashed var(--line)}
.detalle-arista h4{margin:0 0 4px;font-size:14px;color:var(--acento);display:flex;gap:8px;align-items:center}
tr.clic{cursor:pointer}
tr.clic:hover td{background:var(--amarillo-suave)}
.dos-columnas{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 18px}
@media (max-width: 1100px){ .dos-columnas{grid-template-columns:1fr} }
div.vis-tooltip{font:12px/1.4 "Segoe UI",Arial,sans-serif;white-space:pre-line;background:#fff;color:#14245C;
  border:1px solid #FCCF33;border-radius:6px;padding:6px 8px;box-shadow:0 2px 8px rgba(20,36,92,.2)}
.btn-detalle{background:var(--amarillo-suave);border:1px solid var(--amarillo-fuerte);color:var(--marino);border-radius:6px;
  padding:3px 9px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}
.btn-detalle:hover,.btn-detalle[aria-expanded="true"]{background:var(--amarillo-fuerte)}
.btn-todas{margin-left:auto}
.celda-formula{min-width:210px;max-width:300px}
.celda-formula .previa{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
  color:var(--muted);font-size:12px;margin-bottom:5px;white-space:normal}
.tabla-fuentes tbody tr:nth-child(even) td{background:none}
.tabla-fuentes tr.fila-abierta td{background:var(--amarillo-suave);border-bottom-color:transparent}
.tabla-fuentes tr.detalle td{background:var(--amarillo-suave);border-left:4px solid var(--amarillo-fuerte);padding:4px 14px 14px}
.det-fuente{position:sticky;left:14px;display:grid;gap:12px;max-width:100%}
.det-fuente h6{margin:0 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--acento)}
.det-fuente p{margin:0;white-space:pre-wrap;word-break:break-word;line-height:1.6;font-size:13.5px;color:var(--text)}
.bloques{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:4px}
.bloque{border:1px solid var(--line);border-radius:8px;padding:10px 14px 12px;background:var(--panel)}
.bloque.ancho{grid-column:1/-1;border-top:3px solid var(--amarillo-fuerte)}
.bloque h5{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--acento)}
.bloque .descripcion{margin:0 0 12px;white-space:pre-wrap;word-break:break-word;font-size:14px;line-height:1.55}
.bloque dl.ficha{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
@media (max-width: 1100px){ .bloques{grid-template-columns:1fr} }
.atributo h4{margin:16px 0 8px;font-size:13px;color:var(--acento);display:flex;gap:8px;align-items:center;
  padding-left:8px;border-left:4px solid var(--amarillo-fuerte)}
.grupo{margin-bottom:22px}
.grupo h3{font-size:14px;margin:0 0 8px;display:flex;gap:8px;align-items:center;color:var(--acento);
  padding-left:8px;border-left:4px solid var(--amarillo-fuerte)}
.personas{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.persona{display:flex;gap:12px;align-items:flex-start;background:var(--panel);border:1px solid var(--line);
  border-top:3px solid var(--amarillo-fuerte);border-radius:10px;padding:12px 14px;box-shadow:0 1px 2px rgba(20,36,92,.06)}
.avatar{flex:none;width:48px;height:48px;border-radius:50%;background:var(--marino);color:var(--amarillo);
  display:grid;place-items:center;box-shadow:0 0 0 3px var(--amarillo-suave)}
.avatar.vacante{background:var(--chip);color:var(--muted)}
.ico-avatar{width:28px;height:28px;fill:currentColor}
.persona .datos{min-width:0}
.persona .rol{display:inline-block;background:var(--amarillo);color:var(--marino);font-size:11px;font-weight:700;
  border-radius:999px;padding:1px 9px;margin-bottom:5px;letter-spacing:.02em}
.persona .nombre{font-weight:700;color:var(--acento);margin-bottom:4px;word-break:break-word}
.persona .nombre.sin-nombre{color:var(--muted);font-style:italic;font-weight:500}
.persona .linea{display:flex;gap:6px;align-items:flex-start;color:var(--muted);font-size:12.5px;margin-top:2px}
.persona .linea span{word-break:break-word}
.ico{flex:none;width:15px;height:15px;margin-top:1px;fill:none;stroke:var(--acento);stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}
.avisos{background:var(--rojo-suave);color:var(--rojo);border-left:4px solid var(--rojo);border-radius:8px;
  padding:10px 12px;margin-bottom:16px;font-size:13px}
.portada table td:first-child{font-weight:700;color:var(--acento)}
.portada tr{cursor:pointer}
.portada tbody tr:hover td{background:var(--amarillo-suave)}
@media (max-width: 860px){
  body{grid-template-columns:1fr}
  aside{position:relative;height:auto;max-height:none}
  ul.dominios{max-height:220px}
  main{padding:16px}
  .atributo>summary{grid-template-columns:52px 1fr}
  .chips{grid-column:1/-1;justify-content:flex-start}
}
@media print{
  body{display:block}
  aside,.herramientas{display:none}
  .atributo{break-inside:avoid}
  .tarjeta>h3,th{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
</style>
</head>
<body>
<aside>
  <div class="marca">
    <h1>__TITULO__</h1>
    <small>Generado: __GENERADO__ · solo lectura</small>
  </div>
  <nav class="secciones" id="secciones"></nav>
  <div class="buscador"><input id="filtroDominio" type="search" placeholder="Buscar dominio (código o nombre)"></div>
  <div class="etq-lista">Dominios</div>
  <ul class="dominios" id="listaDominios"></ul>
</aside>
<main id="contenido"></main>

<script>__VIS_JS__</script>
<script id="datos" type="application/json">__DATOS__</script>
<script>
(function(){
  "use strict";
  const DATOS = JSON.parse(document.getElementById("datos").textContent);
  const SECCIONES = [
    {id:"estructura", titulo:"Dominio y estructura"},
    {id:"atributos", titulo:"Atributos y fuentes"},
    {id:"investigacion", titulo:"Investigación"},
    {id:"terminos", titulo:"Glosario de términos"},
    {id:"relaciones", titulo:"Relaciones entre dominios"},
    {id:"indicadores", titulo:"Indicadores"},
  ];
  const porCodigo = new Map(DATOS.dominios.map(d => [d.codigo, d]));
  const estado = {seccion:"estructura", dominio:null, filtroDominio:"", filtroAtributo:"",
                  tiposRel:null, aislados:false, estadoRel:"", filtroTermino:"", letraTermino:""};
  // "Término" sin importar tildes ni mayusculas (igual que es_termino en Python)
  const sinTildes = s => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "");
  const esTermino = r => sinTildes(r.clasificacion).trim().toLowerCase() === "termino";

  // --- utilidades: el DOM se arma con textContent (nunca innerHTML con datos)
  function el(tag, props, hijos){
    const n = document.createElement(tag);
    if (props) for (const [k,v] of Object.entries(props)){
      if (v === undefined || v === null) continue;
      if (k === "texto") n.textContent = v;
      else if (k === "clase") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const h of [].concat(hijos || [])) if (h !== null && h !== undefined && h !== false)
      n.append(h instanceof Node ? h : document.createTextNode(String(h)));
    return n;
  }
  const v = x => (x === undefined || x === null || x === "") ? "—" : x;
  function cuenta(n){ return el("span", {clase:"cuenta", texto:String(n)}); }

  function ficha(campos, fila){
    return el("dl", {clase:"ficha"}, campos.map(([k, etq]) =>
      el("div", null, [el("dt", {texto:etq}), el("dd", {texto:v(fila[k])})])));
  }

  function tabla(columnas, filas, vacio){
    if (!filas.length) return el("div", {clase:"vacio", texto:vacio || "Sin registros."});
    const thead = el("thead", null, el("tr", null, columnas.map(c => el("th", {texto:c[1]}))));
    const tbody = el("tbody", null, filas.map(f => el("tr", null, columnas.map(c => {
      const valor = v(f[c[0]]);
      if (c[2] === "enlace" && /^https?:\/\//i.test(f[c[0]] || ""))
        return el("td", {clase:"largo"}, el("a", {href:f[c[0]], target:"_blank", rel:"noopener noreferrer", texto:valor}));
      return el("td", {clase: c[2] === "largo" || c[2] === "enlace" ? "largo" : null, texto:valor});
    }))));
    return el("div", {clase:"tabla-wrap"}, el("table", null, [thead, tbody]));
  }

  function tarjeta(titulo, n, contenido){
    return el("section", {clase:"tarjeta"}, [el("h3", null, [titulo, n === null ? null : cuenta(n)]),
      el("div", {clase:"tc"}, contenido)]);
  }

  // --- menu lateral
  function pintarSecciones(){
    const nav = document.getElementById("secciones");
    nav.replaceChildren(...SECCIONES.map((s, i) => el("button", {
      clase: s.id === estado.seccion ? "activo" : "",
      onclick: () => { estado.seccion = s.id; actualizar(); }
    }, [el("span", {clase:"num", texto:String(i+1)}), s.titulo])));
  }

  function pintarListaDominios(){
    const f = estado.filtroDominio.toLowerCase();
    const ul = document.getElementById("listaDominios");
    const items = [el("li", null, el("button", {
      clase: estado.dominio === null ? "activo" : "",
      onclick: () => { estado.dominio = null; estado.letraTermino = ""; estado.filtroTermino = ""; actualizar(); }
    }, [el("span", {clase:"cod", texto:"Todos"}), el("span", {clase:"nom", texto:"Resumen de dominios"})]))];
    for (const d of DATOS.dominios){
      if (f && !(d.codigo.toLowerCase().includes(f) || (d.nombre || "").toLowerCase().includes(f))) continue;
      items.push(el("li", null, el("button", {
        clase: d.codigo === estado.dominio ? "activo" : "", title: d.nombre,
        onclick: () => { estado.dominio = d.codigo; estado.filtroAtributo = ""; estado.letraTermino = "";
                         estado.filtroTermino = ""; actualizar(); }
      }, [el("span", {clase:"cod", texto:d.codigo}), el("span", {clase:"nom", texto:d.nombre})])));
    }
    ul.replaceChildren(...items);
  }

  // --- portada (todos los dominios)
  function vistaResumen(){
    const r = DATOS.resumen, t = r.totales;
    const sec = SECCIONES.find(s => s.id === estado.seccion);
    const kpis = el("div", {clase:"kpis"}, [
      ["Dominios", r.n_dominios], ["Con atributos", r.n_dominios_con_atributos], ["Atributos", t.atributos],
      ["Fuentes oficiales", t.fuentes_oficiales], ["Fuentes de consumo", t.fuentes_consumo],
      ["Responsables", t.estructura], ["Investigación", t.investigacion], ["Términos", t.terminos],
    ].map(([e, n]) => el("div", {clase:"kpi"}, [el("b", {texto:String(n)}), el("span", {texto:e})])));
    const huerf = Object.entries(r.huerfanos || {});
    const avisos = huerf.length ? el("div", {clase:"avisos", texto:
      "Registros activos sin dominio válido en gdd.dominio_dato (no se muestran): " +
      huerf.map(([k, n]) => k + " " + n).join(", ")}) : null;
    const filas = DATOS.dominios.map(d => el("tr", {onclick: () => { estado.dominio = d.codigo; actualizar(); }}, [
      el("td", {texto:d.codigo}), el("td", {texto:d.nombre}), el("td", {texto:v(d.info.tipo_dominio)}),
      el("td", {texto:String(d.conteos.estructura)}), el("td", {texto:String(d.conteos.atributos)}),
      el("td", {texto:String(d.conteos.fuentes_oficiales)}), el("td", {texto:String(d.conteos.fuentes_consumo)}),
      el("td", {texto:String(d.conteos.investigacion)}), el("td", {texto:String(d.conteos.terminos)}),
    ]));
    const tablaDom = el("div", {clase:"tabla-wrap portada"}, el("table", null, [
      el("thead", null, el("tr", null, ["Código","Dominio","Tipo","Responsables","Atributos","F. oficiales","F. consumo","Investigación","Términos"]
        .map(c => el("th", {texto:c})))),
      el("tbody", null, filas)]));
    return [
      el("div", {clase:"cabecera"}, [el("div", null, [el("h2", {texto:"Resumen de dominios"}),
        el("div", {clase:"sub", texto:"Elige un dominio en el menú lateral (o en la tabla) para ver la sección «" + sec.titulo + "»."})])]),
      avisos, kpis, tarjeta("Dominios", DATOS.dominios.length, tablaDom),
    ];
  }

  function cabeceraDominio(d){
    const sec = SECCIONES.find(s => s.id === estado.seccion);
    return el("div", {clase:"cabecera"}, [
      el("div", null, [el("h2", {texto: d.codigo + " · " + d.nombre}), el("div", {clase:"sub", texto: sec.titulo})]),
    ]);
  }

  // --- iconos (SVG en linea, sin recursos externos)
  const ICONOS = {
    persona: [["circle", {cx:"12", cy:"8", r:"4.2"}], ["path", {d:"M3.8 21c0-4.3 3.7-7 8.2-7s8.2 2.7 8.2 7z"}]],
    plaza:   [["rect", {x:"3", y:"7", width:"18", height:"13", rx:"2"}], ["path", {d:"M9 7V5.2C9 4.5 9.5 4 10.2 4h3.6c.7 0 1.2.5 1.2 1.2V7M3 12.5h18"}]],
    area:    [["rect", {x:"5", y:"3", width:"14", height:"18", rx:"1.5"}], ["path", {d:"M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2M10.5 21v-3h3v3"}]],
    fecha:   [["rect", {x:"3.5", y:"5", width:"17", height:"15.5", rx:"2"}], ["path", {d:"M3.5 10h17M8 3v4M16 3v4"}]],
  };
  function icono(nombre, clase){
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("class", clase);
    for (const [tag, attrs] of ICONOS[nombre]){
      const n = document.createElementNS(ns, tag);
      for (const [k, val] of Object.entries(attrs)) n.setAttribute(k, val);
      s.append(n);
    }
    return s;
  }

  // --- responsables como tarjetas de persona, agrupadas por rol
  function tarjetaPersona(r){
    const linea = (ico, etq, valor) => valor
      ? el("div", {clase:"linea", title:etq}, [icono(ico, "ico"), el("span", {texto:valor})]) : null;
    const plaza = [r.nombre_plaza, r.codigo_plaza ? "Plaza " + r.codigo_plaza : ""].filter(Boolean).join(" · ");
    return el("div", {clase:"persona"}, [
      el("div", {clase: "avatar" + (r.nombre_responsable ? "" : " vacante")}, icono("persona", "ico-avatar")),
      el("div", {clase:"datos"}, [
        el("div", {clase:"rol", texto: r.rol || "Sin rol"}),
        el("div", {clase: "nombre" + (r.nombre_responsable ? "" : " sin-nombre"),
                   texto: r.nombre_responsable || "Sin nombre registrado"}),
        linea("plaza", "Plaza", plaza),
        linea("area", "Área", r.area),
        linea("fecha", "Fecha aprobada", r.fecha_aprobada ? "Aprobado: " + r.fecha_aprobada : ""),
      ]),
    ]);
  }
  function vistaPersonas(filas){
    if (!filas.length) return el("div", {clase:"vacio", texto:"Sin responsables activos."});
    // filas ya vienen ordenadas por rol, area y plaza (construir_modelo)
    return el("div", {clase:"personas"}, filas.map(tarjetaPersona));
  }

  // --- seccion 1
  function vistaEstructura(d){
    const i = d.info;
    return [
      cabeceraDominio(d),
      tarjeta("Información del dominio", null, ficha([
        ["tipo_dominio","Tipo de dominio"], ["frontera","Frontera"],
      ], i)),
      el("div", {clase:"kpis"}, [["Responsables", d.conteos.estructura], ["Atributos", d.conteos.atributos],
        ["Fuentes oficiales", d.conteos.fuentes_oficiales], ["Fuentes de consumo", d.conteos.fuentes_consumo],
        ["Investigación", d.conteos.investigacion], ["Términos", d.conteos.terminos]]
        .map(([e, n]) => el("div", {clase:"kpi"}, [el("b", {texto:String(n)}), el("span", {texto:e})]))),
      tarjeta("Estructura (responsables)", d.estructura.length, vistaPersonas(d.estructura)),
      tarjeta("Respaldos", d.respaldos.length, tabla([
        ["tipo_respaldo","Tipo"], ["enlace_respaldo","Enlace","enlace"], ["observaciones","Observaciones","largo"],
        ["fecha_aprobada","Fecha aprobada"]], d.respaldos, "Sin respaldos activos.")),
    ];
  }

  // --- fuentes oficiales: tabla compacta + fila desplegable con la formula
  //     (formula_calculo y lista_valores_validos son textos largos: varchar(8000) / varchar(max))
  const tieneDetalle = f => !!(f.formula_calculo || f.lista_valores_validos);
  function previa(texto, n){ return texto.length > n ? texto.slice(0, n).trimEnd() + "…" : texto; }
  function alternarDetalle(boton, abrir){
    const fila = boton.closest("tr"), det = fila.nextElementSibling;
    if (abrir === undefined) abrir = det.hidden;
    det.hidden = !abrir;
    fila.classList.toggle("fila-abierta", abrir);
    boton.setAttribute("aria-expanded", String(abrir));
    boton.textContent = abrir ? "Ocultar" : boton.dataset.etiqueta;
    if (abrir){
      // el texto se ajusta al ancho VISIBLE de la tabla (que puede ser mas ancha y desplazarse)
      const wrap = boton.closest(".tabla-wrap");
      det.querySelector(".det-fuente").style.width = Math.max(260, wrap.clientWidth - 34) + "px";
    }
  }
  function tablaFuentesOficiales(filas){
    if (!filas.length) return el("div", {clase:"vacio", texto:"Sin fuentes oficiales activas."});
    const cols = [["servidor","Servidor"], ["base_datos","Base de datos"], ["tabla","Tabla / archivo"],
      ["nombre_campo","Campo"], [null,"Fórmula de cálculo"], ["es_fuente_primaria","Primaria"], ["tipo_campo","Tipo"],
      ["longitud_campo","Longitud"], ["acepta_valores_nulos","Acepta nulos"], ["fecha_aprobacion","Fecha de aprobación"],
      ["version","Versión"]];
    const filasHtml = [];
    for (const f of filas){
      const celdas = cols.map(([k]) => {
        if (k !== null) return el("td", {texto: v(f[k])});
        if (!tieneDetalle(f)) return el("td", {clase:"celda-formula", texto:"—"});
        const etiqueta = f.formula_calculo && f.lista_valores_validos ? "Ver fórmula y valores"
          : f.formula_calculo ? "Ver fórmula" : "Ver valores válidos";
        return el("td", {clase:"celda-formula"}, [
          f.formula_calculo ? el("div", {clase:"previa", texto: previa(f.formula_calculo, 90)}) : null,
          el("button", {type:"button", clase:"btn-detalle btn-fila", "aria-expanded":"false", "data-etiqueta":etiqueta,
            texto:etiqueta, onclick: e => alternarDetalle(e.currentTarget)}),
        ]);
      });
      filasHtml.push(el("tr", null, celdas));
      if (tieneDetalle(f)) filasHtml.push(el("tr", {clase:"detalle", hidden:""}, el("td", {colspan:String(cols.length)},
        el("div", {clase:"det-fuente"}, [
          f.formula_calculo ? el("section", null, [el("h6", {texto:"Fórmula de cálculo"}), el("p", {texto:f.formula_calculo})]) : null,
          f.lista_valores_validos ? el("section", null, [el("h6", {texto:"Valores válidos"}), el("p", {texto:f.lista_valores_validos})]) : null,
        ]))));
    }
    return el("div", {clase:"tabla-wrap"}, el("table", {clase:"tabla-fuentes"}, [
      el("thead", null, el("tr", null, cols.map(c => el("th", {texto:c[1]})))),
      el("tbody", null, filasHtml),
    ]));
  }

  // --- descarga a Excel (.xlsx real, generado en el navegador sin librerias ni internet) ----------------
  //     Un .xlsx es un ZIP de archivos XML: se arma con celdas de texto en linea (inlineStr) y se
  //     empaqueta sin compresion (metodo STORE), que Excel abre sin advertencias.
  const XLSX_MAX_CELDA = 32767;   // limite de caracteres por celda en Excel
  const TABLA_CRC = (() => { const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++){ let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t; })();
  function crc32(bytes){ let c = 0xFFFFFFFF; for (let i = 0; i < bytes.length; i++) c = TABLA_CRC[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
  function zipStore(archivos){
    const enc = new TextEncoder(), ahora = new Date();
    const hora = (ahora.getHours() << 11) | (ahora.getMinutes() << 5) | (ahora.getSeconds() >> 1);
    const fecha = ((ahora.getFullYear() - 1980) << 9) | ((ahora.getMonth() + 1) << 5) | ahora.getDate();
    const partes = [], central = []; let offset = 0;
    for (const a of archivos){
      const nombre = enc.encode(a.nombre), datos = enc.encode(a.texto), crc = crc32(datos);
      const loc = new DataView(new ArrayBuffer(30));
      loc.setUint32(0, 0x04034b50, true); loc.setUint16(4, 20, true); loc.setUint16(6, 0x0800, true); loc.setUint16(8, 0, true);
      loc.setUint16(10, hora, true); loc.setUint16(12, fecha, true); loc.setUint32(14, crc, true);
      loc.setUint32(18, datos.length, true); loc.setUint32(22, datos.length, true); loc.setUint16(26, nombre.length, true); loc.setUint16(28, 0, true);
      const cen = new DataView(new ArrayBuffer(46));
      cen.setUint32(0, 0x02014b50, true); cen.setUint16(4, 20, true); cen.setUint16(6, 20, true); cen.setUint16(8, 0x0800, true);
      cen.setUint16(10, 0, true); cen.setUint16(12, hora, true); cen.setUint16(14, fecha, true); cen.setUint32(16, crc, true);
      cen.setUint32(20, datos.length, true); cen.setUint32(24, datos.length, true); cen.setUint16(28, nombre.length, true);
      cen.setUint16(30, 0, true); cen.setUint16(32, 0, true); cen.setUint16(34, 0, true); cen.setUint16(36, 0, true);
      cen.setUint32(38, 0, true); cen.setUint32(42, offset, true);
      partes.push(new Uint8Array(loc.buffer), nombre, datos);
      central.push(new Uint8Array(cen.buffer), nombre);
      offset += 30 + nombre.length + datos.length;
    }
    const tamCentral = central.reduce((s, p) => s + p.length, 0);
    const fin = new DataView(new ArrayBuffer(22));
    fin.setUint32(0, 0x06054b50, true); fin.setUint16(8, archivos.length, true); fin.setUint16(10, archivos.length, true);
    fin.setUint32(12, tamCentral, true); fin.setUint32(16, offset, true);
    return new Blob([...partes, ...central, new Uint8Array(fin.buffer)],
      {type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"});
  }
  const xmlTexto = s => String(s).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/g, "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  function letraColumna(i){ let s = ""; i++; while (i > 0){ const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; }
  // hoja = {nombre, columnas:[[clave, encabezado, ancho, largo?]], filas:[{clave:valor}]}
  function hojaXml(hoja, recortes){
    const ultima = letraColumna(hoja.columnas.length - 1) + (hoja.filas.length + 1);
    const celda = (ref, valor, estilo) => {
      if (valor === null || valor === undefined || valor === "") return "";
      if (typeof valor === "number" && isFinite(valor)) return '<c r="' + ref + '" s="' + estilo + '"><v>' + valor + "</v></c>";
      let t = String(valor);
      if (t.length > XLSX_MAX_CELDA){ t = t.slice(0, XLSX_MAX_CELDA - 12) + " [recortado]"; recortes.n++; }
      return '<c r="' + ref + '" s="' + estilo + '" t="inlineStr"><is><t xml:space="preserve">' + xmlTexto(t) + "</t></is></c>";
    };
    const filas = ['<row r="1">' + hoja.columnas.map((c, i) => celda(letraColumna(i) + "1", c[1], 1)).join("") + "</row>"];
    hoja.filas.forEach((f, j) => {
      const r = j + 2;
      filas.push('<row r="' + r + '">' + hoja.columnas.map((c, i) => celda(letraColumna(i) + r, f[c[0]], c[3] ? 2 : 0)).join("") + "</row>");
    });
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
      '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' +
      '<sheetFormatPr defaultRowHeight="15"/><cols>' +
      hoja.columnas.map((c, i) => '<col min="' + (i + 1) + '" max="' + (i + 1) + '" width="' + (c[2] || 16) + '" customWidth="1"/>').join("") +
      "</cols><sheetData>" + filas.join("") + '</sheetData><autoFilter ref="A1:' + ultima + '"/></worksheet>';
  }
  function libroXlsx(hojas){
    const recortes = {n: 0};
    const NS = "http://schemas.openxmlformats.org/";
    const archivos = [
      {nombre:"[Content_Types].xml", texto:'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="' + NS + 'package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
        hojas.map((h, i) => '<Override PartName="/xl/worksheets/sheet' + (i + 1) + '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>').join("") +
        "</Types>"},
      {nombre:"_rels/.rels", texto:'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="' + NS + 'package/2006/relationships">' +
        '<Relationship Id="rId1" Type="' + NS + 'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'},
      {nombre:"xl/workbook.xml", texto:'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="' + NS + 'spreadsheetml/2006/main" xmlns:r="' + NS + 'officeDocument/2006/relationships"><sheets>' +
        hojas.map((h, i) => '<sheet name="' + xmlTexto(h.nombre) + '" sheetId="' + (i + 1) + '" r:id="rId' + (i + 1) + '"/>').join("") + "</sheets><definedNames>" +
        hojas.map((h, i) => '<definedName name="_xlnm._FilterDatabase" localSheetId="' + i + '" hidden="1">\'' + xmlTexto(h.nombre) + "'!$A$1:$" +
          letraColumna(h.columnas.length - 1) + "$" + (h.filas.length + 1) + "</definedName>").join("") + "</definedNames></workbook>"},
      {nombre:"xl/_rels/workbook.xml.rels", texto:'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="' + NS + 'package/2006/relationships">' +
        hojas.map((h, i) => '<Relationship Id="rId' + (i + 1) + '" Type="' + NS + 'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet' + (i + 1) + '.xml"/>').join("") +
        '<Relationship Id="rId' + (hojas.length + 1) + '" Type="' + NS + 'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'},
      {nombre:"xl/styles.xml", texto:'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="' + NS + 'spreadsheetml/2006/main">' +
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>' +
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>' +
        '<fill><patternFill patternType="solid"><fgColor rgb="FF14245C"/><bgColor indexed="64"/></patternFill></fill></fills>' +
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>' +
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3">' +
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top"/></xf>' +
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>' +
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>' +
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'},
    ];
    hojas.forEach((h, i) => archivos.push({nombre:"xl/worksheets/sheet" + (i + 1) + ".xml", texto: hojaXml(h, recortes)}));
    return {blob: zipStore(archivos), recortes: recortes.n};
  }

  // Atributos visibles (segun el filtro) con TODA su informacion, en hojas relacionadas por
  // dominio + codigo de atributo.
  function hojasAtributos(d, atributos){
    const cod = a => d.codigo + "-" + a.codigo_atributo;
    const num = x => /^\d+(\.\d+)?$/.test(x || "") ? Number(x) : (x || "");
    const base = a => ({dominio: d.codigo, codigo: cod(a), nombre_atributo: a.nombre_atributo});
    const colsBase = [["dominio","Dominio",10], ["codigo","Código del atributo",14], ["nombre_atributo","Nombre del atributo",34]];

    const hAtributos = {nombre:"Atributos", columnas:[
      ["dominio","Dominio",10], ["nombre_dominio","Nombre del dominio",30], ["codigo","Código del atributo",14],
      ["codigo_atributo","Código numérico",10], ["nombre_atributo","Nombre del atributo",34], ["descripcion_atributo","Descripción",60,1],
      ["criticidad","Criticidad",14], ["tipo_atributo","Tipo de atributo",18], ["compartido_similar","Compartido / similar",14],
      ["atributo_primario","Atributo primario",30], ["dominio_primario","Dominio primario",30], ["es_dato_personal","Dato personal",10],
      ["clasificacion_dato_personal","Clasificación dato personal",24], ["reporte","Reporte",9], ["proyecto","Proyecto",9],
      ["proceso","Proceso",9], ["estructura","Estructura",10], ["fecha_aprobacion","Fecha de aprobación",13], ["version","Versión",8],
      ["n_oficiales","N.º fuentes oficiales",11], ["n_consumo","N.º fuentes de consumo",11]],
      filas: atributos.map(a => ({...a, ...base(a), nombre_dominio: d.nombre, codigo_atributo: num(a.codigo_atributo),
        version: num(a.version), n_oficiales: a.fuentes_oficiales.length, n_consumo: a.fuentes_consumo.length}))};

    const hOficiales = {nombre:"Fuentes oficiales", columnas: colsBase.concat([
      ["servidor","Servidor",18], ["base_datos","Base de datos",40], ["tabla","Tabla / archivo",28], ["nombre_campo","Campo",24],
      ["es_fuente_primaria","Primaria",9], ["tipo_campo","Tipo de campo",12], ["longitud_campo","Longitud",9],
      ["acepta_valores_nulos","Acepta nulos",9], ["formula_calculo","Fórmula de cálculo",70,1],
      ["lista_valores_validos","Valores válidos",50,1], ["fecha_aprobacion","Fecha de aprobación",13], ["version","Versión",8]]),
      filas: atributos.flatMap(a => a.fuentes_oficiales.map(f => ({...f, ...base(a), longitud_campo: num(f.longitud_campo), version: num(f.version)})))};

    const hConsumo = {nombre:"Fuentes de consumo", columnas: colsBase.concat([
      ["clase","Clase",14], ["servidor","Servidor",18], ["base_datos","Base de datos / colección",32], ["tabla","Tabla",28],
      ["nombre_campo","Campo",24], ["fecha_aprobacion","Fecha de aprobación",13]]),
      filas: atributos.flatMap(a => a.fuentes_consumo.map(f => ({...f, ...base(a)})))};

    const hRelaciones = {nombre:"Relaciones", columnas: [["direccion","Dirección",13]].concat(colsBase, [
      ["tipo","Tipo de relación",14], ["dominio_rel","Dominio relacionado",12], ["atributo_rel","Atributo relacionado",40],
      ["estado","Estado del vínculo",18], ["estado_desc","Descripción del estado",50,1]]), filas: []};
    if (REL){
      const codigos = new Set(atributos.map(cod));
      const norm = s => sinTildes(s).trim().toLowerCase();
      const porNombre = new Map(atributos.map(a => [norm(a.nombre_atributo), a]));
      const desc = e => (REL.descripcion_estado || {})[e] || "";
      for (const x of REL.aristas.flatMap(a => a.atributos).concat(REL.problemas)){
        if (codigos.has(x.codigo_origen)) hRelaciones.filas.push({direccion:"Usa", dominio:d.codigo, codigo:x.codigo_origen,
          nombre_atributo:x.atributo_origen, tipo:x.tipo, dominio_rel:x.dominio_destino, atributo_rel:x.atributo_destino,
          estado:x.estado, estado_desc:desc(x.estado)});
        const a = x.dominio_destino === d.codigo ? porNombre.get(norm(x.atributo_destino)) : null;
        if (a) hRelaciones.filas.push({direccion:"Es usado por", ...base(a), tipo:x.tipo, dominio_rel:x.dominio_origen,
          atributo_rel: x.codigo_origen + " · " + x.atributo_origen, estado:x.estado, estado_desc:desc(x.estado)});
      }
    }

    const generado = (document.querySelector(".marca small") || {}).textContent || "";
    const hFiltro = {nombre:"Filtro", columnas:[["campo","Dato",28], ["valor","Valor",70,1]], filas:[
      {campo:"Dominio", valor: d.codigo + " · " + d.nombre},
      {campo:"Filtro aplicado", valor: estado.filtroAtributo || "(sin filtro)"},
      {campo:"Atributos exportados", valor: atributos.length + " de " + d.atributos.length},
      {campo:"Fuentes oficiales", valor: hOficiales.filas.length}, {campo:"Fuentes de consumo", valor: hConsumo.filas.length},
      {campo:"Relaciones", valor: REL ? hRelaciones.filas.length : "No disponibles (falta la vista gdd.v_relacion_atributo)"},
      {campo:"Sitio", valor: generado}, {campo:"Descargado", valor: new Date().toLocaleString("es")},
      {campo:"Nota", valor:"Hojas relacionadas por Dominio + Código del atributo. Fuente: gdd.* (registros activos), solo lectura."},
    ]};
    return [hAtributos, hOficiales, hConsumo, hRelaciones, hFiltro];
  }

  function descargarAtributos(d, atributos){
    const {blob, recortes} = libroXlsx(hojasAtributos(d, atributos));
    const t = new Date(), dos = n => String(n).padStart(2, "0");
    const nombre = "atributos_" + d.codigo + (estado.filtroAtributo ? "_filtrado" : "") + "_" + t.getFullYear() + dos(t.getMonth() + 1) +
      dos(t.getDate()) + "_" + dos(t.getHours()) + dos(t.getMinutes()) + ".xlsx";
    const url = URL.createObjectURL(blob);
    const a = el("a", {href:url, download:nombre});
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    if (recortes) alert(recortes + " celda(s) superaban el límite de Excel (32.767 caracteres) y se recortaron.");
  }

  // --- seccion 2
  function vistaAtributos(d){
    const f = estado.filtroAtributo.toLowerCase();
    const visibles = d.atributos.filter(a => !f || [a.codigo_atributo, a.nombre_atributo, a.descripcion_atributo]
      .concat(a.fuentes_oficiales.flatMap(x => [x.base_datos, x.tabla, x.nombre_campo, x.formula_calculo]))
      .concat(a.fuentes_consumo.flatMap(x => [x.base_datos, x.tabla, x.nombre_campo]))
      .some(t => (t || "").toLowerCase().includes(f)));

    const entrada = el("input", {type:"search", placeholder:"Filtrar por atributo, tabla, campo o fórmula", value: estado.filtroAtributo});
    entrada.addEventListener("input", e => {
      estado.filtroAtributo = e.target.value;
      const pos = e.target.selectionStart;
      pintarContenido();
      const nuevo = document.querySelector(".herramientas input");
      if (nuevo){ nuevo.focus(); nuevo.setSelectionRange(pos, pos); }
    });
    let abiertos = false;
    const botonAbrir = el("button", {type:"button", texto:"Expandir todo", onclick: () => {
      abiertos = !abiertos;
      document.querySelectorAll("details.atributo").forEach(x => x.open = abiertos);
      botonAbrir.textContent = abiertos ? "Contraer todo" : "Expandir todo";
    }});
    const botonExcel = el("button", {type:"button", clase:"btn-excel", texto:"Descargar Excel",
      title: "Descarga los " + visibles.length + " atributos visibles con sus fuentes oficiales, fuentes de consumo y relaciones",
      onclick: () => descargarAtributos(d, visibles)});
    if (!visibles.length) botonExcel.disabled = true;
    const herramientas = el("div", {clase:"herramientas"}, [entrada, botonAbrir, botonExcel,
      el("span", {clase:"nota", texto: visibles.length + " de " + d.atributos.length + " atributos"})]);

    const lista = visibles.map(a => {
      const chips = el("div", {clase:"chips"}, [
        a.criticidad ? el("span", {clase:"chip", texto:a.criticidad}) : null,
        a.es_dato_personal === "Sí" ? el("span", {clase:"chip pers", texto:"Dato personal"}) : null,
        el("span", {clase:"chip of", texto: a.fuentes_oficiales.length + " oficial" + (a.fuentes_oficiales.length === 1 ? "" : "es")}),
        el("span", {clase:"chip co", texto: a.fuentes_consumo.length + " consumo"}),
      ]);
      const resumen = el("summary", null, [
        el("span", {clase:"cod", texto: d.codigo + "-" + a.codigo_atributo}),
        el("div", null, [el("div", {clase:"nom", texto:a.nombre_atributo}), el("div", {clase:"desc", texto:a.descripcion_atributo || ""})]),
        chips,
      ]);
      const tablaFO = tablaFuentesOficiales(a.fuentes_oficiales);
      const botonTodas = a.fuentes_oficiales.filter(tieneDetalle).length > 1 ? el("button", {
        type:"button", clase:"btn-detalle btn-todas", texto:"Abrir todas las fórmulas",
        onclick: e => {
          const b = e.currentTarget, abrir = b.dataset.abierto !== "1";
          tablaFO.querySelectorAll("button.btn-fila").forEach(x => alternarDetalle(x, abrir));
          b.dataset.abierto = abrir ? "1" : "";
          b.textContent = abrir ? "Cerrar todas las fórmulas" : "Abrir todas las fórmulas";
        }}) : null;
      const cuerpo = el("div", {clase:"cuerpo"}, [
        el("div", {clase:"bloques"}, [
          // 1. descripcion completa, con criticidad y tipo de atributo
          el("section", {clase:"bloque ancho"}, [
            el("h5", {texto:"Descripción"}),
            el("p", {clase:"descripcion", texto: v(a.descripcion_atributo)}),
            ficha([["criticidad","Criticidad"], ["tipo_atributo","Tipo de atributo"]], a),
          ]),
          // 2. relacion con otros dominios
          el("section", {clase:"bloque"}, [
            el("h5", {texto:"Compartido / similar"}),
            ficha([["compartido_similar","Compartido / similar"], ["atributo_primario","Atributo primario"],
              ["dominio_primario","Dominio primario"]], a),
          ]),
          // 3. resto de la ficha (sin release, a pedido del usuario 2026-11-10)
          el("section", {clase:"bloque"}, [
            el("h5", {texto:"Clasificación y aprobación"}),
            ficha([["es_dato_personal","Dato personal"], ["clasificacion_dato_personal","Clasificación dato personal"],
              ["reporte","Reporte"], ["proyecto","Proyecto"], ["proceso","Proceso"], ["estructura","Estructura"],
              ["fecha_aprobacion","Fecha de aprobación"], ["version","Versión"]], a),
          ]),
        ]),
        el("h4", null, ["Fuentes oficiales ", cuenta(a.fuentes_oficiales.length), botonTodas]),
        tablaFO,
        el("h4", null, ["Fuentes de consumo ", cuenta(a.fuentes_consumo.length)]),
        tabla([["clase","Clase"], ["servidor","Servidor"], ["base_datos","Base de datos / colección"], ["tabla","Tabla"],
          ["nombre_campo","Campo"], ["fecha_aprobacion","Fecha de aprobación"]],
          a.fuentes_consumo, "Sin fuentes de consumo activas."),
      ]);
      return el("details", {clase:"atributo"}, [resumen, cuerpo]);
    });

    return [cabeceraDominio(d), herramientas,
      lista.length ? lista : el("div", {clase:"vacio", texto: d.atributos.length ? "Ningún atributo coincide con el filtro." : "El dominio no tiene atributos activos."})];
  }

  // --- seccion 3 (sin los terminos: van en la seccion "Glosario de términos")
  function vistaInvestigacion(d){
    const grupos = new Map();
    const registros = d.investigacion.filter(r => !esTermino(r));
    const nTerminos = d.investigacion.length - registros.length;
    for (const r of registros){
      const k = r.clasificacion || "Sin clasificación";
      if (!grupos.has(k)) grupos.set(k, []);
      grupos.get(k).push(r);
    }
    const bloques = [...grupos.entries()].map(([k, filas]) => el("div", {clase:"grupo"}, [
      el("h3", null, [k, cuenta(filas.length)]),
      tabla([["nombre","Nombre"], ["descripcion","Descripción","largo"], ["referencia_normativa","Referencia normativa","largo"]], filas),
    ]));
    const avisoTerminos = nTerminos ? el("div", {clase:"avisos aviso-red"}, [
      "Los " + nTerminos + " términos de este dominio están en la sección «Glosario de términos». ",
      el("button", {type:"button", clase:"btn-detalle", texto:"Ver términos",
        onclick: () => { estado.seccion = "terminos"; actualizar(); }}),
    ]) : null;
    return [cabeceraDominio(d), avisoTerminos,
      tarjeta("Investigación", registros.length,
        bloques.length ? el("div", null, bloques) : el("div", {clase:"vacio", texto:"Sin registros de investigación activos."}))];
  }

  // --- seccion 4: glosario de terminos (registros de investigacion con clasificacion "Término")
  function vistaTerminos(d){
    const fuente = d ? [d] : DATOS.dominios;
    const todos = fuente.flatMap(x => x.investigacion.filter(esTermino).map(r => ({...r, dominio:x.codigo, nombreDominio:x.nombre})))
      .sort((a, b) => (a.nombre || "").localeCompare(b.nombre || "", "es", {sensitivity:"base"}));
    const letraDe = r => (sinTildes(r.nombre).trim().charAt(0).toUpperCase().match(/[A-Z]/) || ["#"])[0];
    const f = sinTildes(estado.filtroTermino).toLowerCase();
    const porTexto = todos.filter(r => !f || [r.nombre, r.descripcion, r.referencia_normativa]
      .some(t => sinTildes(t).toLowerCase().includes(f)));
    const letras = [...new Set(porTexto.map(letraDe))];
    if (estado.letraTermino && !letras.includes(estado.letraTermino)) estado.letraTermino = "";
    const visibles = porTexto.filter(r => !estado.letraTermino || letraDe(r) === estado.letraTermino);

    const cab = d ? cabeceraDominio(d) : el("div", {clase:"cabecera"}, [el("div", null, [
      el("h2", {texto:"Glosario de términos"}),
      el("div", {clase:"sub", texto:"Términos de todos los dominios (hoja Investigación, clasificación «Término»). " +
        "Elige un dominio para ver solo los suyos."})])]);

    const entrada = el("input", {type:"search", placeholder:"Buscar término, definición o referencia", value: estado.filtroTermino});
    entrada.addEventListener("input", e => {
      estado.filtroTermino = e.target.value;
      const pos = e.target.selectionStart;
      pintarContenido();
      const nuevo = document.querySelector(".herramientas input");
      if (nuevo){ nuevo.focus(); nuevo.setSelectionRange(pos, pos); }
    });
    const indice = el("div", {clase:"indice-letras"}, [
      el("button", {type:"button", clase: estado.letraTermino ? "" : "activa", texto:"Todas",
        onclick: () => { estado.letraTermino = ""; pintarContenido(); }}),
      ...letras.map(l => el("button", {type:"button", clase: l === estado.letraTermino ? "activa" : "", texto:l,
        onclick: () => { estado.letraTermino = l; pintarContenido(); }})),
    ]);
    const herramientas = el("div", {clase:"herramientas"}, [entrada,
      el("span", {clase:"nota", texto: visibles.length + " de " + todos.length + " términos"})]);

    const grupos = new Map();
    for (const r of visibles){ const l = letraDe(r); if (!grupos.has(l)) grupos.set(l, []); grupos.get(l).push(r); }
    const lista = [...grupos.entries()].map(([l, filas]) => el("section", {clase:"letra"}, [
      el("div", {clase:"letra-cab"}, [el("span", {clase:"letra-marca", texto:l}), cuenta(filas.length)]),
      el("div", {clase:"terminos"}, filas.map(r => el("article", {clase:"termino"}, [
        el("div", {clase:"termino-cab"}, [
          el("h4", {texto: r.nombre || "(sin nombre)"}),
          d ? null : el("button", {type:"button", clase:"chip-dominio", title: r.nombreDominio,
            texto: r.dominio, onclick: () => {
              estado.dominio = r.dominio; estado.letraTermino = ""; estado.filtroTermino = ""; actualizar(); }}),
        ]),
        el("p", {texto: v(r.descripcion)}),
        r.referencia_normativa ? el("div", {clase:"termino-ref"}, [el("b", {texto:"Referencia: "}), r.referencia_normativa]) : null,
      ]))),
    ]));

    return [cab, herramientas, indice,
      lista.length ? lista : el("div", {clase:"vacio", texto: todos.length
        ? "Ningún término coincide con la búsqueda." : (d ? "Este dominio no tiene términos activos." : "No hay términos activos.")})];
  }

  // --- seccion 4: relaciones entre dominios (grafo vis-network)
  //     REL = modelo de exportar/grafo_relaciones.py; null si falta la vista gdd.v_relacion_atributo.
  //     Flecha: el dominio de ORIGEN usa atributos del dominio DESTINO (dueno / primario).
  const REL = DATOS.relaciones;
  const OSCURO = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  // en modo oscuro el azul marino no se ve sobre el fondo: se usa un azul claro
  const COLOR_TIPO = {"Compartido": OSCURO ? "#8FA8F0" : "#14245C", "Similar":"#E0A800"};
  const colorTipo = t => COLOR_TIPO[t] || "#8A94AD";
  const relNodoPorId = new Map(REL ? REL.nodos.map(n => [n.id, n]) : []);
  const relNodoPorCod = new Map(REL ? REL.nodos.map(n => [n.codigo, n]) : []);
  let redActual = null;          // instancia vis.Network viva (se destruye al cambiar de vista)
  const despuesDePintar = [];    // vis necesita el contenedor ya insertado en la pagina

  function irADominio(codigo){
    if (!porCodigo.has(codigo)) return;
    estado.dominio = codigo;
    setTimeout(actualizar, 0);   // fuera del manejador de clic de vis (la red se destruye al repintar)
  }

  function chipEstado(e){
    return el("span", {clase: "chip" + (e === "RESUELTO" || e === "MISMO_DOMINIO" ? "" : " pers"),
      title: (REL.descripcion_estado || {})[e] || "", texto: e === "DESTINO_INACTIVO" ? "versión inactiva" : e});
  }

  function tablaVecinos(aristas, campo, vacio){
    const agg = new Map();
    for (const a of aristas) agg.set(a[campo], (agg.get(a[campo]) || 0) + a.n);
    if (!agg.size) return el("div", {clase:"vacio", texto:vacio});
    const filas = [...agg].sort((x, y) => y[1] - x[1]).map(([id, n]) => {
      const nodo = relNodoPorId.get(id) || {codigo:"#" + id, nombre:""};
      return el("tr", {clase:"clic", title:"Ver sus relaciones", onclick: () => irADominio(nodo.codigo)}, [
        el("td", {texto:nodo.codigo}), el("td", {texto:nodo.nombre}), el("td", {texto:String(n)})]);
    });
    return el("div", {clase:"tabla-wrap"}, el("table", null, [
      el("thead", null, el("tr", null, ["Código","Dominio","Atributos"].map(c => el("th", {texto:c})))),
      el("tbody", null, filas)]));
  }

  function tablaRelacionesAtributo(filas, vacio){
    if (!filas.length) return el("div", {clase:"vacio", texto:vacio});
    return el("div", {clase:"tabla-wrap"}, el("table", null, [
      el("thead", null, el("tr", null, ["Atributo","Nombre","Tipo","Dominio destino","Atributo destino","Estado"]
        .map(c => el("th", {texto:c})))),
      el("tbody", null, filas.map(x => el("tr", null, [
        el("td", {texto:x.codigo_origen}), el("td", {texto:v(x.atributo_origen)}), el("td", {texto:v(x.tipo)}),
        el("td", {texto:v(x.dominio_destino)}), el("td", {texto:v(x.atributo_destino)}), el("td", null, chipEstado(x.estado)),
      ])))]));
  }

  function mostrarArista(id){
    const a = REL.aristas.find(x => x.id === id), cont = document.getElementById("detalleArista");
    if (!a || !cont) return;
    const o = relNodoPorId.get(a.origen), dd = relNodoPorId.get(a.destino);
    cont.replaceChildren(
      el("h4", null, [o.codigo + " → " + dd.codigo, cuenta(a.n)]),
      el("div", {clase:"nota-red", texto: o.nombre + " usa " + a.n + " atributo(s) " + a.tipo.toLowerCase() +
        (a.n === 1 ? "" : "s") + " de " + dd.nombre + "."}),
      tablaRelacionesAtributo(a.atributos, ""));
  }

  function crearRed(contenedor, nodos, aristas, foco){
    const texto = getComputedStyle(document.body).color || "#1B2547";
    const datosNodos = nodos.map(n => {
      const esFoco = foco && n.id === foco.id;
      const borde = n.sin_vinculo ? "#D93A3E" : "#FCCF33";   // rojo: tiene vinculos pendientes
      return {
        id: n.id, label: n.codigo, value: 1 + n.dependientes,
        scaling: {min: 12, max: 44, label: {enabled: true, min: 12, max: 20}},
        color: esFoco ? {background:"#FDE338", border: n.sin_vinculo ? "#D93A3E" : "#14245C",
                         highlight:{background:"#FDE338", border:"#14245C"}}
                      : {background:"#14245C", border: borde, highlight:{background:"#FDE338", border:"#14245C"},
                         hover:{background:"#2A3F82", border: borde}},
        borderWidth: esFoco || n.sin_vinculo ? 3 : 2, font: {color: texto, face: "Segoe UI, Arial", size: 13, strokeWidth: 0},
        title: n.codigo + " — " + n.nombre + "\nDominios que usan sus atributos: " + n.dependientes +
               "\nDominios de los que usa atributos: " + n.dependencias +
               (n.sin_vinculo ? "\nSin vínculo resuelto: " + n.sin_vinculo : ""),
      };
    });
    const datosAristas = aristas.map(a => {
      const c = colorTipo(a.tipo), o = relNodoPorId.get(a.origen), dd = relNodoPorId.get(a.destino);
      return {
        id: a.id, from: a.origen, to: a.destino, value: a.n, scaling: {min: 1, max: 9},
        color: {color: c, highlight: c, hover: c, opacity: .85}, dashes: a.n_inactivo === a.n,
        smooth: {type: a.tipo === "Similar" ? "curvedCCW" : "curvedCW", roundness: .15},
        title: o.codigo + " → " + dd.codigo + "\n" + a.tipo + ": " + a.n + " atributo(s)" +
               (a.n_inactivo ? "\n" + a.n_inactivo + " apuntan a versión inactiva" : ""),
      };
    });
    redActual = new vis.Network(contenedor, {nodes: new vis.DataSet(datosNodos), edges: new vis.DataSet(datosAristas)}, {
      nodes: {shape: "dot"},   // etiqueta debajo del nodo (con "ellipse" quedaria dentro, ilegible sobre azul)
      edges: {arrows: {to: {enabled: true, scaleFactor: .6}}, selectionWidth: 2},
      physics: {solver: "forceAtlas2Based", stabilization: {iterations: 400},
                forceAtlas2Based: {gravitationalConstant: -70, springLength: 140, avoidOverlap: .4}},
      interaction: {hover: true, tooltipDelay: 150},
    });
    const red = redActual;
    red.once("stabilizationIterationsDone", () => {
      red.setOptions({physics: false});
      if (nodos.length === 1) red.moveTo({scale: 1.2});   // un solo nodo: sin zoom exagerado
    });
    red.on("click", p => {
      if (p.nodes.length){ const n = relNodoPorId.get(p.nodes[0]); if (n) irADominio(n.codigo); }
      else if (p.edges.length) mostrarArista(p.edges[0]);
    });
  }

  function vistaRelaciones(d){
    const cab = d ? cabeceraDominio(d) : el("div", {clase:"cabecera"}, [el("div", null, [
      el("h2", {texto:"Relaciones entre dominios"}),
      el("div", {clase:"sub", texto:"Atributos Compartido / Similar. Elige un dominio para ver solo sus relaciones."})])]);
    if (!REL) return [cab, el("div", {clase:"avisos", texto:"El grafo no está disponible: falta la vista " +
      "gdd.v_relacion_atributo en la base (db/migraciones/20261105_vistas_relacion_dominio.sql)."})];

    const tipos = estado.tiposRel || new Set(REL.tipos);
    const foco = d ? relNodoPorCod.get(d.codigo) || null : null;
    const aristas = REL.aristas.filter(a => tipos.has(a.tipo) &&
      (!foco || a.origen === foco.id || a.destino === foco.id));
    const ids = new Set(); aristas.forEach(a => { ids.add(a.origen); ids.add(a.destino); });
    if (foco) ids.add(foco.id);
    // El grafo se dibuja siempre. Sin flechas resueltas, la vista general muestra todos los dominios
    // (borde rojo = tiene vinculos pendientes) y la de un dominio muestra el dominio solo.
    const todos = !d && (estado.aislados || !aristas.length);
    const nodos = REL.nodos.filter(n => ids.has(n.id) || todos);

    // controles
    const controles = el("div", {clase:"herramientas controles-red"}, [
      ...REL.tipos.map(t => {
        const caja = el("input", {type:"checkbox"});
        caja.checked = tipos.has(t);
        caja.addEventListener("change", () => {
          const s = new Set(tipos); caja.checked ? s.add(t) : s.delete(t);
          estado.tiposRel = s; pintarContenido();
        });
        return el("label", {clase:"opcion"}, [caja, el("span", {clase:"sw", style:"background:" + colorTipo(t)}),
          t + " (" + (REL.resumen.por_tipo[t] || 0) + ")"]);
      }),
      d ? null : (() => {
        const caja = el("input", {type:"checkbox"}); caja.checked = estado.aislados;
        caja.addEventListener("change", () => { estado.aislados = caja.checked; pintarContenido(); });
        return el("label", {clase:"opcion"}, [caja, "Mostrar dominios sin relaciones"]);
      })(),
      el("button", {type:"button", texto:"Reorganizar", onclick: () => {
        if (redActual){ redActual.setOptions({physics: true}); redActual.stabilize(400); } }}),
      el("button", {type:"button", texto:"Ajustar vista", onclick: () => redActual && redActual.fit({animation:{duration:400}})}),
    ]);

    const contenedor = el("div", {clase:"grafo"});
    const pendientes = REL.problemas.length;
    const vacioRed = !aristas.length ? el("div", {clase:"avisos aviso-red", texto: (d
      ? "Este dominio todavía no tiene relaciones resueltas con otros dominios" + (foco && foco.sin_vinculo
          ? ": " + foco.sin_vinculo + " atributo(s) declaran Compartido/Similar sin vínculo resuelto." : ".")
      : "Todavía no hay relaciones resueltas entre dominios: el grafo muestra los " + nodos.length +
        " dominios sin flechas" + (pendientes ? " y " + pendientes + " atributo(s) esperan su vínculo." : ".")) +
      " Las flechas aparecen cuando el atributo queda vinculado a su atributo primario (al cargar el dominio " +
      "dueño o con vinculos_cli)."}) : null;
    if (nodos.length) despuesDePintar.push(() => crearRed(contenedor, nodos, aristas, foco));
    const leyenda = el("div", {clase:"nota-red", texto:"Flecha: el dominio de origen usa atributos del dominio destino " +
      "(dueño). Tamaño del nodo: cuántos dominios dependen de él. Grosor: cantidad de atributos. Línea punteada: " +
      "apunta a una versión inactiva. Borde rojo: el dominio tiene atributos Compartido/Similar sin vínculo resuelto. " +
      "Clic en un dominio para ver sus relaciones; clic en una flecha para ver sus atributos."});
    const tarjetaGrafo = tarjeta(d ? "Relaciones de " + d.codigo : "Grafo de relaciones", aristas.length, [
      controles, vacioRed, nodos.length ? contenedor : null, leyenda,
      el("div", {id:"detalleArista", clase:"detalle-arista"}),
    ]);

    const r = REL.resumen;
    if (!d){
      const estados = [...new Set(REL.problemas.map(p => p.estado))];
      const sel = el("select", null, [el("option", {value:"", texto:"Todos los estados (" + REL.problemas.length + ")"}),
        ...estados.map(e => el("option", {value:e, texto: e + " (" + REL.problemas.filter(p => p.estado === e).length + ")"}))]);
      sel.value = estado.estadoRel;
      sel.addEventListener("change", () => { estado.estadoRel = sel.value; pintarContenido(); });
      const problemas = REL.problemas.filter(p => !estado.estadoRel || p.estado === estado.estadoRel);
      return [cab,
        el("div", {clase:"kpis"}, [
          ["Dominios relacionados", r.n_dominios_con_relacion + " de " + r.n_dominios],
          ["Pares de dominios", r.n_pares_dominio],
          ["Atributos en el grafo", r.n_atributos_en_grafo + " de " + r.n_atributos_relacionados],
          ["Vínculos por revisar", REL.problemas.length],
        ].map(([e, n]) => el("div", {clase:"kpi"}, [el("b", {texto:String(n)}), el("span", {texto:e})]))),
        tarjetaGrafo,
        tarjeta("Calidad de vínculos (no se dibujan)", REL.problemas.length, [
          el("div", {clase:"herramientas"}, [sel, el("span", {clase:"nota", texto: estado.estadoRel
            ? (REL.descripcion_estado[estado.estadoRel] || "") : "Atributos que declaran relación pero sin destino confiable."})]),
          tablaRelacionesAtributo(problemas, "Sin vínculos por revisar."),
        ]),
      ];
    }

    const salientes = aristas.filter(a => foco && a.origen === foco.id);
    const entrantes = aristas.filter(a => foco && a.destino === foco.id);
    const propios = salientes.flatMap(a => a.atributos)
      .concat(REL.problemas.filter(p => p.dominio_origen === d.codigo))
      .sort((x, y) => x.codigo_origen.localeCompare(y.codigo_origen, "es", {numeric: true}));
    return [cab,
      el("div", {clase:"kpis"}, [
        ["Dominios que usan sus atributos", foco ? foco.dependientes : 0],
        ["Dominios de los que usa atributos", foco ? foco.dependencias : 0],
        ["Atributos que declaran relación", propios.length],
        ["Sin vínculo resuelto", foco ? foco.sin_vinculo : 0],
      ].map(([e, n]) => el("div", {clase:"kpi"}, [el("b", {texto:String(n)}), el("span", {texto:e})]))),
      tarjetaGrafo,
      el("div", {clase:"dos-columnas"}, [
        tarjeta("Dominios que usan sus atributos", entrantes.length ? new Set(entrantes.map(a => a.origen)).size : 0,
          tablaVecinos(entrantes, "origen", "Ningún dominio usa atributos de este dominio.")),
        tarjeta("Dominios de los que usa atributos", salientes.length ? new Set(salientes.map(a => a.destino)).size : 0,
          tablaVecinos(salientes, "destino", "No usa atributos de otros dominios.")),
      ]),
      tarjeta("Atributos de " + d.codigo + " que declaran relación", propios.length,
        tablaRelacionesAtributo(propios, "Ningún atributo declara Compartido / Similar.")),
    ];
  }

  // --- seccion 7: indicadores de fuente oficial ---------------------------------------------------------
  //     Graficos en HTML (no SVG escalado: el texto mantiene su tamano). Forma segun la variable:
  //     puntaje 0-100 -> barras horizontales de un color, de menor a mayor; conteos con/sin fuente ->
  //     barras apiladas (2 series, leyenda); si/no por dominio -> medidor "X de N" + matriz de estado
  //     (icono + texto). Cada grafico tiene su vista de tabla. Colores: --serie-1 / --serie-2 (validados).
  const IND = DATOS.resumen.indicadores;
  const tip = el("div", {id:"tip-viz", role:"tooltip", hidden:""});
  document.body.append(tip);
  function mostrarTip(evento, filas){
    tip.replaceChildren(...filas.map(([valor, etiqueta, color]) => el("div", {clase:"tip-fila"}, [
      color ? el("span", {clase:"tip-clave", style:"background:" + color}) : null,
      el("b", {texto:String(valor)}), el("span", {texto:etiqueta})])));
    tip.hidden = false;
    const r = evento.target.getBoundingClientRect();
    const x = evento.clientX || (r.left + r.width / 2), y = evento.clientY || r.top;
    const ancho = tip.offsetWidth, alto = tip.offsetHeight;
    tip.style.left = Math.min(window.innerWidth - ancho - 8, Math.max(8, x + 12)) + "px";
    tip.style.top = Math.max(8, y - alto - 12) + "px";
  }
  function ocultarTip(){ tip.hidden = true; }
  function conTip(nodo, filas){
    nodo.tabIndex = 0;
    nodo.addEventListener("pointermove", e => mostrarTip(e, filas));
    nodo.addEventListener("pointerleave", ocultarTip);
    nodo.addEventListener("focus", e => mostrarTip(e, filas));
    nodo.addEventListener("blur", ocultarTip);
    return nodo;
  }
  const pct = (n, t) => t ? Math.round(100 * n / t) + " %" : "—";
  // Etiqueta dentro de un segmento: visible solo si cabe con holgura; si no, la llevan el tooltip y la
  // tabla. Se mide con ResizeObserver (tamano real ya calculado, y de nuevo si cambia el ancho).
  const obsEtiquetas = new ResizeObserver(entradas => {
    for (const en of entradas){
      const seg = en.target, etq = seg.querySelector(".seg-etq");
      if (etq) etq.style.visibility = etq.offsetWidth > seg.clientWidth - 8 ? "hidden" : "visible";
    }
  });

  // Grafico + boton "Ver tabla" (la tabla es la via accesible para cada valor).
  function conTabla(grafico, columnas, filas){
    const tablaNodo = tabla(columnas, filas);
    tablaNodo.hidden = true;
    const b = el("button", {type:"button", clase:"btn-detalle btn-tabla", texto:"Ver tabla", onclick: () => {
      const verTabla = tablaNodo.hidden;
      tablaNodo.hidden = !verTabla; grafico.hidden = verTabla;
      b.textContent = verTabla ? "Ver gráfico" : "Ver tabla";
    }});
    return el("div", {clase:"viz"}, [el("div", {clase:"viz-acciones"}, b), grafico, tablaNodo]);
  }

  // Barras horizontales de una serie: [{etiqueta, sub, valor, texto, tip, onclick}], escala 0..max.
  function barrasH(filas, max, ticks){
    const cont = el("div", {clase:"barras"});
    for (const f of filas){
      const ancho = f.valor === null ? 0 : Math.max(0, Math.min(100, 100 * f.valor / max));
      const barra = f.valor === null ? null : conTip(el("div", {clase:"barra", style:"width:" + ancho + "%"}), f.tip);
      cont.append(el("div", {clase:"fila-barra" + (f.onclick ? " clic" : ""), onclick: f.onclick}, [
        el("div", {clase:"etq-barra"}, [el("b", {texto:f.etiqueta}), f.sub ? el("span", {texto:f.sub}) : null]),
        el("div", {clase:"pista"}, [barra, el("span", {clase: "valor-barra" + (f.valor === null ? " sin-valor" : ""), texto:f.texto})]),
      ]));
    }
    if (ticks) cont.append(el("div", {clase:"fila-barra eje"}, [el("div"), el("div", {clase:"pista ticks"},
      ticks.map(t => el("span", {style:"left:" + (100 * t / max) + "%", texto:String(t)})))]));
    return cont;
  }

  // Barras apiladas: series [{nombre, color}], filas [{etiqueta, sub, partes:[n por serie], onclick}].
  function barrasApiladas(series, filas, max){
    const leyenda = el("div", {clase:"leyenda"}, series.map(s =>
      el("span", null, [el("span", {clase:"sw-rect", style:"background:" + s.color}), s.nombre])));
    const cont = el("div", {clase:"barras"});
    for (const f of filas){
      const total = f.partes.reduce((a, b) => a + b, 0);
      const segs = f.partes.map((n, i) => {
        if (!n) return null;
        // ancho del segmento proporcional a su valor dentro de la barra (flex-grow = valor)
        const seg = conTip(el("div", {clase:"seg", style:"flex:" + n + " 1 0;background:" + series[i].color}),
          [[n + " de " + total, series[i].nombre, series[i].color], [pct(n, total), "del total de la fila"]]);
        seg.append(el("span", {clase:"seg-etq" + (series[i].claro ? " sobre-claro" : ""), texto:String(n)}));
        obsEtiquetas.observe(seg);   // la etiqueta se muestra solo si cabe (ver obsEtiquetas)
        return seg;
      }).filter(Boolean);
      cont.append(el("div", {clase:"fila-barra" + (f.onclick ? " clic" : ""), onclick: f.onclick}, [
        el("div", {clase:"etq-barra"}, [el("b", {texto:f.etiqueta}), f.sub ? el("span", {texto:f.sub}) : null]),
        el("div", {clase:"pista"}, [total ? el("div", {clase:"apilada", style:"width:" + (100 * total / max) + "%"}, segs) : null,
          el("span", {clase:"valor-barra", texto:String(total)})]),
      ]));
    }
    return el("div", null, [leyenda, cont]);
  }

  // Medidor: una proporcion contra su limite ("4 de 6 dominios").
  function medidor(etiqueta, n, total, detalle){
    return conTip(el("div", {clase:"medidor"}, [
      el("div", {clase:"medidor-cab"}, [el("span", {texto:etiqueta}), el("b", {texto: n + " de " + total})]),
      el("div", {clase:"medidor-pista"}, el("div", {clase:"medidor-relleno", style:"width:" + (total ? 100 * n / total : 0) + "%"})),
    ]), [[n + " de " + total, detalle || etiqueta], [pct(n, total), ""]]);
  }

  // Estado si/no con icono + texto (nunca solo color).
  function chipCumple(ok, texto, titulo){
    return el("span", {clase:"estado " + (ok ? "ok" : "no"), title: titulo || ""}, [
      el("span", {clase:"icono", "aria-hidden":"true", texto: ok ? "✓" : "✕"}), texto]);
  }
  const nombreRol = id => (IND.roles_requeridos.find(r => r.id === id) || {nombre:"Rol " + id}).nombre;
  const SERIES_FO = [{nombre:"Con fuente oficial", color:"var(--serie-1)"}, {nombre:"Sin fuente oficial", color:"var(--serie-2)", claro:true}];
  const SERIES_VINC = [{nombre:"Vínculo resuelto", color:"var(--serie-1)"}, {nombre:"Vínculo pendiente", color:"var(--serie-2)", claro:true}];
  const reglaTexto = "Parte de 100. Cada atributo activo sin fuente oficial activa resta: " +
    IND.penalizacion.critico_estructura + " si es crítico y está marcado en Estructura, " +
    IND.penalizacion.critico + " si solo es crítico y " + IND.penalizacion.otro + " en cualquier otro caso. Mínimo 0.";
  const verDominio = codigo => () => { estado.dominio = codigo; actualizar(); };

  function vistaIndicadores(d){
    return d ? indicadoresDominio(d) : indicadoresGenerales();
  }

  function indicadoresGenerales(){
    const doms = DATOS.dominios, evaluados = doms.filter(x => x.indicadores.puntaje !== null);
    const cab = el("div", {clase:"cabecera"}, [el("div", null, [
      el("h2", {texto:"Indicadores de fuente oficial"}), el("div", {clase:"sub", texto: reglaTexto})])]);

    const hero = el("section", {clase:"tarjeta"}, [el("h3", {texto:"Puntaje promedio de fuente oficial"}), el("div", {clase:"tc hero"}, [
      el("div", {clase:"hero-num", texto: IND.puntaje_promedio === null ? "—" : String(IND.puntaje_promedio)}),
      el("div", {clase:"hero-sub", texto:"de 100 · promedio de " + IND.dominios_evaluados + " dominio(s) con atributos activos"}),
    ])]);
    const kpis = el("div", {clase:"kpis"}, [
      ["Atributos con fuente oficial", IND.con_fo + " de " + IND.atributos, pct(IND.con_fo, IND.atributos)],
      ["Críticos con fuente oficial", IND.criticos_con_fo + " de " + IND.criticos, pct(IND.criticos_con_fo, IND.criticos)],
      ["No críticos con fuente oficial", (IND.con_fo - IND.criticos_con_fo) + " de " + (IND.atributos - IND.criticos),
        pct(IND.con_fo - IND.criticos_con_fo, IND.atributos - IND.criticos)],
    ].map(([e, n, p]) => el("div", {clase:"kpi"}, [el("b", {texto:n}), el("span", {texto: e + " · " + p})])));

    const filasPuntaje = evaluados.slice().sort((a, b) => a.indicadores.puntaje - b.indicadores.puntaje || a.codigo.localeCompare(b.codigo))
      .concat(doms.filter(x => x.indicadores.puntaje === null)).map(x => {
        const i = x.indicadores;
        return {etiqueta:x.codigo, sub:x.nombre, valor:i.puntaje, onclick: verDominio(x.codigo),
          texto: i.puntaje === null ? "sin atributos" : String(i.puntaje),
          tip: [[i.puntaje + " de 100", x.codigo + " · " + x.nombre], ["−" + i.penalizacion, "puntos por atributos sin fuente"],
                [i.con_fo + " de " + i.atributos, "atributos con fuente oficial"]]};
      });
    const tarjetaPuntaje = tarjeta("Puntaje de fuente oficial por dominio (de menor a mayor)", null, conTabla(
      barrasH(filasPuntaje, 100, [0, 25, 50, 75, 100]),
      [["codigo","Dominio"], ["nombre","Nombre"], ["puntaje","Puntaje"], ["penalizacion","Puntos restados"], ["cobertura","Con fuente oficial"]],
      filasPuntaje.map(f => { const x = porCodigo.get(f.etiqueta), i = x.indicadores;
        return {codigo:x.codigo, nombre:x.nombre, puntaje: i.puntaje === null ? "sin atributos" : String(i.puntaje),
                penalizacion: String(i.penalizacion), cobertura: i.con_fo + " de " + i.atributos}; })));

    // conteos con/sin fuente por criticidad: dos graficos con la MISMA escala (multiplos pequenos)
    const maxCrit = Math.max(1, ...evaluados.map(x => Math.max(x.indicadores.criticos, x.indicadores.no_criticos)));
    const filasCrit = campo => evaluados.map(x => { const i = x.indicadores, tot = i[campo], con = i[campo + "_con_fo"];
      return {etiqueta:x.codigo, sub:x.nombre, partes:[con, tot - con], onclick: verDominio(x.codigo)}; });
    const tablaCrit = evaluados.map(x => { const i = x.indicadores; return {codigo:x.codigo,
      criticos:String(i.criticos), criticos_con:String(i.criticos_con_fo), no_criticos:String(i.no_criticos),
      no_criticos_con:String(i.no_criticos_con_fo), total:String(i.atributos), con:String(i.con_fo)}; });
    const tarjetaCrit = tarjeta("Atributos con y sin fuente oficial, por criticidad", null, conTabla(
      el("div", {clase:"dos-columnas"}, [
        el("div", null, [el("h4", {clase:"titulo-multiplo", texto:"Críticos"}), barrasApiladas(SERIES_FO, filasCrit("criticos"), maxCrit)]),
        el("div", null, [el("h4", {clase:"titulo-multiplo", texto:"No críticos"}), barrasApiladas(SERIES_FO, filasCrit("no_criticos"), maxCrit)]),
      ]),
      [["codigo","Dominio"], ["total","Atributos"], ["con","Con fuente oficial"], ["criticos","Críticos"],
       ["criticos_con","Críticos con fuente"], ["no_criticos","No críticos"], ["no_criticos_con","No críticos con fuente"]], tablaCrit));

    const n = doms.length;
    const tarjetaCumple = tarjeta("Cumplimiento por dominio", null, [
      el("div", {clase:"medidores"}, [
        medidor("Estructura completa (roles " + IND.roles_requeridos.map(r => r.id).join(", ") + ")", IND.dominios_estructura_completa, n,
          "dominios con " + IND.roles_requeridos.map(r => r.nombre).join(", ")),
        medidor("Con fuentes de consumo", IND.dominios_con_consumo, n),
        medidor("Con respaldos", IND.dominios_con_respaldos, n),
        medidor("Con términos en el glosario", IND.dominios_con_terminos, n),
      ]),
      el("div", {clase:"tabla-wrap"}, el("table", {clase:"matriz"}, [
        el("thead", null, el("tr", null, ["Dominio","Puntaje","Estructura completa","Fuentes de consumo","Respaldos","Glosario","Vínculos"]
          .map(c => el("th", {texto:c})))),
        el("tbody", null, doms.map(x => { const i = x.indicadores;
          return el("tr", {clase:"clic", onclick: verDominio(x.codigo), title:"Ver indicadores de " + x.codigo}, [
            el("td", null, [el("b", {texto:x.codigo}), " ", el("span", {clase:"nota", texto:x.nombre})]),
            el("td", {texto: i.puntaje === null ? "—" : String(i.puntaje)}),
            el("td", null, chipCumple(i.estructura_completa, i.estructura_completa ? "Completa" :
              "Falta " + i.roles_faltantes.length, i.roles_faltantes.map(nombreRol).join(", "))),
            el("td", null, chipCumple(i.tiene_consumo, i.tiene_consumo ? i.fuentes_consumo + " fuentes" : "No")),
            el("td", null, chipCumple(i.respaldos > 0, i.respaldos ? String(i.respaldos) : "No")),
            el("td", null, chipCumple(i.terminos > 0, i.terminos ? i.terminos + " términos" : "No")),
            el("td", {texto: i.vinculos_resueltos === null ? "—" : i.vinculos_resueltos + " de " + (i.vinculos_resueltos + i.vinculos_pendientes)}),
          ]); })),
      ])),
    ]);

    const conVinc = doms.filter(x => x.indicadores.vinculos_resueltos !== null && (x.indicadores.vinculos_resueltos + x.indicadores.vinculos_pendientes));
    const tarjetaVinc = REL ? tarjeta("Vínculos Compartido / Similar por dominio", null, conVinc.length ? conTabla(
      barrasApiladas(SERIES_VINC, conVinc.map(x => ({etiqueta:x.codigo, sub:x.nombre, onclick: verDominio(x.codigo),
        partes:[x.indicadores.vinculos_resueltos, x.indicadores.vinculos_pendientes]})),
        Math.max(1, ...conVinc.map(x => x.indicadores.vinculos_resueltos + x.indicadores.vinculos_pendientes))),
      [["codigo","Dominio"], ["res","Resueltos"], ["pen","Pendientes"]],
      conVinc.map(x => ({codigo:x.codigo, res:String(x.indicadores.vinculos_resueltos), pen:String(x.indicadores.vinculos_pendientes)})))
      : el("div", {clase:"vacio", texto:"Ningún atributo declara Compartido / Similar."})) : null;

    return [cab, el("div", {clase:"fila-hero"}, [hero, kpis]), tarjetaPuntaje, tarjetaCrit, tarjetaCumple, tarjetaVinc];
  }

  function indicadoresDominio(d){
    const i = d.indicadores, P = IND.penalizacion;
    if (i.puntaje === null) return [cabeceraDominio(d), el("div", {clase:"avisos aviso-red",
      texto:"Este dominio no tiene atributos activos: no hay puntaje de fuente oficial."}), cumplimientoDominio(d)];
    const hero = el("section", {clase:"tarjeta"}, [el("h3", {texto:"Puntaje de fuente oficial"}), el("div", {clase:"tc hero"}, [
      el("div", {clase:"hero-num", texto:String(i.puntaje)}),
      el("div", {clase:"hero-sub", texto:"de 100 · " + reglaTexto}),
      el("div", {clase:"medidor-pista grande"}, el("div", {clase:"medidor-relleno", style:"width:" + i.puntaje + "%"})),
    ])]);
    const detalle = el("div", {clase:"kpis"}, [
      ["Críticos con estructura sin fuente", i.detalle_penalizacion.critico_estructura, P.critico_estructura],
      ["Críticos sin fuente", i.detalle_penalizacion.critico, P.critico],
      ["Otros sin fuente", i.detalle_penalizacion.otro, P.otro],
    ].map(([e, x, p]) => el("div", {clase:"kpi"}, [el("b", {texto: x.puntos ? "−" + x.puntos : "0"}),
      el("span", {texto: e + ": " + x.n + " × " + p})])));

    const filas = [
      {etiqueta:"Críticos", partes:[i.criticos_con_fo, i.criticos - i.criticos_con_fo]},
      {etiqueta:"No críticos", partes:[i.no_criticos_con_fo, i.no_criticos - i.no_criticos_con_fo]},
    ];
    const tarjetaCrit = tarjeta("Atributos con y sin fuente oficial", i.atributos, conTabla(
      barrasApiladas(SERIES_FO, filas, Math.max(1, i.criticos, i.no_criticos)),
      [["etiqueta","Criticidad"], ["total","Atributos"], ["con","Con fuente oficial"], ["sin","Sin fuente oficial"]],
      filas.map(f => ({etiqueta:f.etiqueta, total:String(f.partes[0] + f.partes[1]), con:String(f.partes[0]), sin:String(f.partes[1])}))));

    const tarjetaSin = tarjeta("Atributos sin fuente oficial", i.sin_fuente.length, i.sin_fuente.length
      ? el("div", {clase:"tabla-wrap"}, el("table", null, [
          el("thead", null, el("tr", null, ["Atributo","Nombre","Criticidad","Estructura","Resta"].map(c => el("th", {texto:c})))),
          el("tbody", null, i.sin_fuente.map(x => el("tr", {clase:"clic", title:"Ver el atributo",
            onclick: () => { estado.seccion = "atributos"; estado.filtroAtributo = x.nombre_atributo; actualizar(); }}, [
            el("td", {texto: d.codigo + "-" + x.codigo_atributo}), el("td", {texto:x.nombre_atributo}),
            el("td", {texto:v(x.criticidad)}), el("td", {texto:v(x.estructura)}), el("td", {texto:"−" + x.penalizacion}),
          ])))]))
      : el("div", {clase:"vacio", texto:"Todos los atributos activos tienen fuente oficial."}));

    return [cabeceraDominio(d), el("div", {clase:"fila-hero"}, [hero, detalle]), tarjetaCrit, cumplimientoDominio(d), tarjetaSin];
  }

  function cumplimientoDominio(d){
    const i = d.indicadores;
    const filas = [
      ["Estructura completa", i.estructura_completa, i.estructura_completa
        ? "Tiene los roles " + IND.roles_requeridos.map(r => r.nombre).join(", ") + "."
        : "Falta: " + i.roles_faltantes.map(nombreRol).join(", ") + "."],
      ["Fuentes de consumo", i.tiene_consumo, i.tiene_consumo
        ? i.fuentes_consumo + " fuente(s) en " + i.atributos_con_consumo + " atributo(s)." : "Ningún atributo tiene fuente de consumo."],
      ["Respaldos", i.respaldos > 0, i.respaldos ? i.respaldos + " respaldo(s) registrados." : "Sin respaldos."],
      ["Glosario", i.terminos > 0, i.terminos ? i.terminos + " término(s)." : "Sin términos."],
    ];
    const vinc = i.vinculos_resueltos === null ? null : i.vinculos_resueltos + i.vinculos_pendientes;
    return tarjeta("Cumplimiento del dominio", null, [
      el("div", {clase:"lista-cumple"}, filas.map(([e, ok, txt]) => el("div", {clase:"item-cumple"}, [
        chipCumple(ok, ok ? "Sí" : "No"), el("b", {texto:e}), el("span", {clase:"nota", texto:txt})]))),
      vinc ? el("div", null, [el("h4", {clase:"titulo-multiplo", texto:"Vínculos Compartido / Similar"}),
        barrasApiladas(SERIES_VINC, [{etiqueta:d.codigo, partes:[i.vinculos_resueltos, i.vinculos_pendientes]}], vinc)]) : null,
    ]);
  }

  function pintarContenido(){
    const main = document.getElementById("contenido");
    const d = estado.dominio ? porCodigo.get(estado.dominio) : null;
    if (redActual){ redActual.destroy(); redActual = null; }
    despuesDePintar.length = 0;
    let nodos;
    ocultarTip();
    obsEtiquetas.disconnect();
    if (estado.seccion === "relaciones") nodos = vistaRelaciones(d);
    else if (estado.seccion === "indicadores") nodos = vistaIndicadores(d);
    else if (estado.seccion === "terminos") nodos = vistaTerminos(d);
    else if (!d) nodos = vistaResumen();
    else if (estado.seccion === "atributos") nodos = vistaAtributos(d);
    else if (estado.seccion === "investigacion") nodos = vistaInvestigacion(d);
    else nodos = vistaEstructura(d);
    main.replaceChildren(...[].concat(nodos).flat(Infinity).filter(Boolean));
    despuesDePintar.splice(0).forEach(f => f());
  }

  function guardarHash(){
    const h = "#" + estado.seccion + (estado.dominio ? "/" + encodeURIComponent(estado.dominio) : "");
    if (location.hash !== h) history.replaceState(null, "", h);
  }
  function leerHash(){
    const [s, dom] = location.hash.replace(/^#/, "").split("/");
    if (SECCIONES.some(x => x.id === s)) estado.seccion = s;
    const cod = dom ? decodeURIComponent(dom) : null;
    estado.dominio = cod && porCodigo.has(cod) ? cod : null;
  }
  function actualizar(){
    pintarSecciones(); pintarListaDominios(); pintarContenido(); guardarHash(); window.scrollTo(0, 0);
  }

  document.getElementById("filtroDominio").addEventListener("input", e => {
    estado.filtroDominio = e.target.value; pintarListaDominios();
  });
  window.addEventListener("hashchange", () => { leerHash(); actualizar(); });
  leerHash();
  actualizar();
})();
</script>
</body>
</html>
"""
