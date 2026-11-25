"""Examen y registro de resultados de la capacitación (antes lo hacía un flujo de n8n; desde 2026-11-17 todo acá).

La página del chofer (web/curso.html) llama:
  POST /panel/publico/examen/inicio  {nombre, dni, celular, empresa, fecha_nacimiento, consentimiento, curso, modo, examen}
       -> valida, elige preguntas al azar del curso (o del banco Ley 24.449 si el curso no tiene propias),
          guarda la sesión CON las respuestas correctas y devuelve las preguntas SIN ellas.
  POST /panel/publico/examen/fin     {sesion, respuestas: {"0": "opción", ...}, atencion: {...}, dispositivo}
       -> corrige contra la sesión guardada y registra el resultado (atención: solo números, nunca imágenes).
El control de atención lo hace el navegador del chofer (MediaPipe); acá solo llegan los números.
"""
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timezone

from . import cursos, padron

VENCE_SESION = 24 * 3600

COLUMNAS = ["fecha", "nombre", "dni", "celular", "empresa", "fecha_nacimiento", "curso", "modo", "correctas", "total",
            "puntaje", "aprobado", "errores", "consentimiento", "atencion_pct", "distracciones", "salidas_pantalla",
            "segundos_estudio", "segundos_sin_atencion", "dispositivo", "curso_version", "diploma", "detalle"]


def iniciar():
    with closing(cursos.con()) as c, c:
        c.execute("""CREATE TABLE IF NOT EXISTS examen_sesiones (
            sesion TEXT PRIMARY KEY, creado REAL NOT NULL, curso TEXT, modo TEXT, nombre TEXT, dni TEXT,
            celular TEXT, empresa TEXT, fecha_nacimiento TEXT, preguntas TEXT NOT NULL, aprobacion INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS resultados (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, nombre TEXT, dni TEXT, celular TEXT,
            empresa TEXT, fecha_nacimiento TEXT, curso TEXT, modo TEXT, correctas INTEGER, total INTEGER,
            puntaje INTEGER, aprobado INTEGER, errores TEXT, consentimiento INTEGER, atencion_pct INTEGER,
            distracciones INTEGER, salidas_pantalla INTEGER, segundos_estudio INTEGER,
            segundos_sin_atencion INTEGER, dispositivo TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS resultados_fecha ON resultados(fecha)")
        for tabla in ("examen_sesiones", "resultados"):          # 2026-11-17: qué versión del curso rindió
            if "curso_version" not in {r["name"] for r in c.execute(f"PRAGMA table_info({tabla})")}:
                c.execute(f"ALTER TABLE {tabla} ADD COLUMN curso_version INTEGER")
        if "consentimiento" not in {r["name"] for r in c.execute("PRAGMA table_info(examen_sesiones)")}:   # 2026-11-25
            c.execute("ALTER TABLE examen_sesiones ADD COLUMN consentimiento INTEGER")
        if "detalle" not in {r["name"] for r in c.execute("PRAGMA table_info(resultados)")}:   # 2026-11-25
            c.execute("ALTER TABLE resultados ADD COLUMN detalle TEXT")   # fallas con la respuesta (para volver a verlo)
        if "diploma" not in {r["name"] for r in c.execute("PRAGMA table_info(resultados)")}:   # 2026-11-24
            c.execute("ALTER TABLE resultados ADD COLUMN diploma TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS resultados_diploma ON resultados(diploma)")


# Código de verificación del diploma: 12 caracteres sin los que se confunden (0/O, 1/I/L).
_LETRAS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def codigo_diploma():
    x = "".join(secrets.choice(_LETRAS) for _ in range(12))
    return f"{x[:4]}-{x[4:8]}-{x[8:]}"


def asegurar_diploma(rid):
    """Código del diploma de un resultado aprobado (los aprobados de antes del diploma lo reciben al pedirlo)."""
    with closing(cursos.con()) as c, c:
        r = c.execute("SELECT diploma, aprobado FROM resultados WHERE id=?", (rid,)).fetchone()
        if not r or not r["aprobado"]:
            return None
        if r["diploma"]:
            return r["diploma"]
        cod = codigo_diploma()
        c.execute("UPDATE resultados SET diploma=? WHERE id=?", (cod, rid))
        return cod


def por_diploma(codigo):
    cod = re.sub(r"[^A-Z0-9]", "", str(codigo or "").upper())
    if len(cod) != 12:
        return None
    with closing(cursos.con()) as c:
        r = c.execute("SELECT * FROM resultados WHERE diploma=? AND aprobado=1",
                      (f"{cod[:4]}-{cod[4:8]}-{cod[8:]}",)).fetchone()
    return dict(r) if r else None


def _mezclar(lista):
    lista = list(lista)
    secrets.SystemRandom().shuffle(lista)
    return lista


def _num(v, maximo):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0
    return max(0, min(maximo, round(x))) if x == x else 0


def inicio(b):
    b = b if isinstance(b, dict) else {}
    ch = padron.chofer_de_token(b.get("token"))
    if ch:                                   # entró con DNI + nacimiento: los datos salen del padrón
        b = {**b, "nombre": ch["nombre"], "dni": ch["dni"], "celular": ch["celular"] or b.get("celular"),
             "empresa": ch["transporte"] or "", "fecha_nacimiento": ch["nacimiento"]}
    nombre = str(b.get("nombre") or "").strip()[:80]
    dni = re.sub(r"\D", "", str(b.get("dni") or ""))
    celular = re.sub(r"[^\d+]", "", str(b.get("celular") or ""))[:20]
    empresa = str(b.get("empresa") or "").strip()[:80]
    modo = "sin" if b.get("modo") == "sin" else "camara"
    nacimiento = str(b.get("fecha_nacimiento") or "")
    try:
        edad = (date.today() - date.fromisoformat(nacimiento)).days / 365.25
    except ValueError:
        nacimiento, edad = "", -1
    errores = []
    if len(nombre) < 3:
        errores.append("Nombre y apellido")
    if not padron.normalizar_dni(dni):
        errores.append("DNI o documento (de 6 a 12 números)")
    dni = padron.normalizar_dni(dni) or dni
    if len(re.sub(r"\D", "", celular)) < 8 and not ch:      # el que entró con DNI puede no tenerlo cargado
        errores.append("Celular")
    if not 16 <= edad <= 90:
        errores.append("Fecha de nacimiento")
    slug = str(b.get("curso") or "")
    curso = cursos.obtener(slug)
    pide = (curso or {}).get("pedir_consentimiento", True)      # el curso puede no mostrar la pantalla de aviso
    if modo == "camara" and pide and b.get("consentimiento") is not True:
        errores.append("Consentimiento")
    if not curso or not curso.get("habilitado"):
        errores.append("Curso no disponible")
    elif curso.get("acceso") == "asignados" and not (ch and padron.asignacion(slug, ch["dni"])):
        errores.append("Este curso es solo para choferes asignados: ingresá con tu DNI y fecha de nacimiento"
                       if not ch else "No tenés asignado este curso. Consultá con tu transporte")
    if errores:
        return {"ok": False, "errores": errores}

    padron.guardar_celular(dni, celular)          # si está en el padrón sin celular, queda el que escribió
    banco = cursos.banco_del_curso(curso)
    armar = lambda q, paso=None: {"pregunta": q["pregunta"], "correcta": q["correcta"], "paso": paso,
                                  "opciones": _mezclar([q["correcta"], *q["incorrectas"]])}
    if curso.get("flujo"):
        # Flujo: cada paso de preguntas trae las suyas (elegidas o al azar), sin repetir entre pasos.
        por_id = {cursos._id_pregunta(q): q for q in banco}
        usadas = {i for x in curso["flujo"] if x["tipo"] == "preguntas" for i in x.get("ids", [])}
        resto = [q for q in _mezclar(banco) if cursos._id_pregunta(q) not in usadas]
        elegidas = []
        for x in curso["flujo"]:
            if x["tipo"] != "preguntas":
                continue
            if x.get("modo") == "elegidas":
                elegidas += [armar(por_id[i], x["id"]) for i in x.get("ids", []) if i in por_id]
            else:
                toma, resto = resto[:x.get("cantidad", 5)], resto[x.get("cantidad", 5):]
                elegidas += [armar(q, x["id"]) for q in toma]
    else:
        n = curso.get("preguntas_por_examen", cursos.POR_EXAMEN) if curso.get("examen") else 0
        elegidas = [armar(q) for q in _mezclar(banco)[:n]]
    sesion = secrets.token_hex(16)
    ahora = time.time()
    with closing(cursos.con()) as c, c:
        c.execute("DELETE FROM examen_sesiones WHERE creado < ?", (ahora - VENCE_SESION,))
        c.execute("""INSERT INTO examen_sesiones (sesion, creado, curso, modo, nombre, dni, celular, empresa,
                     fecha_nacimiento, preguntas, aprobacion, curso_version, consentimiento) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sesion, ahora, slug, modo, nombre, dni, celular, empresa, nacimiento,
                   json.dumps(elegidas, ensure_ascii=False), curso.get("aprobacion", cursos.APROBACION),
                   curso.get("version", 1), 1 if modo == "camara" and b.get("consentimiento") is True else 0))
    return {"ok": True, "sesion": sesion,
            "preguntas": [{"n": i, "pregunta": p["pregunta"], "opciones": p["opciones"], "paso": p.get("paso")}
                          for i, p in enumerate(elegidas)]}


def fin(b):
    b = b if isinstance(b, dict) else {}
    with closing(cursos.con()) as c, c:
        # Se borra al leerla: una sesión se corrige una sola vez.
        s = c.execute("DELETE FROM examen_sesiones WHERE sesion=? AND creado>=? RETURNING *",
                      (str(b.get("sesion") or "x"), time.time() - VENCE_SESION)).fetchone()
    if not s:
        return {"ok": False, "error": "Sesión inválida o vencida. Volvé a empezar la capacitación."}
    preguntas = json.loads(s["preguntas"])
    resp = b.get("respuestas") if isinstance(b.get("respuestas"), dict) else {}
    correctas, fallas = 0, []
    for i, p in enumerate(preguntas):
        elegida = resp.get(str(i))
        if elegida == p["correcta"]:
            correctas += 1
        else:
            fallas.append({"pregunta": p["pregunta"], "respuesta": str(elegida or "(sin respuesta)")[:300],
                           "correcta": p["correcta"]})
    total = len(preguntas)
    puntaje = round(correctas * 100 / total) if total else 100      # sin examen: curso completado
    aprobacion = s["aprobacion"] or cursos.APROBACION
    camara = s["modo"] == "camara"
    a = b.get("atencion") if isinstance(b.get("atencion"), dict) else {}
    fila = {
        "fecha": datetime.now(timezone.utc).isoformat(timespec="microseconds"), "nombre": s["nombre"], "dni": s["dni"],
        "celular": s["celular"], "empresa": s["empresa"], "fecha_nacimiento": s["fecha_nacimiento"],
        "curso": s["curso"], "modo": s["modo"], "correctas": correctas, "total": total, "puntaje": puntaje,
        "aprobado": puntaje >= aprobacion, "errores": " | ".join(f["pregunta"] for f in fallas),
        "consentimiento": camara and bool(s["consentimiento"]),   # con cámara sin la pantalla de aviso: queda 0
        "atencion_pct": _num(a.get("pct"), 100) if camara else None,
        "distracciones": _num(a.get("distracciones"), 9999) if camara else None,
        "salidas_pantalla": _num(a.get("salidas"), 9999) if camara else None,
        "segundos_estudio": _num(a.get("segundos"), 86400),
        "segundos_sin_atencion": _num(a.get("segundosSinAtencion"), 86400) if camara else None,
        "dispositivo": str(b.get("dispositivo") or "")[:120], "curso_version": s["curso_version"],
        "diploma": None,
        "detalle": json.dumps({"fallas": fallas, "aprobacion": aprobacion}, ensure_ascii=False),
    }
    curso = cursos.obtener(s["curso"]) or {}
    if fila["aprobado"] and curso.get("diploma", True):
        fila["diploma"] = codigo_diploma()
    with closing(cursos.con()) as c, c:
        c.execute(f"INSERT INTO resultados ({','.join(COLUMNAS)}) VALUES ({','.join('?' * len(COLUMNAS))})",
                  [fila[k] for k in COLUMNAS])
    return {"ok": True, "correctas": correctas, "total": total, "puntaje": puntaje, "aprobado": fila["aprobado"],
            "aprobacion": aprobacion, "fallas": fallas, "diploma": fila["diploma"]}


def resultado(rid):
    with closing(cursos.con()) as c:
        r = c.execute("SELECT * FROM resultados WHERE id=?", (rid,)).fetchone()
    return dict(r) if r else None


def intentos(dni, curso):
    """Todos los intentos de un chofer en un curso (el más nuevo primero)."""
    with closing(cursos.con()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM resultados WHERE dni=? AND curso=? ORDER BY fecha DESC",
                                           (str(dni), curso))]


def para_ver(r):
    """Un resultado listo para mostrar (la misma pantalla que ve el chofer al terminar)."""
    d = json.loads(r["detalle"]) if r.get("detalle") else {}
    fallas = d.get("fallas")
    if fallas is None:            # resultados de antes del 25/11: solo se guardaba el texto de las preguntas erradas
        fallas = [{"pregunta": p.strip(), "respuesta": None, "correcta": None} for p in (r.get("errores") or "").split(" | ")
                  if p.strip()]
    curso = cursos.obtener(r["curso"]) or {}
    fecha = datetime.fromisoformat(str(r["fecha"]).replace("Z", "+00:00")).astimezone(cursos.TZ)
    return {"id": r["id"], "fecha": fecha.strftime("%d/%m/%Y %H:%M"), "curso": r["curso"],
            "curso_titulo": curso.get("titulo", r["curso"]), "version": r.get("curso_version"),
            "nombre": r["nombre"], "dni": r["dni"], "modo": r["modo"], "examen": bool(r["total"]),
            "correctas": r["correctas"], "total": r["total"], "puntaje": r["puntaje"], "aprobado": bool(r["aprobado"]),
            "aprobacion": d.get("aprobacion") or curso.get("aprobacion", cursos.APROBACION),
            "atencion": {"pct": r["atencion_pct"], "distracciones": r["distracciones"], "salidas": r["salidas_pantalla"],
                         "minutos": round((r["segundos_estudio"] or 0) / 60, 1)},
            "fallas": fallas, "diploma": r.get("diploma") if r["aprobado"] and curso.get("diploma", True) else None,
            "consentimiento": bool(r.get("consentimiento")), "dispositivo": r.get("dispositivo") or ""}


def resultados():
    with closing(cursos.con()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM resultados ORDER BY fecha")]
