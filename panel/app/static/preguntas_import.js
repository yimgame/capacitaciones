// Carga masiva de preguntas: lee una planilla (.xlsx/.csv) o texto pegado y devuelve [{pregunta, correcta, incorrectas}].
//
// Formatos que entiende:
//  1. Texto: "1. ¿Pregunta? a) opción b) opción c) opción" (en una celda/línea o repartido en varias),
//     con títulos de tema sueltos en el medio (se ignoran). La respuesta sale de:
//       - una hoja/tabla de clave con pares Nº | letra (ej. "Preg. | Resp. | Preg. | Resp."),
//       - o en el mismo texto: "Respuesta: b" / "Correcta: b",
//       - o marcando la opción correcta con * (ej. "*b) 80 km/h").
//  2. Tabla con títulos: Pregunta | Correcta | Incorrecta 1 | Incorrecta 2 ...
//     o Pregunta | A | B | C | D | Respuesta (letra).
(function (raiz) {
  const LETRAS = "abcdefgh";
  const norm = (t) => String(t ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const limpio = (t) => String(t ?? "").replace(/\s+/g, " ").trim();
  const RE_NUM = /^\s*(\d{1,4})\s*[.)\-–:]\s*(.*)$/s;
  const RE_RESP = /\b(?:respuesta(?:\s+correcta)?|correcta|clave)\s*[:=]\s*\(?([a-h])\)?(?![a-z])/i;

  // Separa "¿texto? a) uno b) dos c) tres" en enunciado + opciones (las letras tienen que ir en orden a, b, c...).
  function separarOpciones(texto) {
    const re = /(^|[\s(])(\*?)([a-hA-H])\s*[).]\s+/g;
    const marcas = [];
    let m, esperada = 0;
    while ((m = re.exec(texto))) {
      if (m[3].toLowerCase() === LETRAS[esperada]) {
        marcas.push({ ini: m.index + m[1].length, fin: re.lastIndex, letra: m[3].toLowerCase(), estrella: !!m[2] });
        esperada++;
      }
    }
    if (marcas.length < 2) return null;
    const opciones = marcas.map((mk, i) => {
      let t = texto.slice(mk.fin, i + 1 < marcas.length ? marcas[i + 1].ini : texto.length);
      let estrella = mk.estrella;
      if (/\(\s*correcta\s*\)|\*\s*$/i.test(t)) { estrella = true; t = t.replace(/\(\s*correcta\s*\)|\*\s*$/gi, ""); }
      return { letra: mk.letra, texto: limpio(t), estrella };
    });
    return { enunciado: limpio(texto.slice(0, marcas[0].ini)), opciones };
  }

  function armar(n, bloque, clave, errores) {
    let texto = bloque;
    let letra = null;
    const r = texto.match(RE_RESP);
    if (r) { letra = r[1].toLowerCase(); texto = texto.replace(RE_RESP, " "); }
    const sep = separarOpciones(texto);
    const etiqueta = n ? `Pregunta ${n}` : `«${limpio(bloque).slice(0, 40)}…»`;
    if (!sep) { errores.push(`${etiqueta}: no encuentro las opciones a), b), c)…`); return null; }
    const marcada = sep.opciones.find((o) => o.estrella);
    letra = (n && clave[n]) || letra || (marcada && marcada.letra);
    if (!letra) { errores.push(`${etiqueta}: falta la respuesta correcta (en la clave, "Respuesta: x" o marcada con *)`); return null; }
    const correcta = sep.opciones.find((o) => o.letra === letra);
    if (!correcta) { errores.push(`${etiqueta}: la respuesta «${letra}» no es una de sus opciones`); return null; }
    const incorrectas = sep.opciones.filter((o) => o !== correcta).map((o) => o.texto).filter(Boolean);
    if (!sep.enunciado) { errores.push(`${etiqueta}: falta el texto de la pregunta`); return null; }
    if (incorrectas.length > 5) { errores.push(`${etiqueta}: tiene más de 6 opciones (máximo 6)`); return null; }
    return { pregunta: sep.enunciado, correcta: correcta.texto, incorrectas };
  }

  // Lista de líneas/celdas de texto + clave {n: letra} -> preguntas
  function desdeTexto(lineas, clave = {}) {
    const preguntas = [], errores = [], temas = [];
    let actual = null;
    const cerrar = () => { if (actual) { const q = armar(actual.n, actual.texto, clave, errores); if (q) preguntas.push(q); } actual = null; };
    for (const crudo of lineas) {
      const linea = String(crudo ?? "").trim();
      if (!linea) continue;
      const m = linea.match(RE_NUM);
      const tieneOpciones = !!separarOpciones(actual ? actual.texto : "");
      if (m && !/^[a-h]\s*[).]/i.test(linea)) {
        cerrar();
        actual = { n: Number(m[1]), texto: m[2] };
      } else if (actual && (/^\*?[a-h]\s*[).]\s/i.test(linea) || RE_RESP.test(linea) || !tieneOpciones)) {
        actual.texto += " " + linea;               // opción, respuesta o continuación del enunciado
      } else {
        cerrar();
        temas.push(linea);                         // título de tema u otra línea suelta
      }
    }
    cerrar();
    return { preguntas, errores, temas };
  }

  // Filas (arrays) de una hoja de clave: pares Nº | letra en cualquier columna.
  function claveDeFilas(filas) {
    const clave = {};
    let pares = 0;
    for (const f of filas) {
      for (let i = 0; i + 1 < f.length; i++) {
        const n = Number(String(f[i]).replace(",", "."));
        const l = String(f[i + 1] ?? "").trim().toLowerCase().replace(/[).]/g, "");
        if (Number.isInteger(n) && n > 0 && /^[a-h]$/.test(l)) { clave[n] = l; pares++; i++; }
      }
    }
    return pares >= 3 ? clave : null;
  }

  // Hoja en columnas con títulos.
  function desdeTabla(filas) {
    const iTit = filas.findIndex((f) => f.some((c) => norm(c) === "pregunta" || norm(c) === "enunciado"));
    if (iTit < 0) return null;
    const tit = filas[iTit].map(norm);
    const col = (pred) => tit.findIndex(pred);
    const cP = col((t) => t === "pregunta" || t === "enunciado");
    const cCorrecta = col((t) => t === "correcta" || t === "respuestacorrecta" || t === "opcioncorrecta");
    const cLetra = col((t) => ["respuesta", "clave", "letra", "respuestaletra"].includes(t));
    const cInc = tit.map((t, i) => (t.startsWith("incorrecta") ? i : -1)).filter((i) => i >= 0);
    const cOps = tit.map((t, i) => (/^(opcion)?[a-h]$|^opcion\d$/.test(t) ? i : -1)).filter((i) => i >= 0);
    if (cCorrecta < 0 && cLetra < 0) return null;
    const preguntas = [], errores = [];
    filas.slice(iTit + 1).forEach((f, k) => {
      const nfila = iTit + k + 2;
      const p = limpio(f[cP]);
      if (!p) return;
      let correcta = cCorrecta >= 0 ? limpio(f[cCorrecta]) : "";
      let incorrectas = cInc.map((i) => limpio(f[i])).filter(Boolean);
      const ops = cOps.map((i) => limpio(f[i]));
      const letra = cLetra >= 0 ? limpio(f[cLetra]).toLowerCase().replace(/[).]/g, "") : (/^[a-h]$/i.test(correcta) && ops.length ? correcta.toLowerCase() : "");
      if (ops.some(Boolean) && letra) {
        const i = LETRAS.indexOf(letra);
        if (i < 0 || !ops[i]) { errores.push(`Fila ${nfila}: la respuesta «${letra}» no corresponde a ninguna opción`); return; }
        correcta = ops[i];
        incorrectas = ops.filter((o, j) => o && j !== i);
      }
      if (!correcta) { errores.push(`Fila ${nfila}: falta la respuesta correcta`); return; }
      if (!incorrectas.length) { errores.push(`Fila ${nfila}: faltan las respuestas incorrectas`); return; }
      if (incorrectas.length > 5) { errores.push(`Fila ${nfila}: máximo 6 opciones`); return; }
      preguntas.push({ pregunta: p, correcta, incorrectas });
    });
    return { preguntas, errores, temas: [] };
  }

  // Libro entero (hojas como arrays de filas): busca tabla, clave y texto.
  function desdeLibro(hojas) {
    for (const h of hojas) { const t = desdeTabla(h.filas); if (t && (t.preguntas.length || t.errores.length)) return { ...t, formato: `tabla (hoja «${h.nombre}»)` }; }
    let clave = {}, hojaClave = null;
    const lineas = [];
    for (const h of hojas) {
      const c = claveDeFilas(h.filas);
      if (c && !h.filas.some((f) => f.some((x) => RE_NUM.test(String(x)) && String(x).length > 25))) { Object.assign(clave, c); hojaClave = h.nombre; continue; }
      for (const f of h.filas) for (const x of f) if (String(x ?? "").trim()) lineas.push(String(x));
    }
    const r = desdeTexto(lineas, clave);
    return { ...r, formato: hojaClave ? `texto + clave (hoja «${hojaClave}», ${Object.keys(clave).length} respuestas)` : "texto" };
  }

  // Texto pegado: las preguntas y, opcionalmente, una clave al final ("1 a, 2 b, 3 c" o "1-a 2-b").
  function desdePegado(texto) {
    const clave = {};
    const m = texto.match(/\n\s*(?:clave|respuestas)\s*:?\s*\n?([\s\S]*)$/i);
    let cuerpo = texto;
    if (m) {
      for (const p of m[1].matchAll(/(\d{1,4})\s*[.)\-:=]?\s*([a-h])\b/gi)) clave[Number(p[1])] = p[2].toLowerCase();
      cuerpo = texto.slice(0, m.index);
    }
    return { ...desdeTexto(cuerpo.split(/\r?\n/), clave), formato: Object.keys(clave).length ? "texto pegado + clave" : "texto pegado" };
  }

  const api = { desdeLibro, desdePegado, desdeTexto, desdeTabla, claveDeFilas, separarOpciones };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else raiz.ImportPreguntas = api;
})(typeof window !== "undefined" ? window : globalThis);
