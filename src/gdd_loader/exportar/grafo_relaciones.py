"""
Grafo de relaciones entre dominios (atributos Compartido / Similar).

Dos pasos, ambos puros (sin base de datos), para poder probarlos con datos
sinteticos:

  construir_modelo(dominios, relaciones) -> dict
      Agrega las filas de gdd.v_relacion_atributo en nodos (dominios) y
      aristas (dominio_origen -> dominio_destino, por tipo de relacion).
      Solo los estados RESUELTO y DESTINO_INACTIVO generan arista: en ambos el
      dominio destino es conocido. El resto se informa como "calidad de
      vinculo" y NO se dibuja (no hay destino confiable).

  generar_html(modelo, generado_en) -> str
      Un unico archivo HTML autocontenido (vis-network incrustado, sin
      internet ni servidor). Solo muestra nombres de dominio y atributo, no
      valores de datos.

Semantica de la arista: el dominio ORIGEN usa el atributo; el DESTINO es el
dominio primario (dueno). Un dominio con muchas aristas de entrada es un
dominio "maestro" del que dependen otros.
"""

from __future__ import annotations

import json
from collections import defaultdict
from html import escape
from pathlib import Path

ESTADOS_ARISTA = ("RESUELTO", "DESTINO_INACTIVO")
SIN_TIPO = "Sin tipo"
RECURSOS = Path(__file__).parent / "recursos"
ARCHIVO_VIS = "vis-network-9.1.9.min.js"

DESCRIPCION_ESTADO = {
    "RESUELTO": "Vínculo completo, atributo primario activo",
    "DESTINO_INACTIVO": "Apunta a una versión dada de baja del atributo primario",
    "MISMO_DOMINIO": "El atributo primario está en el mismo dominio",
    "SIN_VINCULO": "Declara Compartido/Similar pero sin atributo/dominio primario resuelto",
    "VINCULO_ROTO": "El id guardado no existe en gdd.atributo o gdd.dominio_dato",
    "INCONSISTENTE": "El atributo primario pertenece a otro dominio que el declarado",
    "SIN_TIPO": "Tiene vínculo pero no tiene tipo Compartido/Similar",
}


def _txt(valor) -> str | None:
    if valor is None:
        return None
    s = str(valor).strip()
    return s or None


def _int(valor) -> int | None:
    return None if valor is None else int(valor)


def construir_modelo(dominios: list[dict], relaciones: list[dict]) -> dict:
    nodos: dict[int, dict] = {}
    for d in dominios:
        id_ = _int(d["id_dominio"])
        nodos[id_] = {
            "id": id_,
            "codigo": _txt(d.get("codigo_dominio")) or f"#{id_}",
            "nombre": _txt(d.get("nombre_dominio")) or "",
            "tipo_dominio": _txt(d.get("tipo_dominio")) or "",
            "n_atributos": int(d.get("n_atributos_activos") or 0),
            "desconocido": False,
        }

    def asegurar_nodo(id_, codigo, nombre):
        if id_ not in nodos:  # no deberia pasar (la vista hace JOIN), por robustez
            nodos[id_] = {"id": id_, "codigo": _txt(codigo) or f"#{id_}", "nombre": _txt(nombre) or "",
                          "tipo_dominio": "", "n_atributos": 0, "desconocido": True}

    aristas: dict[tuple, dict] = {}
    problemas: list[dict] = []
    por_estado: dict[str, int] = defaultdict(int)
    por_tipo: dict[str, int] = defaultdict(int)
    sin_vinculo_por_dominio: dict[int, int] = defaultdict(int)

    for r in relaciones:
        estado = _txt(r.get("estado_vinculo")) or "DESCONOCIDO"
        tipo = _txt(r.get("tipo_relacion")) or SIN_TIPO
        por_estado[estado] += 1
        por_tipo[tipo] += 1
        id_o = _int(r["id_dominio_origen"])
        asegurar_nodo(id_o, r.get("codigo_dominio_origen"), r.get("nombre_dominio_origen"))
        detalle = {
            "codigo_origen": f"{_txt(r.get('codigo_dominio_origen')) or ''}-{_txt(r.get('codigo_atributo_origen')) or ''}",
            "atributo_origen": _txt(r.get("nombre_atributo_origen")) or "",
            "dominio_origen": _txt(r.get("codigo_dominio_origen")) or "",
            "atributo_destino": _txt(r.get("nombre_atributo_destino")) or "",
            "dominio_destino": _txt(r.get("codigo_dominio_destino")) or "",
            "tipo": tipo,
            "estado": estado,
        }
        if estado in ESTADOS_ARISTA:
            id_d = _int(r["id_dominio_destino"])
            asegurar_nodo(id_d, r.get("codigo_dominio_destino"), r.get("nombre_dominio_destino"))
            clave = (id_o, id_d, tipo)
            a = aristas.get(clave)
            if a is None:
                a = aristas[clave] = {"origen": id_o, "destino": id_d, "tipo": tipo,
                                      "n": 0, "n_inactivo": 0, "atributos": []}
            a["n"] += 1
            a["n_inactivo"] += estado == "DESTINO_INACTIVO"
            a["atributos"].append(detalle)
        else:
            problemas.append(detalle)
            if estado == "SIN_VINCULO":
                sin_vinculo_por_dominio[id_o] += 1

    entrantes: dict[int, set] = defaultdict(set)
    salientes: dict[int, set] = defaultdict(set)
    for a in aristas.values():
        salientes[a["origen"]].add(a["destino"])
        entrantes[a["destino"]].add(a["origen"])
    for id_, n in nodos.items():
        n["dependientes"] = len(entrantes[id_])    # dominios que usan atributos de este
        n["dependencias"] = len(salientes[id_])    # dominios de los que este usa atributos
        n["sin_vinculo"] = sin_vinculo_por_dominio[id_]

    lista_aristas = sorted(aristas.values(), key=lambda a: (-a["n"], a["origen"], a["destino"], a["tipo"]))
    for i, a in enumerate(lista_aristas):
        a["id"] = i
        a["atributos"].sort(key=lambda x: x["codigo_origen"])
    prioridad = {e: i for i, e in enumerate(
        ("VINCULO_ROTO", "INCONSISTENTE", "SIN_VINCULO", "SIN_TIPO", "MISMO_DOMINIO"))}
    problemas.sort(key=lambda x: (prioridad.get(x["estado"], 99), x["codigo_origen"]))

    con_relacion = {a["origen"] for a in lista_aristas} | {a["destino"] for a in lista_aristas}
    return {
        "nodos": sorted(nodos.values(), key=lambda n: n["codigo"]),
        "aristas": lista_aristas,
        "problemas": problemas,
        "tipos": sorted(por_tipo),
        "descripcion_estado": DESCRIPCION_ESTADO,
        "resumen": {
            "n_dominios": len(nodos),
            "n_dominios_con_relacion": len(con_relacion),
            "n_aristas": len(lista_aristas),
            "n_pares_dominio": len({(a["origen"], a["destino"]) for a in lista_aristas}),
            "n_atributos_relacionados": len(relaciones),
            "n_atributos_en_grafo": sum(a["n"] for a in lista_aristas),
            "por_estado": dict(sorted(por_estado.items())),
            "por_tipo": dict(sorted(por_tipo.items())),
        },
    }


def _json_para_script(obj) -> str:
    # "</" dentro de un <script> cerraria la etiqueta: se escapa.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def generar_html(modelo: dict, generado_en: str, titulo: str = "Relaciones entre dominios") -> str:
    vis_js = (RECURSOS / ARCHIVO_VIS).read_text(encoding="utf-8")
    return (
        _PLANTILLA
        .replace("__TITULO__", escape(titulo))
        .replace("__GENERADO__", escape(generado_en))
        .replace("__VIS_JS__", vis_js)
        .replace("__DATOS__", _json_para_script(modelo))
    )


_PLANTILLA = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--txt:#1f2328;--sub:#57606a;--linea:#d8dee4;--acento:#0b5cad;
--compartido:#2463c7;--similar:#d07a12;--otro:#7d8590;--alerta:#b42318;--suave:#eef2f6}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
font:13px/1.45 "Segoe UI",Arial,sans-serif}
header{display:flex;align-items:baseline;gap:16px;padding:10px 16px;background:var(--panel);
border-bottom:1px solid var(--linea);flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:600}
header .gen{color:var(--sub);font-size:12px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
.chip{background:var(--suave);border-radius:12px;padding:2px 10px;font-size:12px;color:var(--sub)}
.chip b{color:var(--txt)}
.tabs{display:flex;gap:2px;padding:0 16px;background:var(--panel);border-bottom:1px solid var(--linea)}
.tab{padding:8px 14px;cursor:pointer;border-bottom:2px solid transparent;color:var(--sub);user-select:none}
.tab.activa{color:var(--txt);border-bottom-color:var(--acento);font-weight:600}
main{display:flex;height:calc(100% - 92px)}
aside{width:340px;min-width:340px;background:var(--panel);border-right:1px solid var(--linea);
overflow:auto;padding:12px 14px}
aside h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--sub);margin:14px 0 6px}
aside h2:first-child{margin-top:0}
#red{flex:1;position:relative;background:var(--panel)}
input[type=search]{width:100%;padding:6px 8px;border:1px solid var(--linea);border-radius:6px;font:inherit}
label{display:flex;align-items:center;gap:6px;margin:3px 0;cursor:pointer}
.sw{display:inline-block;width:18px;height:3px;border-radius:2px}
button{font:inherit;padding:5px 10px;border:1px solid var(--linea);background:var(--panel);
border-radius:6px;cursor:pointer}
button:hover{background:var(--suave)}
.fila-bot{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
#detalle{border-top:1px solid var(--linea);margin-top:14px;padding-top:8px}
#detalle .vacio{color:var(--sub)}
#detalle h3{margin:2px 0 2px;font-size:14px}
#detalle .meta{color:var(--sub);font-size:12px;margin-bottom:6px}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{border-bottom:1px solid var(--linea);padding:4px 6px;text-align:left;vertical-align:top}
th{background:var(--suave);font-weight:600;position:sticky;top:0}
td.n{text-align:right;white-space:nowrap}
tr.clic{cursor:pointer} tr.clic:hover td{background:var(--suave)}
.tag{display:inline-block;padding:0 6px;border-radius:8px;font-size:11px;background:var(--suave)}
.tag.alerta{background:#fde8e7;color:var(--alerta)}
#vista-calidad{flex:1;overflow:auto;padding:12px 16px;display:none}
#vista-calidad .barra{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
select{font:inherit;padding:4px 6px;border:1px solid var(--linea);border-radius:6px}
.nota{color:var(--sub);font-size:12px;margin:6px 0}
@media (max-width:760px){main{flex-direction:column;height:auto}aside{width:100%;min-width:0}
#red{height:70vh}}
</style>
</head>
<body>
<header>
  <h1>__TITULO__</h1><span class="gen">Generado: __GENERADO__ · fuente gdd.v_relacion_atributo</span>
  <div class="chips" id="chips"></div>
</header>
<div class="tabs">
  <div class="tab activa" data-v="grafo">Grafo</div>
  <div class="tab" data-v="calidad" id="tab-calidad">Calidad de vínculos</div>
</div>
<main>
  <aside id="panel">
    <h2>Buscar dominio</h2>
    <input type="search" id="buscar" placeholder="Código o nombre…" list="lista-dom">
    <datalist id="lista-dom"></datalist>
    <h2>Tipo de relación</h2>
    <div id="tipos"></div>
    <h2>Filtros</h2>
    <label>Mínimo de atributos por arista <input type="number" id="minimo" value="1" min="1" style="width:60px"></label>
    <label><input type="checkbox" id="solo-vecinos" checked> Al seleccionar, mostrar solo sus vecinos</label>
    <label><input type="checkbox" id="aislados"> Mostrar dominios sin relaciones</label>
    <label><input type="checkbox" id="etiquetas-arista"> Mostrar cantidad en aristas</label>
    <div class="fila-bot">
      <button id="ver-todo">Ver todo</button>
      <button id="reorganizar">Reorganizar</button>
      <button id="ajustar">Ajustar vista</button>
      <button id="csv">Descargar CSV</button>
    </div>
    <p class="nota">Flecha: el dominio de origen <b>usa</b> atributos del dominio destino (dueño).
    Tamaño del nodo: cuántos dominios dependen de él. Grosor: cantidad de atributos.</p>
    <div id="detalle"><span class="vacio">Haz clic en un dominio o una arista para ver el detalle.</span></div>
  </aside>
  <div id="red"></div>
  <section id="vista-calidad">
    <div class="barra">
      <b>Atributos relacionados que no se dibujan en el grafo</b>
      <select id="filtro-estado"></select>
      <span class="nota" id="desc-estado"></span>
    </div>
    <table><thead><tr><th>Atributo origen</th><th>Nombre</th><th>Tipo</th><th>Dominio destino</th>
    <th>Atributo destino</th><th>Estado</th></tr></thead><tbody id="tabla-calidad"></tbody></table>
  </section>
</main>
<script>__VIS_JS__</script>
<script>
"use strict";
const M = __DATOS__;
const COLOR_TIPO = {"Compartido":"#2463c7","Similar":"#d07a12"};
const colorTipo = t => COLOR_TIPO[t] || "#7d8590";
const nodoPorId = new Map(M.nodos.map(n => [n.id, n]));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// ---- resumen ----
const R = M.resumen;
const inact = R.por_estado.DESTINO_INACTIVO || 0;
document.getElementById("chips").innerHTML =
  `<span class="chip"><b>${R.n_dominios_con_relacion}</b> de ${R.n_dominios} dominios relacionados</span>` +
  `<span class="chip"><b>${R.n_pares_dominio}</b> pares de dominios</span>` +
  `<span class="chip"><b>${R.n_atributos_en_grafo}</b> de ${R.n_atributos_relacionados} atributos en el grafo</span>` +
  (inact ? `<span class="chip"><b>${inact}</b> a versión inactiva</span>` : "");
document.getElementById("tab-calidad").textContent = `Calidad de vínculos (${M.problemas.length})`;

// ---- controles ----
const tiposDiv = document.getElementById("tipos");
M.tipos.forEach(t => {
  const n = R.por_tipo[t] || 0;
  tiposDiv.insertAdjacentHTML("beforeend",
    `<label><input type="checkbox" class="tipo" value="${esc(t)}" checked>
     <span class="sw" style="background:${colorTipo(t)}"></span>${esc(t)} <span class="nota">(${n})</span></label>`);
});
document.getElementById("lista-dom").innerHTML =
  M.nodos.map(n => `<option value="${esc(n.codigo)} — ${esc(n.nombre)}">`).join("");

// ---- red ----
const nodos = new vis.DataSet();
const aristas = new vis.DataSet();
const red = new vis.Network(document.getElementById("red"), {nodes: nodos, edges: aristas}, {
  nodes: {shape: "dot", font: {size: 13, face: "Segoe UI, Arial", color: "#1f2328"},
          borderWidth: 1.5, color: {background: "#dbe6f4", border: "#0b5cad",
          highlight: {background: "#ffe7a8", border: "#9a6700"}}},
  edges: {arrows: {to: {enabled: true, scaleFactor: .6}}, smooth: {type: "continuous"},
          font: {size: 11, align: "middle", strokeWidth: 3}, selectionWidth: 2},
  physics: {solver: "forceAtlas2Based", stabilization: {iterations: 500},
            forceAtlas2Based: {gravitationalConstant: -70, springLength: 140, avoidOverlap: .4}},
  interaction: {hover: true, tooltipDelay: 150, multiselect: false}
});
red.on("stabilizationIterationsDone", () => red.setOptions({physics: false}));

let foco = null;   // dominio seleccionado (modo vecinos)
function tiposActivos(){ return new Set([...document.querySelectorAll(".tipo:checked")].map(x => x.value)); }

function aristasFiltradas(){
  const tipos = tiposActivos(), minimo = +document.getElementById("minimo").value || 1;
  return M.aristas.filter(a => tipos.has(a.tipo) && a.n >= minimo);
}
function aristasVisibles(){
  const base = aristasFiltradas();
  if (foco === null || !document.getElementById("solo-vecinos").checked) return base;
  return base.filter(a => a.origen === foco || a.destino === foco);
}

// Se agregan una sola vez; los filtros solo ocultan (hidden) para conservar posiciones.
nodos.add(M.nodos.map(n => ({
  id: n.id, label: n.codigo, value: 1 + n.dependientes,
  scaling: {min: 10, max: 46, label: {enabled: true, min: 12, max: 22}},
  title: `${n.codigo} — ${n.nombre}\nDependen de él: ${n.dependientes} dominio(s)\n` +
         `Usa atributos de: ${n.dependencias} dominio(s)\nAtributos activos: ${n.n_atributos}` +
         (n.sin_vinculo ? `\nSin vínculo resuelto: ${n.sin_vinculo}` : "")
})));
aristas.add(M.aristas.map(a => {
  const c = colorTipo(a.tipo);
  return {
    id: a.id, from: a.origen, to: a.destino, value: a.n, scaling: {min: 1, max: 9},
    color: {color: c, highlight: c, hover: c, opacity: .75}, dashes: a.n_inactivo === a.n,
    smooth: {type: a.tipo === "Similar" ? "curvedCCW" : "curvedCW", roundness: .15},
    title: `${nodoPorId.get(a.origen).codigo} → ${nodoPorId.get(a.destino).codigo}\n${a.tipo}: ${a.n} atributo(s)` +
           (a.n_inactivo ? `\n${a.n_inactivo} apuntan a versión inactiva` : "")
  };
}));

function redibujar(ajustar){
  const vis_ = aristasVisibles(), ids = new Set(vis_.map(a => a.id));
  const usados = new Set(); vis_.forEach(a => { usados.add(a.origen); usados.add(a.destino); });
  if (foco !== null) usados.add(foco);
  const enFoco = foco !== null && document.getElementById("solo-vecinos").checked;
  const conAislados = document.getElementById("aislados").checked && !enFoco;
  const etq = document.getElementById("etiquetas-arista").checked;
  nodos.update(M.nodos.map(n => ({id: n.id, hidden: !(conAislados || usados.has(n.id))})));
  aristas.update(M.aristas.map(a => ({id: a.id, hidden: !ids.has(a.id), label: etq ? String(a.n) : " "})));
  if (ajustar) red.fit({nodes: [...usados], animation: {duration: 400}});
}

// ---- detalle ----
const det = document.getElementById("detalle");
function tablaAtributos(lista){
  return `<table><thead><tr><th>Atributo</th><th>Usa (primario)</th><th>Tipo</th></tr></thead><tbody>` +
    lista.map(x => `<tr><td>${esc(x.codigo_origen)}<br><span class="nota">${esc(x.atributo_origen)}</span></td>
      <td>${esc(x.dominio_destino)}<br><span class="nota">${esc(x.atributo_destino)}</span>
      ${x.estado === "DESTINO_INACTIVO" ? '<span class="tag alerta">versión inactiva</span>' : ""}</td>
      <td>${esc(x.tipo)}</td></tr>`).join("") + `</tbody></table>`;
}
function filasVecinos(lista, campo, titulo){
  if (!lista.length) return "";
  const agg = new Map();
  lista.forEach(a => { const k = a[campo]; agg.set(k, (agg.get(k) || 0) + a.n); });
  const filas = [...agg].sort((x, y) => y[1] - x[1]).map(([id, n]) => {
    const v = nodoPorId.get(id);
    return `<tr class="clic" data-nodo="${id}"><td>${esc(v.codigo)}<br><span class="nota">${esc(v.nombre)}</span></td><td class="n">${n}</td></tr>`;
  }).join("");
  return `<h2>${titulo}</h2><table><thead><tr><th>Dominio</th><th>Atributos</th></tr></thead><tbody>${filas}</tbody></table>`;
}
function mostrarNodo(id){
  const n = nodoPorId.get(id), vis_ = aristasVisibles();
  const sal = vis_.filter(a => a.origen === id), ent = vis_.filter(a => a.destino === id);
  det.innerHTML = `<h3>${esc(n.codigo)} — ${esc(n.nombre)}</h3>
    <div class="meta">${esc(n.tipo_dominio || "Sin tipo de dominio")} · ${n.n_atributos} atributos activos` +
    (n.sin_vinculo ? ` · <span class="tag alerta">${n.sin_vinculo} sin vínculo resuelto</span>` : "") + `</div>` +
    filasVecinos(ent, "origen", "Dominios que usan sus atributos") +
    filasVecinos(sal, "destino", "Dominios de los que usa atributos") +
    (sal.length ? `<h2>Atributos que usa de otros dominios</h2>` + tablaAtributos(sal.flatMap(a => a.atributos)) : "") +
    (!sal.length && !ent.length ? `<p class="vacio">Sin relaciones con los filtros actuales.</p>` : "");
  det.querySelectorAll("tr[data-nodo]").forEach(tr => tr.onclick = () => enfocar(+tr.dataset.nodo));
}
function mostrarArista(id){
  const a = M.aristas[id], o = nodoPorId.get(a.origen), d = nodoPorId.get(a.destino);
  det.innerHTML = `<h3>${esc(o.codigo)} → ${esc(d.codigo)}</h3>
    <div class="meta">${esc(o.nombre)} usa ${a.n} atributo(s) ${esc(a.tipo.toLowerCase())}(s) de ${esc(d.nombre)}</div>` +
    tablaAtributos(a.atributos);
}
function enfocar(id){
  foco = id; redibujar(true); red.selectNodes([id]); mostrarNodo(id);
}
function verTodo(){
  foco = null; red.unselectAll(); redibujar(true);
  det.innerHTML = '<span class="vacio">Haz clic en un dominio o una arista para ver el detalle.</span>';
}
red.on("click", p => {
  if (p.nodes.length) enfocar(p.nodes[0]);
  else if (p.edges.length) mostrarArista(p.edges[0]);
});

document.getElementById("buscar").addEventListener("change", e => {
  const q = e.target.value.split(" — ")[0].trim().toLowerCase();
  if (!q) return;
  const n = M.nodos.find(x => x.codigo.toLowerCase() === q) ||
            M.nodos.find(x => x.nombre.toLowerCase().includes(q) || x.codigo.toLowerCase().includes(q));
  if (n) enfocar(n.id);
});
["minimo", "aislados", "etiquetas-arista", "solo-vecinos"].forEach(id =>
  document.getElementById(id).addEventListener("change", () => { redibujar(true); if (foco !== null) mostrarNodo(foco); }));
tiposDiv.addEventListener("change", () => { redibujar(true); if (foco !== null) mostrarNodo(foco); });
document.getElementById("ver-todo").onclick = verTodo;
document.getElementById("reorganizar").onclick = () => { red.setOptions({physics: true}); red.stabilize(500); };
document.getElementById("ajustar").onclick = () => redibujar(true);

// ---- CSV (todas las relaciones por atributo, incluidas las que no se dibujan) ----
document.getElementById("csv").onclick = () => {
  const filas = [["codigo_atributo_origen","atributo_origen","dominio_origen","tipo","dominio_destino","atributo_destino","estado"]];
  M.aristas.forEach(a => a.atributos.forEach(x => filas.push([x.codigo_origen, x.atributo_origen, x.dominio_origen, x.tipo, x.dominio_destino, x.atributo_destino, x.estado])));
  M.problemas.forEach(x => filas.push([x.codigo_origen, x.atributo_origen, x.dominio_origen, x.tipo, x.dominio_destino, x.atributo_destino, x.estado]));
  const csv = "﻿" + filas.map(f => f.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(";")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
  const a = Object.assign(document.createElement("a"), {href: url, download: "relaciones_dominios.csv"});
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};

// ---- calidad de vinculos ----
const selEstado = document.getElementById("filtro-estado");
const estados = [...new Set(M.problemas.map(p => p.estado))];   // ya vienen por prioridad
selEstado.innerHTML = `<option value="">Todos los estados (${M.problemas.length})</option>` +
  estados.map(e => `<option value="${esc(e)}">${esc(e)} (${M.problemas.filter(p => p.estado === e).length})</option>`).join("");
function pintarCalidad(){
  const e = selEstado.value;
  document.getElementById("desc-estado").textContent = e ? (M.descripcion_estado[e] || "") : "";
  const filas = M.problemas.filter(p => !e || p.estado === e);
  document.getElementById("tabla-calidad").innerHTML = filas.length ? filas.map(p =>
    `<tr><td>${esc(p.codigo_origen)}</td><td>${esc(p.atributo_origen)}</td><td>${esc(p.tipo)}</td>
     <td>${esc(p.dominio_destino)}</td><td>${esc(p.atributo_destino)}</td>
     <td><span class="tag ${p.estado === "MISMO_DOMINIO" ? "" : "alerta"}" title="${esc(M.descripcion_estado[p.estado] || "")}">${esc(p.estado)}</span></td></tr>`).join("")
    : `<tr><td colspan="6" class="nota">Sin registros.</td></tr>`;
}
selEstado.onchange = pintarCalidad;
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("activa", x === t));
  const g = t.dataset.v === "grafo";
  document.getElementById("panel").style.display = g ? "" : "none";
  document.getElementById("red").style.display = g ? "" : "none";
  document.getElementById("vista-calidad").style.display = g ? "none" : "block";
});

redibujar(false); pintarCalidad();
</script>
</body>
</html>
"""
