// Panel de evaluadores: pide /api/resumen con los filtros y dibuja todo.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, suf = "") => (v === null || v === undefined ? "—" : v + suf);
const FILTROS = ["desde", "hasta", "empresa", "edad", "fuente", "curso"];
const graficos = {};
let datos = null;

// ---------------------------------------------------------------- colores (claro / oscuro)
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const C = () => ({ azul: css("--azul"), verde: css("--verde"), rojo: css("--rojo"), ambar: css("--ambar"),
                   gris: css("--suave"), txt: css("--txt"), borde: css("--borde") });

function params() {
  const p = new URLSearchParams();
  for (const f of FILTROS) if ($("f-" + f).value) p.set(f, $("f-" + f).value);
  if ($("f-demo").checked) p.set("demo", "1");
  return p;
}

function guardarFiltros() {
  history.replaceState(null, "", "?" + params().toString());
  $("b-csv").href = P + "/exportar.csv?" + params().toString();
}

function leerFiltrosDeURL() {
  const p = new URLSearchParams(location.search);
  $("f-demo").checked = p.get("demo") === "1";
  return p;
}

async function cargar() {
  guardarFiltros();
  $("estado").hidden = false;
  $("estado").textContent = "Cargando…";
  let r;
  try {
    r = await fetch(P + "/api/resumen?" + params().toString(), { credentials: "same-origin" });
  } catch (e) {
    $("estado").textContent = "Sin conexión con el panel.";
    return;
  }
  if (r.redirected || r.status === 401 || r.status === 303) { location.href = P + "/login"; return; }
  if (!r.ok) { $("estado").textContent = (await r.json().catch(() => ({}))).detail || "Error al cargar."; return; }
  datos = await r.json();
  $("banner-demo").hidden = !$("f-demo").checked;
  llenarOpciones(datos.opciones);
  if (datos.vacio) {
    $("estado").textContent = "No hay evaluaciones con estos filtros.";
    $("contenido").hidden = true;
    return;
  }
  $("estado").hidden = true;
  $("contenido").hidden = false;
  dibujar(datos);
}

function llenarOpciones(o) {
  const llenar = (sel, items, primera) => {
    const actual = sel.value;
    sel.innerHTML = `<option value="">${primera}</option>` +
      items.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("");
    sel.value = items.some(([v]) => v === actual) ? actual : "";
  };
  llenar($("f-empresa"), o.empresas.map((e) => [e, e]), "Todos");
  llenar($("f-edad"), o.edades.map((e) => [e, e === "Sin dato" ? "Sin dato" : e + " años"]), "Todas");
  llenar($("f-fuente"), Object.entries(o.fuentes), "Las dos");
  llenar($("f-curso"), Object.entries(o.cursos || {}), "Todos");
  for (const f of ["desde", "hasta"]) { $("f-" + f).min = o.min || ""; $("f-" + f).max = o.max || ""; }
}

// ---------------------------------------------------------------- KPIs
function dibujarKpis(k) {
  const tarjetas = [
    ["Evaluaciones", k.evaluaciones, `${k.choferes} choferes`],
    ["Aprobación", num(k.aprobacion, "%"), "de las evaluaciones", k.aprobacion >= 70 ? "bien" : k.aprobacion >= 50 ? "medio" : "mal"],
    ["Choferes aprobados", num(k.choferes_aprobados, "%"), `${k.pendientes} sin aprobar todavía`],
    ["Nota promedio", num(k.puntaje, "%"), "se aprueba con 70%"],
    ["Aprueban al 1er intento", num(k.primer_intento, "%"), k.intentos_prom ? `${k.intentos_prom} intentos promedio hasta aprobar` : ""],
    ["Atención promedio", num(k.atencion, "%"), k.atencion === null ? "sin datos con cámara" : `${num(k.distracciones)} distracciones · ${num(k.minutos_estudio)} min de estudio`,
     k.atencion === null ? "" : k.atencion >= 80 ? "bien" : k.atencion >= 60 ? "medio" : "mal"],
    ["Edad promedio", num(k.edad_prom, " años"), `${num(k.con_edad, "%")} con fecha de nacimiento`],
    ["Con control de atención", num(k.con_camara, "%"), "de las evaluaciones"],
  ];
  $("kpis").innerHTML = tarjetas.map(([t, v, sub, cls]) =>
    `<div class="kpi ${cls || ""}"><div class="kpi-t">${t}</div><div class="kpi-v">${v}</div><div class="kpi-s">${esc(sub)}</div></div>`).join("");
}

// ---------------------------------------------------------------- gráficos
function grafico(id, config) {
  if (graficos[id]) graficos[id].destroy();
  const c = C();
  Chart.defaults.color = c.gris;
  Chart.defaults.borderColor = c.borde;
  Chart.defaults.font.family = "system-ui, sans-serif";
  config.options = Object.assign({ responsive: true, maintainAspectRatio: false,
                                   plugins: { legend: { labels: { boxWidth: 12 } } } }, config.options || {});
  graficos[id] = new Chart($(id), config);
}

const ejePct = { min: 0, max: 100, ticks: { callback: (v) => v + "%" } };

function dibujar(d) {
  const c = C();
  dibujarKpis(d.kpis);
  $("hallazgos").innerHTML = d.hallazgos.map((h) => `<li>${h}</li>`).join("");   // armado en el servidor, sin datos de usuario salvo nombres escapados por Python
  $("hallazgos-box").hidden = !d.hallazgos.length;

  grafico("g-tiempo", { type: "bar", data: { labels: d.tiempo.etiquetas, datasets: [
      { label: "Aprobadas", data: d.tiempo.aprobadas, backgroundColor: c.verde, stack: "s" },
      { label: "Desaprobadas", data: d.tiempo.evaluaciones.map((v, i) => v - d.tiempo.aprobadas[i]), backgroundColor: c.rojo, stack: "s" }] },
    options: { scales: { x: { stacked: true, title: { display: d.tiempo.semanal, text: "semana del…" } }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } } } });

  const emp = d.empresas;
  grafico("g-empresas", { type: "bar", data: { labels: emp.map((e) => e.nombre), datasets: [
      { label: "Aprobación %", data: emp.map((e) => e.aprobacion), backgroundColor: emp.map((e) => color(e.aprobacion)) }] },
    options: { indexAxis: "y", plugins: { legend: { display: false },
      tooltip: { callbacks: { afterLabel: (x) => `${emp[x.dataIndex].evaluaciones} evaluaciones · ${emp[x.dataIndex].choferes} choferes · nota prom. ${emp[x.dataIndex].puntaje}%` } } },
      scales: { x: ejePct } } });
  document.querySelector("#g-empresas").parentElement.style.height = Math.max(220, emp.length * 34 + 60) + "px";

  const ed = d.edades;
  grafico("g-edades", { data: { labels: ed.map((e) => e.nombre), datasets: [
      { type: "bar", label: "Choferes", data: ed.map((e) => e.choferes), backgroundColor: c.azul + "99", yAxisID: "y1" },
      { type: "line", label: "Aprobación %", data: ed.map((e) => e.aprobacion), borderColor: c.verde, backgroundColor: c.verde, yAxisID: "y" },
      { type: "line", label: "Atención %", data: ed.map((e) => e.atencion), borderColor: c.ambar, backgroundColor: c.ambar, yAxisID: "y", spanGaps: true }] },
    options: { scales: { y: Object.assign({ position: "left" }, ejePct),
                         y1: { position: "right", beginAtZero: true, grid: { display: false }, ticks: { precision: 0 } } } } });

  grafico("g-notas", { type: "bar", data: { labels: d.histograma.map((_, i) => i * 10 + "%"), datasets: [
      { label: "Evaluaciones", data: d.histograma, backgroundColor: d.histograma.map((_, i) => (i >= 7 ? c.verde : c.rojo)) }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } } });

  grafico("g-dispersion", { type: "scatter", data: { datasets: [
      { label: "Evaluación", data: d.dispersion, backgroundColor: c.azul + "88", pointRadius: 4 }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: (x) => `${x.raw.nombre}: atención ${x.raw.x}% · nota ${x.raw.y}%` } } },
      scales: { x: Object.assign({ title: { display: true, text: "atención" } }, ejePct), y: Object.assign({ title: { display: true, text: "nota" } }, ejePct) } } });

  grafico("g-atencion", { type: "bar", data: { labels: d.atencion.map((a) => a.nombre), datasets: [
      { label: "Aprobación %", data: d.atencion.map((a) => a.aprobacion), backgroundColor: d.atencion.map((a) => color(a.aprobacion)) }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { afterLabel: (x) => `${d.atencion[x.dataIndex].evaluaciones} evaluaciones` } } },
      scales: { y: ejePct, x: { title: { display: true, text: "atención durante el estudio" } } } } });

  grafico("g-fuentes", { type: "bar", data: { labels: d.fuentes.map((f) => f.nombre), datasets: [
      { label: "Aprobación %", data: d.fuentes.map((f) => f.aprobacion), backgroundColor: c.verde },
      { label: "Nota promedio %", data: d.fuentes.map((f) => f.puntaje), backgroundColor: c.azul }] },
    options: { scales: { y: ejePct } } });

  grafico("g-dias", { type: "bar", data: { labels: d.dias.etiquetas, datasets: [{ label: "Evaluaciones", data: d.dias.valores, backgroundColor: c.azul }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } } });
  grafico("g-horas", { type: "bar", data: { labels: d.horas.etiquetas, datasets: [{ label: "Evaluaciones", data: d.horas.valores, backgroundColor: c.azul }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } } });

  $("t-empresas").innerHTML = "<thead><tr><th>Transporte</th><th class=n>Choferes</th><th class=n>Evaluaciones</th><th class=n>Aprobación</th><th class=n>Nota prom.</th><th class=n>Atención prom.</th></tr></thead><tbody>" +
    emp.map((e) => `<tr><td><a href="#" data-empresa="${esc(e.nombre)}">${esc(e.nombre)}</a></td><td class=n>${e.choferes}</td><td class=n>${e.evaluaciones}</td>
      <td class=n>${barra(e.aprobacion)}</td><td class=n>${num(e.puntaje, "%")}</td><td class=n>${num(e.atencion, "%")}</td></tr>`).join("") + "</tbody>";

  $("t-preguntas").innerHTML = "<thead><tr><th>#</th><th>Pregunta</th><th class=n>Errores</th><th class=n title='Estimado: de las veces que salió, cuántas se respondió mal'>% de error*</th></tr></thead><tbody>" +
    d.preguntas.map((p, i) => `<tr><td>${i + 1}</td><td>${esc(p.pregunta)}</td><td class=n>${p.fallas}</td><td class=n>${p.pct === null ? "—" : barra(p.pct, true)}</td></tr>`).join("") +
    "</tbody>";

  $("alertas-box").hidden = !d.alertas.length;
  const tipos = { reprobados: "🔁 Varios intentos", atencion: "👀 Baja atención", estudio: "⏱️ Estudio muy corto" };
  $("t-alertas").innerHTML = "<thead><tr><th>Tipo</th><th>Chofer</th><th>Transporte</th><th>Detalle</th></tr></thead><tbody>" +
    d.alertas.map((a) => `<tr><td>${tipos[a.tipo]}</td><td><a href="#" data-dni="${esc(a.dni)}">${esc(a.nombre)}</a></td><td>${esc(a.empresa)}</td><td>${esc(a.texto)}</td></tr>`).join("") + "</tbody>";

  dibujarChoferes();
}

function color(pct) {
  const c = C();
  return pct === null ? c.gris : pct >= 70 ? c.verde : pct >= 50 ? c.ambar : c.rojo;
}

function barra(pct, invertido = false) {
  if (pct === null || pct === undefined) return "—";
  const col = invertido ? color(100 - pct) : color(pct);
  return `<span class="barra-pct"><span style="width:${pct}%;background:${col}"></span></span> ${pct}%`;
}

// ---------------------------------------------------------------- tabla de choferes
function dibujarChoferes() {
  const q = $("buscar").value.trim().toLowerCase();
  const estado = $("f-estado").value;
  const lista = datos.choferes.filter((c) =>
    (!q || c.nombre.toLowerCase().includes(q) || c.dni.includes(q)) &&
    (!estado || (estado === "si") === c.aprobado));
  const max = 300;
  $("t-choferes").innerHTML = "<thead><tr><th>Chofer</th><th>DNI</th><th>Transporte</th><th class=n>Edad</th><th class=n>Intentos</th><th>Estado</th><th class=n>Mejor nota</th><th class=n>Atención</th><th>Última</th></tr></thead><tbody>" +
    lista.slice(0, max).map((c) => `<tr><td><a href="#" data-dni="${esc(c.dni)}">${esc(c.nombre)}</a></td><td>${esc(c.dni)}</td><td>${esc(c.empresa)}</td>
      <td class=n>${num(c.edad)}</td><td class=n>${c.intentos}</td>
      <td>${c.aprobado ? `<span class="ok">✔ Aprobado</span>${c.intentos_hasta_aprobar > 1 ? ` <span class=suave>(al ${c.intentos_hasta_aprobar}º)</span>` : ""}` : '<span class="mal">✘ Pendiente</span>'}</td>
      <td class=n>${c.mejor}%</td><td class=n>${num(c.atencion, "%")}</td><td>${esc(c.ultima_fecha)}</td></tr>`).join("") + "</tbody>";
  $("choferes-pie").textContent = lista.length > max ? `Mostrando ${max} de ${lista.length}. Usá el buscador o los filtros.` : `${lista.length} choferes.`;
}

// ---------------------------------------------------------------- detalle de un chofer
async function verChofer(dni) {
  const p = new URLSearchParams();
  if ($("f-demo").checked) p.set("demo", "1");
  const r = await fetch(`${P}/api/chofer/${encodeURIComponent(dni)}?${p}`, { credentials: "same-origin" });
  if (!r.ok) return;
  const d = await r.json();
  const c = d.resumen;
  $("d-contenido").innerHTML = `<h2>${esc(c.nombre)}</h2>
    <p>DNI ${esc(c.dni)} · ${esc(c.empresa)} · Cel. ${esc(c.celular)}${c.edad !== null ? ` · ${c.edad} años` : ""}</p>
    <p>${c.aprobado ? `<span class="ok">✔ Aprobado</span> en el intento ${c.intentos_hasta_aprobar}` : '<span class="mal">✘ Todavía no aprobó</span>'} · mejor nota ${c.mejor}%${c.atencion !== null ? ` · atención promedio ${c.atencion}%` : ""}</p>` +
    d.evaluaciones.map((e) => `<div class="eval ${e.aprobado ? "eval-ok" : "eval-mal"}">
      <div><b>${esc(e.fecha)}</b> · ${esc(e.curso || "")} · ${esc(e.fuente)} · <b>${e.puntaje}%</b> (${e.correctas}/${e.total}) ${e.aprobado ? "✔" : "✘"}</div>
      ${e.atencion !== null ? `<div class="suave">Atención ${e.atencion}% · ${e.distracciones} distracciones · ${e.salidas} salidas de pantalla · ${num(e.minutos_estudio)} min de estudio</div>` : ""}
      ${e.errores.length ? `<details><summary>${e.errores.length} respuestas incorrectas</summary><ul>${e.errores.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
    </div>`).join("");
  $("d-chofer").showModal();
}

// ---------------------------------------------------------------- eventos
document.addEventListener("click", (ev) => {
  const a = ev.target.closest("a[data-dni], a[data-empresa]");
  if (!a) return;
  ev.preventDefault();
  if (a.dataset.dni) verChofer(a.dataset.dni);
  else { $("f-empresa").value = a.dataset.empresa; cargar(); window.scrollTo({ top: 0, behavior: "smooth" }); }
});
for (const f of FILTROS) $("f-" + f).addEventListener("change", cargar);
$("f-demo").addEventListener("change", () => { for (const f of FILTROS) $("f-" + f).value = ""; cargar(); });
$("b-limpiar").onclick = () => { for (const f of FILTROS) $("f-" + f).value = ""; cargar(); };
$("b-actualizar").onclick = async () => {
  await fetch(P + "/api/actualizar", { method: "POST", credentials: "same-origin" });
  cargar();
};
$("buscar").addEventListener("input", dibujarChoferes);
$("f-estado").addEventListener("change", dibujarChoferes);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => datos && !datos.vacio && dibujar(datos));

// Los filtros de la URL se aplican después de la primera carga (que llena las opciones de los select).
(async () => {
  const p = leerFiltrosDeURL();
  await cargar();
  let cambio = false;
  for (const f of FILTROS) if (p.get(f)) { $("f-" + f).value = p.get(f); cambio = true; }
  if (cambio) cargar();
})();
