"""Cursos de capacitación: configuración editable desde el panel y servida a la página pública.

Cada curso tiene su página en https://<dominio>/<slug> (web/curso.html la arma con este JSON).
Los videos y archivos se guardan en RECURSOS/<slug>/ (carpeta del NAS, nginx la publica en /recursos/).
"""
import contextvars
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DB = os.environ.get("PANEL_DB", "/data/panel.db")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
# Quién está guardando (lo setea main._requiere): queda en el historial de cada curso.
USUARIO = contextvars.ContextVar("usuario", default="")
RECURSOS = Path(os.environ.get("RECURSOS_DIR", "/recursos"))
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
RESERVADOS = {"panel", "api", "recursos", "static", "index", "curso", "favicon.ico"}
# Campos de control (no son contenido del curso): no cuentan como "cambio" ni se restauran.
META = ("version", "version_fecha", "version_usuario", "revision", "modificado", "modificado_por")
EXTENSIONES = {".mp4": "video", ".webm": "video", ".m4v": "video", ".mov": "video",
               ".pdf": "documento", ".jpg": "imagen", ".jpeg": "imagen", ".png": "imagen", ".webp": "imagen",
               ".mp3": "audio", ".m4a": "audio", ".aac": "audio", ".ogg": "audio", ".opus": "audio", ".wav": "audio"}
ACCESO = {"abierto": "Abierto: lo puede hacer cualquiera desde la portada",
          "asignados": "Solo choferes asignados (entran con DNI y fecha de nacimiento)"}
VIGENCIA_FECHA = -1          # vigencia_meses = -1: vence en una fecha fija (vence_fecha)
VIGENCIA = {0: "No vence", 6: "Cada 6 meses", 12: "Anual (cada 12 meses)", 24: "Cada 2 años", 36: "Cada 3 años",
            VIGENCIA_FECHA: "Hasta una fecha específica"}
ATENCION = {"auto": "Automático: con cámara si el dispositivo tiene, si no sin cámara (sin preguntar)",
            "elegir": "El chofer elige: con o sin cámara",
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
            "preguntas": [], "preguntas_por_examen": 10, "aprobacion": 70, "acceso": "abierto", "vigencia_meses": 0,
            "flujo": [], "idioma_original": "Español", "diploma": True}


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
        # Historial: una fila por cada guardado (revisión), con el curso entero para poder restaurarlo.
        c.execute("""CREATE TABLE IF NOT EXISTS curso_historial (
            slug TEXT NOT NULL, revision INTEGER NOT NULL, version INTEGER, fecha TEXT NOT NULL, usuario TEXT,
            cambios TEXT, datos TEXT NOT NULL, PRIMARY KEY (slug, revision))""")
        for curso in INICIALES:
            c.execute("INSERT OR IGNORE INTO cursos VALUES (?, ?, ?)",
                      (curso["slug"], json.dumps(curso, ensure_ascii=False), time.time()))
    RECURSOS.mkdir(parents=True, exist_ok=True)
    for curso in listar():                     # cursos de antes del versionado: arrancan en v1, revisión 1
        if "version" not in curso:
            guardar(curso, nota="Versión inicial (inicio del historial)")


def ahora():
    return datetime.now(TZ).isoformat(timespec="seconds")


def listar(solo_habilitados=False):
    with closing(con()) as c:
        cursos = [json.loads(r["datos"]) for r in c.execute("SELECT datos FROM cursos")]
    cursos.sort(key=lambda x: (x.get("orden", 99), x["titulo"].lower()))
    return [x for x in cursos if x.get("habilitado")] if solo_habilitados else cursos


def obtener(slug):
    with closing(con()) as c:
        r = c.execute("SELECT datos FROM cursos WHERE slug=?", (slug,)).fetchone()
    return json.loads(r["datos"]) if r else None


def guardar(curso, nueva_version=False, nota=""):
    """Guarda el curso y deja una revisión en el historial (si cambió algo). nueva_version: sube vN (y su fecha,
    que es la que usa la vigencia "desde la versión")."""
    anterior = obtener(curso["slug"])
    cambios = resumen_cambios(anterior, curso)
    if anterior and "version" in anterior and not cambios and not nueva_version and not nota:
        return curso                                         # nada para registrar
    hora, quien = ahora(), USUARIO.get() or ""
    previa = anterior or {}
    curso["version"] = previa.get("version") or curso.get("version") or 1
    curso["version_fecha"] = previa.get("version_fecha") or curso.get("version_fecha") or hora
    curso["version_usuario"] = previa.get("version_usuario") or curso.get("version_usuario") or quien
    if nueva_version and previa.get("version"):
        curso["version"] = previa["version"] + 1
        curso["version_fecha"], curso["version_usuario"] = hora, quien
        cambios.insert(0, f"Nueva versión v{curso['version']}")
    curso["revision"] = (previa.get("revision") or 0) + 1
    curso["modificado"], curso["modificado_por"] = hora, quien
    texto = "; ".join(([nota] if nota else []) + cambios) or "Guardado sin cambios"
    with closing(con()) as c, c:
        c.execute("INSERT OR REPLACE INTO cursos VALUES (?, ?, ?)",
                  (curso["slug"], json.dumps(curso, ensure_ascii=False), time.time()))
        c.execute("INSERT OR REPLACE INTO curso_historial VALUES (?,?,?,?,?,?,?)",
                  (curso["slug"], curso["revision"], curso["version"], hora, quien, texto[:2000],
                   json.dumps(curso, ensure_ascii=False)))
    return curso


_NOMBRES = {"titulo": "título", "descripcion": "descripción", "acceso": "quién lo puede hacer",
            "vigencia_meses": "vigencia", "vence_fecha": "fecha de vencimiento", "vigencia_desde": "cómo se cuenta la vigencia",
            "atencion": "control de atención", "mostrar_camara": "mostrar cámara", "pedir_consentimiento": "pedir consentimiento", "examen": "evaluación",
            "min_segundos": "tiempo mínimo", "orden": "orden", "preguntas_por_examen": "preguntas por examen",
            "periodo_inicio": "fecha de inicio", "periodo_fin": "fecha de fin",
            "diploma": "diploma", "diploma_emisor": "quién emite el diploma", "diploma_firma_nombre": "firma del diploma",
            "aprobacion": "% para aprobar"}


def resumen_cambios(a, b):
    """Lista corta de qué cambió entre dos versiones del curso (para el historial)."""
    if not a:
        return ["Curso creado"]
    out = []
    if a.get("habilitado") != b.get("habilitado"):
        out.append("Habilitado" if b.get("habilitado") else "Deshabilitado")
    for k, nombre in _NOMBRES.items():
        if a.get(k) != b.get(k):
            out.append(f"Cambió {nombre}")
    if a.get("material") != b.get("material"):
        n = lambda m: sum(len(x["items"]) for x in m or [])
        out.append(f"Material editado ({n(a.get('material'))} → {n(b.get('material'))} puntos)")
    va = {v["id"]: v for v in a.get("videos", [])}
    vb = {v["id"]: v for v in b.get("videos", [])}
    if (a.get("flujo") or []) != (b.get("flujo") or []):
        out.append(f"Flujo editado ({len(a.get('flujo') or [])} → {len(b.get('flujo') or [])} pasos)")
    aud = lambda d: {(v["id"], x["id"]) for v in d.values() for x in v.get("audios") or []}
    if aud(va) != aud(vb):
        out.append("Cambió audios de idiomas")
    out += [f"Agregó «{vb[i]['titulo']}»" for i in vb if i not in va]
    out += [f"Quitó «{va[i]['titulo']}»" for i in va if i not in vb]
    if any(i in va and (va[i].get("titulo"), va[i].get("orden")) != (v.get("titulo"), v.get("orden")) for i, v in vb.items()):
        out.append("Renombró u ordenó recursos")
    pa = {q["pregunta"]: q for q in a.get("preguntas", [])}
    pb = {q["pregunta"]: q for q in b.get("preguntas", [])}
    mas, menos = len(set(pb) - set(pa)), len(set(pa) - set(pb))
    mod = sum(1 for k in set(pa) & set(pb) if pa[k] != pb[k])
    if mas or menos or mod:
        out.append("Preguntas: " + ", ".join(x for x in (f"+{mas}" if mas else "", f"-{menos}" if menos else "",
                                                          f"{mod} modificadas" if mod else "") if x) +
                   f" (total {len(pb)})")
    return out


def historial(slug):
    with closing(con()) as c:
        return [dict(r) for r in c.execute("""SELECT slug, revision, version, fecha, usuario, cambios FROM curso_historial
                                              WHERE slug=? ORDER BY revision DESC""", (slug,))]


def restaurar(slug, revision):
    """Vuelve el contenido a una revisión anterior (queda como revisión nueva; la versión no cambia).
    No toca habilitado ni el control de versión."""
    with closing(con()) as c:
        r = c.execute("SELECT datos FROM curso_historial WHERE slug=? AND revision=?", (slug, revision)).fetchone()
    actual = obtener(slug)
    if not r or not actual:
        raise ValueError("No existe esa revisión.")
    viejo = json.loads(r["datos"])
    nuevo = {k: v for k, v in viejo.items() if k not in META and k != "habilitado"}
    nuevo.update({k: actual[k] for k in META if k in actual}, habilitado=actual.get("habilitado"), slug=slug)
    faltan = [v["titulo"] for v in nuevo.get("videos", []) if v.get("archivo") and not (RECURSOS / v["archivo"]).is_file()]
    nuevo["videos"] = [v for v in nuevo.get("videos", []) if not (v.get("archivo") and not (RECURSOS / v["archivo"]).is_file())]
    guardar(nuevo, nota=f"Restaurada la revisión {revision}")
    return faltan


def fecha_corte(curso):
    """Vigencia con fecha fija: la elegida a mano o (si se cuenta desde la versión) fecha de la versión + N meses."""
    meses = int(curso.get("vigencia_meses") or 0)
    if meses == VIGENCIA_FECHA and curso.get("vence_fecha"):
        return date.fromisoformat(curso["vence_fecha"])
    if meses > 0 and curso.get("vigencia_desde") == "version" and curso.get("version_fecha"):
        d = date.fromisoformat(curso["version_fecha"][:10])
        m = d.month - 1 + meses
        a, m = d.year + m // 12, m % 12 + 1
        import calendar
        return date(a, m, min(d.day, calendar.monthrange(a, m)[1]))
    return None


def crear(slug, titulo, copiar_de=""):
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug) or slug in RESERVADOS:
        raise ValueError("La dirección tiene que ser de 2 a 40 letras minúsculas, números o guiones (ej. manejo-nocturno).")
    if obtener(slug):
        raise ValueError(f"Ya existe un curso con la dirección /{slug}.")
    base = obtener(copiar_de) if copiar_de else None
    curso = {k: v for k, v in base.items() if k not in META} if base else _base(slug, titulo or slug, "", 99)
    if not base:
        curso["atencion"] = "auto"             # pedido 2026-11-25: con cámara si tiene, si no sin cámara, sin elegir
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
    curso["pedir_consentimiento"] = f.get("pedir_consentimiento") == "1"
    curso["examen"] = f.get("examen") == "1"
    curso["atencion"] = f.get("atencion") if f.get("atencion") in ATENCION else "elegir"
    curso["acceso"] = f.get("acceso") if f.get("acceso") in ACCESO else "abierto"
    try:
        curso["vigencia_meses"] = max(VIGENCIA_FECHA, min(120, int(f.get("vigencia_meses") or 0)))
    except ValueError:
        pass
    curso["vigencia_desde"] = "version" if f.get("vigencia_desde") == "version" else "aprobacion"
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
    curso["diploma"] = f.get("diploma") == "1"
    for k in ("periodo_inicio", "periodo_fin"):         # período del curso (para el avance mes a mes)
        try:
            curso[k] = date.fromisoformat(str(f.get(k) or "")).isoformat()
        except ValueError:
            curso[k] = ""
    if curso["periodo_inicio"] and curso["periodo_fin"] and curso["periodo_fin"] <= curso["periodo_inicio"]:
        raise ValueError("La fecha de fin del curso tiene que ser posterior a la de inicio.")
    for k in ("diploma_emisor", "diploma_firma_nombre", "diploma_firma_cargo"):
        curso[k] = " ".join(str(f.get(k) or "").split())[:80]
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
    guardar(curso, nueva_version=f.get("nueva_version") == "1")
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
    usado = [p.get("titulo") or "sin título" for p in curso.get("flujo") or [] if p.get("recurso") == vid]
    if usado:
        raise ValueError("Ese recurso se usa en el flujo (paso «" + "», «".join(usado) + "»). Cambialo o sacalo del flujo primero.")
    sacado = next((v for v in curso["videos"] if v["id"] == vid), None)
    curso["videos"] = [v for v in curso["videos"] if v["id"] != vid]
    guardar(curso)
    # El archivo queda en el NAS: el historial lo puede necesitar para restaurar una versión anterior.
    return curso


def agregar_link(slug, url, titulo):
    url = (url or "").strip()
    if not re.fullmatch(r"https?://[^\s<>\"']{4,2000}", url):
        raise ValueError("El link tiene que empezar con http:// o https://")
    curso = obtener(slug)
    curso["videos"].append({"id": f"{int(time.time() * 1000):x}", "tipo": "link", "url": url,
                            "titulo": (titulo or url.split("//", 1)[1].split("/", 1)[0]).strip()[:120],
                            "orden": len(curso["videos"]) + 1})
    guardar(curso)
    return curso


def _recurso_publico(v):
    return {"titulo": v["titulo"], "tipo": v["tipo"],
            **({"youtube": v["youtube"]} if v["tipo"] == "youtube" else
               {"url": v["url"]} if v["tipo"] == "link" else {"url": "/recursos/" + v["archivo"]}),
            **({"audios": [{"idioma": x["idioma"], "url": "/recursos/" + x["archivo"]} for x in v["audios"]]}
               if v.get("audios") else {})}


def publico(curso):
    """Lo que ve la página del chofer (sin campos internos). Con flujo: los pasos en orden, cada uno con su recurso
    (las preguntas NO van acá: las arma examen.inicio sin las respuestas)."""
    recursos = {v["id"]: v for v in curso.get("videos", [])}
    flujo = []
    for p in curso.get("flujo") or []:
        paso = {k: p[k] for k in ("id", "tipo", "titulo", "avance", "segundos") if k in p}
        if p["tipo"] == "recurso":
            if p.get("recurso") not in recursos:
                continue
            paso["recurso"] = _recurso_publico(recursos[p["recurso"]])
        elif p["tipo"] == "material":
            paso["material"] = texto_a_material(p.get("texto", ""))
        flujo.append(paso)
    idiomas = [curso.get("idioma_original") or "Español"]
    for p in curso.get("flujo") or []:
        for x in (recursos.get(p.get("recurso")) or {}).get("audios") or []:
            if x["idioma"] not in idiomas:
                idiomas.append(x["idioma"])
    return {k: curso[k] for k in ("slug", "titulo", "descripcion", "material", "min_segundos", "atencion",
                                  "mostrar_camara", "examen")} | {
        "aprobacion": curso.get("aprobacion", APROBACION), "acceso": curso.get("acceso", "abierto"),
        "version": curso.get("version", 1), "flujo": flujo, "idiomas": idiomas,
        "pedir_consentimiento": curso.get("pedir_consentimiento", True),
        "videos": [_recurso_publico(v) for v in curso.get("videos", [])]}


# ------------------------------------------------------------------ flujo del curso (línea de tiempo)
# Cada paso: {"id", "tipo": recurso|material|preguntas, "titulo", "avance", "segundos",
#             "recurso": id de curso.videos (tipo recurso), "texto": material como texto (tipo material),
#             "modo": azar|elegidas, "cantidad": N, "ids": [ids de preguntas] (tipo preguntas)}
# avance: libre (hasta que hace clic en Siguiente) | minimo (Siguiente se habilita a los N s) |
#         auto (pasa solo a los N s) | fin_video (cuando termina el video, sin poder adelantarlo)
AVANCE = {"libre": "Hasta que hace clic en «Siguiente»", "minimo": "Tiempo mínimo, después «Siguiente»",
          "auto": "Pasa solo después del tiempo", "fin_video": "Cuando termina el video (sin adelantar)"}
MAX_PASOS = 60


def _id_pregunta(q):
    return str(q.get("id"))


def limpiar_flujo(lista, curso):
    if not isinstance(lista, list):
        raise ValueError("Formato inválido.")
    if len(lista) > MAX_PASOS:
        raise ValueError(f"Máximo {MAX_PASOS} pasos.")
    recursos = {v["id"]: v for v in curso.get("videos", [])}
    banco = {_id_pregunta(q) for q in banco_del_curso(curso)}
    salida, errores, ids = [], [], set()
    for n, p in enumerate(lista, start=1):
        p = p if isinstance(p, dict) else {}
        tipo = p.get("tipo")
        if tipo not in ("recurso", "material", "preguntas"):
            errores.append(f"Paso {n}: tipo inválido.")
            continue
        pid = str(p.get("id") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,40}", pid) or pid in ids:
            pid = f"p{int(time.time() * 1000):x}{n}"
        ids.add(pid)
        avance = p.get("avance") if p.get("avance") in AVANCE else "libre"
        try:
            segundos = max(0, min(7200, int(p.get("segundos") or 0)))
        except (TypeError, ValueError):
            segundos = 0
        paso = {"id": pid, "tipo": tipo, "titulo": " ".join(str(p.get("titulo") or "").split())[:120]}
        if tipo == "recurso":
            r = recursos.get(p.get("recurso"))
            if not r:
                errores.append(f"Paso {n}: elegí qué video o archivo muestra.")
                continue
            paso["recurso"] = r["id"]                # sin título propio: se muestra el del recurso
            if avance == "fin_video" and r["tipo"] not in ("video", "audio"):
                avance = "minimo" if segundos else "libre"
        elif tipo == "material":
            paso["texto"] = str(p.get("texto") or "")[:20000]
            paso["titulo"] = paso["titulo"] or "Material de estudio"
            if not texto_a_material(paso["texto"]):
                errores.append(f"Paso {n}: el material está vacío.")
            if avance == "fin_video":
                avance = "libre"
        else:
            paso["modo"] = "elegidas" if p.get("modo") == "elegidas" else "azar"
            if paso["modo"] == "elegidas":
                paso["ids"] = [str(i) for i in (p.get("ids") or []) if str(i) in banco][:50]
                if not paso["ids"]:
                    errores.append(f"Paso {n}: marcá qué preguntas van.")
            else:
                try:
                    paso["cantidad"] = max(1, min(50, int(p.get("cantidad") or 5)))
                except (TypeError, ValueError):
                    paso["cantidad"] = 5
            avance = "libre"                       # las preguntas avanzan cuando contestó todas
            paso["titulo"] = paso["titulo"] or "Preguntas"
        if avance in ("minimo", "auto") and not segundos:
            avance = "libre"
        paso.update(avance=avance, segundos=segundos if avance in ("minimo", "auto") else 0)
        salida.append(paso)
    if errores:
        raise ValueError(" ".join(errores[:8]))
    azar = sum(p.get("cantidad", 0) for p in salida if p["tipo"] == "preguntas" and p["modo"] == "azar")
    fijas = {i for p in salida if p["tipo"] == "preguntas" for i in p.get("ids", [])}
    if azar + len(fijas) > len(banco):
        raise ValueError(f"El flujo pide {azar + len(fijas)} preguntas y el curso tiene {len(banco)}. "
                         "Bajá la cantidad o cargá más preguntas.")
    return salida


def guardar_flujo(slug, datos):
    curso = obtener(slug)
    if not curso:
        raise ValueError("No existe ese curso.")
    curso["flujo"] = limpiar_flujo(datos.get("flujo"), curso)
    original = " ".join(str(datos.get("idioma_original") or "").split())[:30]
    if original:
        curso["idioma_original"] = original
    guardar(curso, nueva_version=bool(datos.get("nueva_version")))
    return curso


def flujo_desde_actual(curso):
    """Arma un flujo con lo que el curso ya tiene: cada recurso, después el material y al final las preguntas."""
    pasos = [{"id": f"p{i}", "tipo": "recurso", "recurso": v["id"], "titulo": v["titulo"],
              "avance": "fin_video" if v["tipo"] == "video" else "libre", "segundos": 0}
             for i, v in enumerate(curso.get("videos", []), start=1)]
    if curso.get("material"):
        pasos.append({"id": "pm", "tipo": "material", "titulo": "Material de estudio", "texto": material_a_texto(curso["material"]),
                      "avance": "minimo" if curso.get("min_segundos") else "libre", "segundos": curso.get("min_segundos") or 0})
    if curso.get("examen"):
        pasos.append({"id": "pq", "tipo": "preguntas", "titulo": "Evaluación", "modo": "azar",
                      "cantidad": min(curso.get("preguntas_por_examen") or POR_EXAMEN, len(banco_del_curso(curso)))})
    return pasos


def agregar_audio(slug, vid, archivo, idioma):
    """Audio en otro idioma para un video (la página lo reproduce sincronizado, con el video en silencio)."""
    curso = obtener(slug)
    v = next((x for x in curso["videos"] if x["id"] == vid), None)
    if not v or v["tipo"] != "video":
        raise ValueError("Los audios en otros idiomas se agregan a videos subidos (no a YouTube).")
    idioma = " ".join((idioma or "").split())[:30]
    if not idioma:
        raise ValueError("Escribí el idioma del audio.")
    v["audios"] = [x for x in v.get("audios") or [] if x["idioma"].lower() != idioma.lower()]
    v["audios"].append({"id": f"{int(time.time() * 1000):x}", "idioma": idioma, "archivo": f"{slug}/{archivo}"})
    guardar(curso)
    return curso


def quitar_audio(slug, vid, aid):
    curso = obtener(slug)
    for v in curso["videos"]:
        if v["id"] == vid:
            v["audios"] = [x for x in v.get("audios") or [] if x["id"] != aid]
    guardar(curso)
    return curso


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
    guardar(curso, nueva_version=bool(datos.get("nueva_version")))
    return curso


def banco_del_curso(curso):
    """Preguntas propias del curso o, si no tiene, el banco de la Ley 24.449."""
    return curso.get("preguntas") or banco_ley()
