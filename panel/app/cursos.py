"""Cursos de capacitación: configuración editable desde el panel y servida a la página pública.

Cada curso tiene su página en https://<dominio>/<slug> (web/curso.html la arma con este JSON).
Los videos y archivos se guardan en RECURSOS/<slug>/ (carpeta del NAS, nginx la publica en /recursos/).
"""
import json
import os
from datetime import date
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DB = os.environ.get("PANEL_DB", "/data/panel.db")
RECURSOS = Path(os.environ.get("RECURSOS_DIR", "/recursos"))
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
RESERVADOS = {"panel", "api", "recursos", "static", "index", "curso", "favicon.ico"}
EXTENSIONES = {".mp4": "video", ".webm": "video", ".m4v": "video", ".mov": "video",
               ".pdf": "documento", ".jpg": "imagen", ".jpeg": "imagen", ".png": "imagen", ".webp": "imagen"}
ACCESO = {"abierto": "Abierto: lo puede hacer cualquiera desde la portada",
          "asignados": "Solo choferes asignados (entran con DNI y fecha de nacimiento)"}
VIGENCIA_FECHA = -1          # vigencia_meses = -1: vence en una fecha fija (vence_fecha)
VIGENCIA = {0: "No vence", 6: "Cada 6 meses", 12: "Anual (cada 12 meses)", 24: "Cada 2 años", 36: "Cada 3 años",
            VIGENCIA_FECHA: "Hasta una fecha específica"}
ATENCION = {"elegir": "El chofer elige: con o sin cámara",
            "camara": "Solo con control de atención (cámara)",
            "sin": "Solo sin cámara"}

# Material del curso actual (Ley 24.449), el que estaba fijo en web/index.html hasta 2026-10-28.
MATERIAL_LEY = [
    {"titulo": "Antes de salir", "items": [
        "Verificá que vos y el vehículo estén en condiciones de seguridad.",
        "Llevá licencia habilitante, cédula del vehículo, comprobante de seguro vigente y VTV cuando corresponda.",
        "El automóvil debe tener matafuego y balizas portátiles normalizadas."]},
    {"titulo": "Licencias", "items": [
        "Edad mínima para automóviles: 17 años.",
        "Clase D: vehículos de transporte de pasajeros, emergencia y seguridad.",
        "Nadie puede retener tu licencia o documentación, salvo los casos previstos por la ley o un juez."]},
    {"titulo": "Señales", "items": [
        "Reglamentarias: de cumplimiento obligatorio (prohibición, prioridad, restricción, fin de prescripción).",
        "Preventivas: advierten un peligro o cambio en la vía.",
        "Horizontales: marcas pintadas sobre la calzada. Vallas, conos y tambores son señalamiento transitorio."]},
    {"titulo": "Al conducir", "items": [
        "Toda maniobra se avisa antes y se hace con precaución, sin crear riesgo.",
        "Si al cruzar una intersección ponés en peligro a un peatón, tenés que detenerte.",
        "En un paso a nivel, cuando empieza a bajar la barrera equivale a la luz amarilla del semáforo.",
        "Luz alta: solo en zona rural y autopistas, con poca luz natural y sin encandilar."]},
    {"titulo": "Velocidades y estacionamiento", "items": [
        "Cerca de escuelas la velocidad precautoria es de 20 km/h como máximo.",
        "En zona rural se estaciona lo más lejos posible de la calzada y la banquina.",
        "Por la banquina solo se circula en casos de emergencia."]},
]


def _base(slug, titulo, descripcion, orden):
    return {"slug": slug, "titulo": titulo, "descripcion": descripcion, "habilitado": True, "orden": orden,
            "material": json.loads(json.dumps(MATERIAL_LEY)), "videos": [], "min_segundos": 60,
            "atencion": "elegir", "mostrar_camara": True, "examen": True,
            "preguntas": [], "preguntas_por_examen": 10, "aprobacion": 70, "acceso": "abierto", "vigencia_meses": 0}


INICIALES = [
    _base("cursomanejo", "Curso de manejo",
          "Material de estudio sobre la Ley Nacional de Tránsito 24.449 y evaluación de 10 preguntas.", 1),
    _base("seguridadvial", "Seguridad vial",
          "Capacitación de concientización en seguridad vial para choferes.", 2),
]


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def iniciar():
    with closing(con()) as c, c:
        c.execute("CREATE TABLE IF NOT EXISTS cursos (slug TEXT PRIMARY KEY, datos TEXT NOT NULL, actualizado REAL)")
        for curso in INICIALES:
            c.execute("INSERT OR IGNORE INTO cursos VALUES (?, ?, ?)",
                      (curso["slug"], json.dumps(curso, ensure_ascii=False), time.time()))
    RECURSOS.mkdir(parents=True, exist_ok=True)


def listar(solo_habilitados=False):
    with closing(con()) as c:
        cursos = [json.loads(r["datos"]) for r in c.execute("SELECT datos FROM cursos")]
    cursos.sort(key=lambda x: (x.get("orden", 99), x["titulo"].lower()))
    return [x for x in cursos if x.get("habilitado")] if solo_habilitados else cursos


def obtener(slug):
    with closing(con()) as c:
        r = c.execute("SELECT datos FROM cursos WHERE slug=?", (slug,)).fetchone()
    return json.loads(r["datos"]) if r else None


def guardar(curso):
    with closing(con()) as c, c:
        c.execute("INSERT OR REPLACE INTO cursos VALUES (?, ?, ?)",
                  (curso["slug"], json.dumps(curso, ensure_ascii=False), time.time()))


def crear(slug, titulo, copiar_de=""):
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug) or slug in RESERVADOS:
        raise ValueError("La dirección tiene que ser de 2 a 40 letras minúsculas, números o guiones (ej. manejo-nocturno).")
    if obtener(slug):
        raise ValueError(f"Ya existe un curso con la dirección /{slug}.")
    base = obtener(copiar_de) if copiar_de else None
    curso = dict(base) if base else _base(slug, titulo or slug, "", 99)
    curso.update(slug=slug, titulo=(titulo or curso["titulo"]).strip()[:120], videos=[] if not base else
                 [dict(v) for v in base.get("videos", []) if v.get("tipo") == "youtube"], habilitado=False,
                 orden=len(listar()) + 1)
    guardar(curso)
    return curso


# ------------------------------------------------------------------ material como texto
def material_a_texto(material):
    """[{titulo, items}] -> texto editable: '# Título' y una línea por punto."""
    partes = []
    for s in material:
        partes.append("# " + s["titulo"])
        partes += ["- " + i for i in s["items"]]
        partes.append("")
    return "\n".join(partes).strip()


def texto_a_material(texto):
    secciones, actual = [], None
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith("#"):
            actual = {"titulo": linea.lstrip("#").strip()[:120] or "Sin título", "items": []}
            secciones.append(actual)
            continue
        if actual is None:
            actual = {"titulo": "Material", "items": []}
            secciones.append(actual)
        actual["items"].append(re.sub(r"^[-*•]\s*", "", linea)[:600])
    return secciones


def actualizar(slug, f):
    """Aplica los campos del formulario del editor."""
    curso = obtener(slug)
    if not curso:
        raise ValueError("No existe ese curso.")
    curso["titulo"] = (f.get("titulo") or curso["titulo"]).strip()[:120]
    curso["descripcion"] = (f.get("descripcion") or "").strip()[:1000]
    curso["habilitado"] = f.get("habilitado") == "1"
    curso["mostrar_camara"] = f.get("mostrar_camara") == "1"
    curso["examen"] = f.get("examen") == "1"
    curso["atencion"] = f.get("atencion") if f.get("atencion") in ATENCION else "elegir"
    curso["acceso"] = f.get("acceso") if f.get("acceso") in ACCESO else "abierto"
    try:
        curso["vigencia_meses"] = max(VIGENCIA_FECHA, min(120, int(f.get("vigencia_meses") or 0)))
    except ValueError:
        pass
    if curso.get("vigencia_meses") == VIGENCIA_FECHA:
        try:
            curso["vence_fecha"] = date.fromisoformat(str(f.get("vence_fecha") or "")).isoformat()
        except ValueError:
            raise ValueError("Elegí la fecha de vencimiento del curso.")
    try:
        curso["min_segundos"] = max(0, min(7200, int(f.get("min_segundos") or 0)))
    except ValueError:
        pass
    try:
        curso["orden"] = int(f.get("orden") or curso.get("orden", 99))
    except ValueError:
        pass
    curso["material"] = texto_a_material(f.get("material", ""))
    # títulos y orden de los videos (v_titulo_<id>, v_orden_<id>)
    for v in curso["videos"]:
        t = f.get(f"v_titulo_{v['id']}")
        if t is not None:
            v["titulo"] = t.strip()[:120]
        try:
            v["orden"] = int(f.get(f"v_orden_{v['id']}") or v.get("orden", 0))
        except ValueError:
            pass
    curso["videos"].sort(key=lambda v: v.get("orden", 0))
    guardar(curso)
    return curso


# ------------------------------------------------------------------ recursos (videos y archivos)
def nombre_seguro(nombre):
    base, ext = os.path.splitext(os.path.basename(nombre or ""))
    ext = ext.lower()
    if ext not in EXTENSIONES:
        raise ValueError("Formato no permitido. Videos: mp4, webm, m4v, mov. También pdf, jpg, png, webp.")
    import unicodedata
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:60] or "archivo"
    return base + ext


def agregar_recurso(slug, archivo, titulo):
    curso = obtener(slug)
    ruta = f"{slug}/{archivo}"
    if not any(v.get("archivo") == ruta for v in curso["videos"]):
        curso["videos"].append({"id": f"{int(time.time() * 1000):x}", "tipo": EXTENSIONES[os.path.splitext(archivo)[1]],
                                "titulo": titulo or os.path.splitext(archivo)[0].replace("-", " ").capitalize(),
                                "archivo": ruta, "orden": len(curso["videos"]) + 1})
        guardar(curso)
    return curso


def agregar_youtube(slug, url, titulo):
    m = re.search(r"(?:youtu\.be/|v=|embed/|shorts/)([A-Za-z0-9_-]{11})", url or "") or \
        re.fullmatch(r"([A-Za-z0-9_-]{11})", (url or "").strip())
    if not m:
        raise ValueError("No reconozco ese link de YouTube.")
    curso = obtener(slug)
    curso["videos"].append({"id": f"{int(time.time() * 1000):x}", "tipo": "youtube", "youtube": m.group(1),
                            "titulo": (titulo or "Video").strip()[:120], "orden": len(curso["videos"]) + 1})
    guardar(curso)
    return curso


def quitar_recurso(slug, vid):
    curso = obtener(slug)
    sacado = next((v for v in curso["videos"] if v["id"] == vid), None)
    curso["videos"] = [v for v in curso["videos"] if v["id"] != vid]
    guardar(curso)
    if sacado and sacado.get("archivo"):
        # el archivo se borra solo si ningún otro curso lo usa
        en_uso = any(v.get("archivo") == sacado["archivo"] for c in listar() for v in c.get("videos", []))
        p = RECURSOS / sacado["archivo"]
        if not en_uso and p.is_file() and RECURSOS.resolve() in p.resolve().parents:
            p.unlink()
    return curso


def publico(curso):
    """Lo que ve la página del chofer (sin campos internos)."""
    return {k: curso[k] for k in ("slug", "titulo", "descripcion", "material", "min_segundos", "atencion",
                                  "mostrar_camara", "examen")} | {
        "aprobacion": curso.get("aprobacion", APROBACION), "acceso": curso.get("acceso", "abierto"),
        "videos": [{"titulo": v["titulo"], "tipo": v["tipo"],
                    **({"youtube": v["youtube"]} if v["tipo"] == "youtube" else {"url": "/recursos/" + v["archivo"]})}
                   for v in curso.get("videos", [])]}


# ------------------------------------------------------------------ evaluación (preguntas propias del curso)
# Cada pregunta: {"id", "pregunta", "correcta", "incorrectas": [...]}. Un curso sin preguntas propias usa el banco
# de la Ley 24.449 (el de siempre). El examen lo arma y lo corrige examen.py.
BANCO_LEY = Path(__file__).resolve().parent / "banco_ley_24449.json"
APROBACION = 70
POR_EXAMEN = 10
MAX_PREGUNTAS = 500
MAX_INCORRECTAS = 5


def banco_ley():
    return json.loads(BANCO_LEY.read_text())


def limpiar_preguntas(lista):
    """Valida lo que manda el editor. Devuelve (preguntas, errores)."""
    if not isinstance(lista, list):
        return [], ["Formato inválido."]
    if len(lista) > MAX_PREGUNTAS:
        return [], [f"Máximo {MAX_PREGUNTAS} preguntas por curso."]
    salida, errores, vistas = [], [], set()
    for n, q in enumerate(lista, start=1):
        q = q if isinstance(q, dict) else {}
        texto = " ".join(str(q.get("pregunta") or "").split())[:500]
        correcta = " ".join(str(q.get("correcta") or "").split())[:300]
        incorrectas = []
        for x in q.get("incorrectas") or []:
            x = " ".join(str(x or "").split())[:300]
            if x and x != correcta and x not in incorrectas:
                incorrectas.append(x)
        if not texto and not correcta and not incorrectas:
            continue                                         # tarjeta vacía: se ignora
        if not texto:
            errores.append(f"Pregunta {n}: falta el texto de la pregunta.")
        if not correcta:
            errores.append(f"Pregunta {n}: falta la respuesta correcta.")
        if not incorrectas:
            errores.append(f"Pregunta {n}: tiene que tener al menos una respuesta incorrecta (distinta de la correcta).")
        if len(incorrectas) > MAX_INCORRECTAS:
            errores.append(f"Pregunta {n}: máximo {MAX_INCORRECTAS} respuestas incorrectas.")
        if texto.lower() in vistas:
            errores.append(f"Pregunta {n}: está repetida.")
        vistas.add(texto.lower())
        qid = str(q.get("id") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,40}", qid):
            qid = f"{int(time.time() * 1000):x}-{n}"
        salida.append({"id": qid, "pregunta": texto, "correcta": correcta, "incorrectas": incorrectas})
    return salida, errores


def guardar_evaluacion(slug, datos):
    curso = obtener(slug)
    if not curso:
        raise ValueError("No existe ese curso.")
    preguntas, errores = limpiar_preguntas(datos.get("preguntas"))
    try:
        por_examen = int(datos.get("preguntas_por_examen") or POR_EXAMEN)
        aprobacion = int(datos.get("aprobacion") or APROBACION)
    except (TypeError, ValueError):
        errores.append("Cantidad de preguntas y porcentaje de aprobación tienen que ser números.")
        por_examen, aprobacion = POR_EXAMEN, APROBACION
    if not 1 <= por_examen <= 50:
        errores.append("Preguntas por examen: entre 1 y 50.")
    if not 1 <= aprobacion <= 100:
        errores.append("Porcentaje para aprobar: entre 1 y 100.")
    if errores:
        raise ValueError(" ".join(errores[:8]) + (f" (y {len(errores) - 8} más)" if len(errores) > 8 else ""))
    curso.update(preguntas=preguntas, preguntas_por_examen=por_examen, aprobacion=aprobacion)
    guardar(curso)
    return curso


def banco_del_curso(curso):
    """Preguntas propias del curso o, si no tiene, el banco de la Ley 24.449."""
    return curso.get("preguntas") or banco_ley()
