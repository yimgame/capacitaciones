"""Padrón de transportes y choferes, asignación de cursos, recursadas y vigencia (2026-11-17).

Estado de un chofer en un curso asignado (se calcula, no se guarda):
  aprobado    aprobó después de la asignación / del último "recursar", y todavía no venció
  por_vencer  aprobado, pero vence en los próximos 30 días (cursos con vigencia)
  vencido     aprobó, pero pasó la vigencia del curso (ej. anual): lo tiene que volver a hacer
  atrasado    no aprobó y pasó la fecha límite
  pendiente   no aprobó todavía (puede tener intentos desaprobados)
"""
import calendar
import csv
import io
import re
import secrets
import time
import unicodedata
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import cursos

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SESION_CHOFER = 12 * 3600
AVISO_DIAS = 30
ESTADOS = {"aprobado": "✅ Aprobado", "por_vencer": "🟡 Por vencer", "vencido": "🔴 Vencido",
           "atrasado": "🔴 Fuera de término", "pendiente": "⚪ Pendiente"}


def con():
    return cursos.con()


def iniciar():
    with closing(con()) as c, c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS transportes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
            activo INTEGER NOT NULL DEFAULT 1, creado TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS choferes (
            dni TEXT PRIMARY KEY, nombre TEXT NOT NULL, celular TEXT, nacimiento TEXT,
            transporte_id INTEGER REFERENCES transportes(id), activo INTEGER NOT NULL DEFAULT 1,
            creado TEXT DEFAULT CURRENT_TIMESTAMP, actualizado TEXT, cargado_por TEXT);
        CREATE INDEX IF NOT EXISTS choferes_transporte ON choferes(transporte_id);
        -- un chofer tiene asignado un curso; "desde" = asignación o último recursar (cuenta lo aprobado después)
        CREATE TABLE IF NOT EXISTS asignaciones (
            curso TEXT NOT NULL, dni TEXT NOT NULL, desde TEXT NOT NULL, limite TEXT, asignado_por TEXT,
            PRIMARY KEY (curso, dni));
        -- "asignar a todo el transporte": los choferes que se carguen después también lo reciben
        CREATE TABLE IF NOT EXISTS reglas_transporte (
            curso TEXT NOT NULL, transporte_id INTEGER NOT NULL, plazo_dias INTEGER, creado_por TEXT,
            PRIMARY KEY (curso, transporte_id));
        CREATE TABLE IF NOT EXISTS chofer_sesiones (token TEXT PRIMARY KEY, dni TEXT NOT NULL, vence REAL NOT NULL);
        """)


def ahora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clave(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


# ------------------------------------------------------------------ transportes
def transportes(solo_activos=False):
    with closing(con()) as c:
        filas = c.execute("""SELECT t.*, (SELECT count(*) FROM choferes ch WHERE ch.transporte_id=t.id AND ch.activo=1) AS choferes
                             FROM transportes t ORDER BY t.nombre COLLATE NOCASE""").fetchall()
    return [dict(f) for f in filas if f["activo"] or not solo_activos]


def transporte(tid):
    with closing(con()) as c:
        f = c.execute("SELECT * FROM transportes WHERE id=?", (tid,)).fetchone()
    return dict(f) if f else None


def transporte_id_de(nombre, crear=True):
    """Busca el transporte por nombre ("Transp. Sur" = "transp sur") y si no existe lo crea."""
    nombre = " ".join((nombre or "").split())[:80]
    if not nombre:
        return None
    k = _clave(nombre)
    for t in transportes():
        if _clave(t["nombre"]) == k:
            return t["id"]
    if not crear:
        return None
    with closing(con()) as c, c:
        return c.execute("INSERT INTO transportes (nombre) VALUES (?)", (nombre,)).lastrowid


def renombrar_transporte(tid, nombre, activo):
    nombre = " ".join((nombre or "").split())[:80]
    if not nombre:
        raise ValueError("El transporte necesita un nombre.")
    with closing(con()) as c, c:
        otro = c.execute("SELECT id FROM transportes WHERE nombre=? COLLATE NOCASE AND id<>?", (nombre, tid)).fetchone()
        if otro:
            raise ValueError("Ya existe otro transporte con ese nombre.")
        c.execute("UPDATE transportes SET nombre=?, activo=? WHERE id=?", (nombre, 1 if activo else 0, tid))


# ------------------------------------------------------------------ choferes
def normalizar_dni(dni):
    dni = re.sub(r"\D", "", str(dni or ""))
    return dni if re.fullmatch(r"\d{7,8}", dni) else ""


def normalizar_fecha(s):
    """'1985-03-21', '21/03/1985', '21-3-85', '21.03.1985' o número de serie de Excel -> 'AAAA-MM-DD' ('' si no se entiende)."""
    s = str(s or "").strip()
    if not s:
        return ""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s)
    if m:
        a, mes, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})(?: .*)?", s)
        if m:
            d, mes, a = int(m[1]), int(m[2]), int(m[3])
            if a < 100:
                a += 1900 if a > date.today().year % 100 else 2000
        elif re.fullmatch(r"\d{4,5}(\.0+)?", s):             # Excel: días desde 1899-12-30
            try:
                return (date(1899, 12, 30) + timedelta(days=int(float(s)))).isoformat()
            except OverflowError:
                return ""
        else:
            return ""
    try:
        f = date(a, mes, d)
    except ValueError:
        return ""
    return f.isoformat() if 1920 <= f.year <= date.today().year - 15 else ""


def normalizar_fecha_limite(s):
    """Fecha límite (puede ser futura): 'AAAA-MM-DD' o 'DD/MM/AAAA' -> ISO; vacío/ inválido -> None."""
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def choferes(transporte_id=None, buscar="", incluir_inactivos=False):
    sql = """SELECT ch.*, t.nombre AS transporte FROM choferes ch LEFT JOIN transportes t ON t.id = ch.transporte_id
             WHERE 1=1"""
    args = []
    if transporte_id:
        sql += " AND ch.transporte_id=?"
        args.append(transporte_id)
    if not incluir_inactivos:
        sql += " AND ch.activo=1"
    with closing(con()) as c:
        filas = [dict(f) for f in c.execute(sql + " ORDER BY ch.nombre COLLATE NOCASE", args)]
    if buscar:
        b = _clave(buscar)
        filas = [f for f in filas if b in _clave(f["nombre"]) or b in f["dni"]]
    return filas


def chofer(dni):
    with closing(con()) as c:
        f = c.execute("""SELECT ch.*, t.nombre AS transporte FROM choferes ch LEFT JOIN transportes t ON t.id = ch.transporte_id
                         WHERE ch.dni=?""", (dni,)).fetchone()
    return dict(f) if f else None


def guardar_chofer(datos, por, solo_transporte=None):
    """Alta o modificación. solo_transporte: el coordinador solo puede cargar/tocar choferes de su transporte.
    Devuelve 'nuevo' o 'actualizado'."""
    dni = normalizar_dni(datos.get("dni"))
    if not dni:
        raise ValueError("DNI inválido (7 u 8 números).")
    nombre = " ".join(str(datos.get("nombre") or "").split())[:80]
    if len(nombre) < 3:
        raise ValueError(f"DNI {dni}: falta el nombre.")
    nacimiento = normalizar_fecha(datos.get("nacimiento"))
    if datos.get("nacimiento") and not nacimiento:
        raise ValueError(f"DNI {dni}: no entiendo la fecha de nacimiento «{datos.get('nacimiento')}» (usá DD/MM/AAAA).")
    celular = re.sub(r"[^\d+]", "", str(datos.get("celular") or ""))[:20]
    tid = datos.get("transporte_id")
    if solo_transporte:
        tid = solo_transporte
    existente = chofer(dni)
    if existente and solo_transporte and existente["transporte_id"] not in (None, solo_transporte):
        raise ValueError(f"DNI {dni}: ese chofer está cargado en otro transporte. Pedile a un administrador que lo pase.")
    with closing(con()) as c, c:
        if existente:
            c.execute("""UPDATE choferes SET nombre=?, celular=COALESCE(NULLIF(?, ''), celular),
                         nacimiento=COALESCE(NULLIF(?, ''), nacimiento), transporte_id=COALESCE(?, transporte_id),
                         activo=?, actualizado=? WHERE dni=?""",
                      (nombre, celular, nacimiento, tid, 0 if datos.get("activo") is False else 1, ahora_iso(), dni))
        else:
            c.execute("""INSERT INTO choferes (dni, nombre, celular, nacimiento, transporte_id, cargado_por, actualizado)
                         VALUES (?,?,?,?,?,?,?)""", (dni, nombre, celular, nacimiento, tid, por, ahora_iso()))
    if tid:
        aplicar_reglas(dni, tid, por)
    return "actualizado" if existente else "nuevo"


def activar_chofer(dni, activo, solo_transporte=None):
    ch = chofer(dni)
    if not ch or (solo_transporte and ch["transporte_id"] != solo_transporte):
        raise ValueError("No existe ese chofer.")
    with closing(con()) as c, c:
        c.execute("UPDATE choferes SET activo=?, actualizado=? WHERE dni=?", (1 if activo else 0, ahora_iso(), dni))


# ------------------------------------------------------------------ importación masiva (pegar desde Excel / CSV)
COLUMNAS_IMPORT = {
    "dni": {"dni", "documento", "nro documento", "numero documento", "n documento", "doc", "nro doc", "cuil", "cuit"},
    "nombre_completo": {"nombre y apellido", "apellido y nombre", "apellido nombre", "nombre completo", "chofer",
                        "conductor", "empleado", "nombre apellido"},
    "nombre": {"nombre", "nombres"},
    "apellido": {"apellido", "apellidos"},
    "celular": {"celular", "telefono", "tel", "movil", "cel", "whatsapp", "telefono celular"},
    "nacimiento": {"nacimiento", "fecha nacimiento", "fecha de nacimiento", "f nacimiento", "f nac", "fec nac",
                   "fecha nac"},
    "transporte": {"transporte", "empresa", "razon social", "transportista", "empleador"},
}


def _dni_de_cuil(v):
    d = re.sub(r"\D", "", str(v or ""))
    return d[2:10].lstrip("0") if len(d) == 11 else d


def importar(texto, transporte_por_defecto=None, por="", simular=True, solo_transporte=None):
    """Lee una lista pegada desde Excel (tabulada), CSV (; o ,). La primera fila tiene que tener los títulos."""
    lineas = [l for l in (texto or "").replace("\r", "").split("\n") if l.strip()]
    if len(lineas) < 2:
        raise ValueError("Pegá la lista con la fila de títulos arriba (DNI, Nombre, Celular, Nacimiento, Transporte).")
    sep = "\t" if "\t" in lineas[0] else (";" if lineas[0].count(";") >= lineas[0].count(",") else ",")
    filas = list(csv.reader(io.StringIO("\n".join(lineas)), delimiter=sep))
    titulos = [_clave(t) for t in filas[0]]
    mapa = {}
    for i, t in enumerate(titulos):
        for campo, nombres in COLUMNAS_IMPORT.items():
            if t in nombres and campo not in mapa:
                mapa[campo] = i
    if "dni" not in mapa:
        raise ValueError("No encuentro la columna del DNI. Títulos leídos: " + ", ".join(filas[0]))
    if not ({"nombre_completo", "nombre", "apellido"} & set(mapa)):
        raise ValueError("No encuentro la columna del nombre. Títulos leídos: " + ", ".join(filas[0]))
    res = {"nuevos": 0, "actualizados": 0, "errores": [], "transportes_nuevos": set(), "columnas": {
        k: filas[0][v] for k, v in mapa.items()}, "vista": []}
    vistos = set()
    for n, f in enumerate(filas[1:], start=2):
        val = lambda k: (f[mapa[k]].strip() if k in mapa and mapa[k] < len(f) else "")
        dni = normalizar_dni(_dni_de_cuil(val("dni")))
        if not dni:
            if any(x.strip() for x in f):
                res["errores"].append(f"Fila {n}: DNI inválido «{val('dni')}»")
            continue
        if dni in vistos:
            res["errores"].append(f"Fila {n}: DNI {dni} repetido en la lista (se usa la primera)")
            continue
        vistos.add(dni)
        nombre = val("nombre_completo") or " ".join(x for x in (val("apellido"), val("nombre")) if x)
        nombre = nombre.title() if nombre.isupper() or nombre.islower() else nombre
        tnombre = "" if solo_transporte else val("transporte")
        datos = {"dni": dni, "nombre": nombre, "celular": val("celular"), "nacimiento": val("nacimiento")}
        try:
            if tnombre:
                tid = transporte_id_de(tnombre, crear=False)
                if not tid:
                    res["transportes_nuevos"].add(" ".join(tnombre.split()))
                    if not simular:
                        tid = transporte_id_de(tnombre)
            else:
                tid = solo_transporte or transporte_por_defecto
            datos["transporte_id"] = tid
            if simular:
                if len(" ".join(nombre.split())) < 3:
                    raise ValueError(f"DNI {dni}: falta el nombre.")
                if val("nacimiento") and not normalizar_fecha(val("nacimiento")):
                    raise ValueError(f"DNI {dni}: fecha de nacimiento «{val('nacimiento')}» no válida.")
                ex = chofer(dni)
                if ex and solo_transporte and ex["transporte_id"] not in (None, solo_transporte):
                    raise ValueError(f"DNI {dni}: está cargado en otro transporte.")
                tipo = "actualizado" if ex else "nuevo"
            else:
                tipo = guardar_chofer(datos, por, solo_transporte)
        except ValueError as e:
            res["errores"].append(f"Fila {n}: {e}")
            continue
        res["nuevos" if tipo == "nuevo" else "actualizados"] += 1
        if len(res["vista"]) < 8:
            res["vista"].append({"dni": dni, "nombre": nombre, "nacimiento": normalizar_fecha(val("nacimiento")),
                                 "celular": datos["celular"], "transporte": tnombre or "", "tipo": tipo})
    res["transportes_nuevos"] = sorted(res["transportes_nuevos"])
    return res


# ------------------------------------------------------------------ asignaciones
def asignar(curso, dnis, limite, por):
    """Asigna el curso a los DNI. Si ya lo tenían, solo cambia la fecha límite (no borra lo aprobado)."""
    limite = limite or None
    n = 0
    with closing(con()) as c, c:
        for dni in dnis:
            r = c.execute("""INSERT INTO asignaciones (curso, dni, desde, limite, asignado_por) VALUES (?,?,?,?,?)
                             ON CONFLICT(curso, dni) DO UPDATE SET limite=excluded.limite""",
                          (curso, dni, ahora_iso(), limite, por))
            n += r.rowcount
    return n


def asignar_transporte(curso, tid, limite, plazo_dias, auto, por):
    dnis = [ch["dni"] for ch in choferes(transporte_id=tid)]
    n = asignar(curso, dnis, limite, por)
    with closing(con()) as c, c:
        if auto:
            c.execute("""INSERT INTO reglas_transporte VALUES (?,?,?,?)
                         ON CONFLICT(curso, transporte_id) DO UPDATE SET plazo_dias=excluded.plazo_dias""",
                      (curso, tid, plazo_dias or None, por))
        else:
            c.execute("DELETE FROM reglas_transporte WHERE curso=? AND transporte_id=?", (curso, tid))
    return n


def quitar_regla(curso, tid):
    with closing(con()) as c, c:
        c.execute("DELETE FROM reglas_transporte WHERE curso=? AND transporte_id=?", (curso, tid))


def reglas():
    with closing(con()) as c:
        return [dict(r) for r in c.execute("""SELECT r.*, t.nombre AS transporte FROM reglas_transporte r
                                              JOIN transportes t ON t.id=r.transporte_id ORDER BY t.nombre""")]


def aplicar_reglas(dni, tid, por):
    """Chofer nuevo (o que cambió de transporte): recibe los cursos que el transporte tiene asignados a todos."""
    with closing(con()) as c, c:
        for r in c.execute("SELECT * FROM reglas_transporte WHERE transporte_id=?", (tid,)).fetchall():
            limite = (date.today() + timedelta(days=r["plazo_dias"])).isoformat() if r["plazo_dias"] else None
            c.execute("INSERT OR IGNORE INTO asignaciones (curso, dni, desde, limite, asignado_por) VALUES (?,?,?,?,?)",
                      (r["curso"], dni, ahora_iso(), limite, por or "automático"))


def quitar(curso, dnis):
    with closing(con()) as c, c:
        for dni in dnis:
            c.execute("DELETE FROM asignaciones WHERE curso=? AND dni=?", (curso, dni))


def recursar(curso, dnis, limite, por):
    """Vuelve a pendiente: lo aprobado antes de ahora deja de contar (queda en el historial). Si no lo tenía, se asigna."""
    n = 0
    with closing(con()) as c, c:
        for dni in dnis:
            n += c.execute("""INSERT INTO asignaciones (curso, dni, desde, limite, asignado_por) VALUES (?,?,?,?,?)
                              ON CONFLICT(curso, dni) DO UPDATE SET desde=excluded.desde, limite=excluded.limite,
                              asignado_por=excluded.asignado_por""",
                           (curso, dni, ahora_iso(), limite or None, por)).rowcount
    return n


def asignacion(curso, dni):
    with closing(con()) as c:
        r = c.execute("SELECT * FROM asignaciones WHERE curso=? AND dni=?", (curso, dni)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ estado / cumplimiento
def _sumar_meses(d, meses):
    m = d.month - 1 + meses
    a, m = d.year + m // 12, m % 12 + 1
    return date(a, m, min(d.day, calendar.monthrange(a, m)[1]))


def _fecha_local(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TZ)


def estado(asig, resultados, curso, hoy=None):
    """asig: fila de asignaciones; resultados: los del chofer en ese curso (cualquier orden)."""
    hoy = hoy or datetime.now(TZ).date()
    desde = _fecha_local(asig["desde"])
    validos = sorted((r for r in resultados if _fecha_local(r["fecha"]) >= desde), key=lambda r: r["fecha"])
    aprobados = [r for r in validos if r["aprobado"]]
    out = {"intentos": len(validos), "mejor": max((r["puntaje"] for r in validos), default=None),
           "aprobado_el": None, "vence": None, "limite": asig.get("limite"),
           "ultimo": _fecha_local(validos[-1]["fecha"]).strftime("%d/%m/%Y") if validos else None}
    meses = int(curso.get("vigencia_meses") or 0) if curso else 0
    if aprobados:
        ap = _fecha_local(aprobados[-1]["fecha"]).date()
        out["aprobado_el"] = ap.isoformat()
        if meses:
            vence = _sumar_meses(ap, meses)
            out["vence"] = vence.isoformat()
            if vence <= hoy:
                out["estado"] = "vencido"
                return out
            if (vence - hoy).days <= AVISO_DIAS:
                out["estado"] = "por_vencer"
                return out
        out["estado"] = "aprobado"
        return out
    lim = asig.get("limite")
    out["estado"] = "atrasado" if lim and date.fromisoformat(lim) < hoy else "pendiente"
    return out


def cumplimiento(resultados, transporte_id=None, curso=None, dni=None):
    """Una fila por (chofer activo, curso asignado) con su estado."""
    sql = """SELECT a.*, ch.nombre, ch.celular, ch.transporte_id, t.nombre AS transporte
             FROM asignaciones a JOIN choferes ch ON ch.dni = a.dni LEFT JOIN transportes t ON t.id = ch.transporte_id
             WHERE ch.activo = 1"""
    args = []
    if transporte_id:
        sql += " AND ch.transporte_id=?"
        args.append(transporte_id)
    if curso:
        sql += " AND a.curso=?"
        args.append(curso)
    if dni:
        sql += " AND a.dni=?"
        args.append(dni)
    with closing(con()) as c:
        asigs = [dict(r) for r in c.execute(sql + " ORDER BY t.nombre, ch.nombre", args)]
    por = {}
    for r in resultados:
        por.setdefault((r["dni"], r["curso"]), []).append(r)
    cursos_ = {c["slug"]: c for c in cursos.listar()}
    filas = []
    for a in asigs:
        cu = cursos_.get(a["curso"])
        if not cu:
            continue
        e = estado(a, por.get((a["dni"], a["curso"]), []), cu)
        filas.append({**a, **e, "curso_titulo": cu["titulo"], "estado_txt": ESTADOS[e["estado"]]})
    return filas


def resumen_cumplimiento(filas):
    """Semáforo por transporte."""
    grupos = {}
    for f in filas:
        g = grupos.setdefault(f["transporte"] or "Sin transporte", {"transporte": f["transporte"] or "Sin transporte",
                                                                     "transporte_id": f["transporte_id"], "total": 0,
                                                                     **{k: 0 for k in ESTADOS}})
        g["total"] += 1
        g[f["estado"]] += 1
    for g in grupos.values():
        al_dia = g["aprobado"] + g["por_vencer"]
        g["al_dia_pct"] = round(al_dia * 100 / g["total"]) if g["total"] else None
    return sorted(grupos.values(), key=lambda g: (g["al_dia_pct"] if g["al_dia_pct"] is not None else 101, g["transporte"]))


# ------------------------------------------------------------------ ingreso del chofer (DNI + fecha de nacimiento)
def ingresar_chofer(dni, nacimiento):
    """Devuelve (token, chofer) o (None, motivo)."""
    dni, nac = normalizar_dni(dni), normalizar_fecha(nacimiento)
    ch = chofer(dni) if dni else None
    if not ch or not ch["activo"] or not nac:
        return None, "No encontramos ese DNI con esa fecha de nacimiento."
    if ch["nacimiento"] and ch["nacimiento"] != nac:
        return None, "No encontramos ese DNI con esa fecha de nacimiento."
    token = secrets.token_urlsafe(24)
    with closing(con()) as c, c:
        if not ch["nacimiento"]:            # el padrón no la tenía (ej. lista sin esa columna): queda la que dio él
            c.execute("UPDATE choferes SET nacimiento=? WHERE dni=?", (nac, dni))
            ch["nacimiento"] = nac
        c.execute("DELETE FROM chofer_sesiones WHERE vence < ?", (time.time(),))
        c.execute("INSERT INTO chofer_sesiones VALUES (?,?,?)", (token, dni, time.time() + SESION_CHOFER))
    return token, ch


def chofer_de_token(token):
    if not token:
        return None
    with closing(con()) as c:
        r = c.execute("SELECT dni FROM chofer_sesiones WHERE token=? AND vence>?", (str(token), time.time())).fetchone()
    ch = chofer(r["dni"]) if r else None
    return ch if ch and ch["activo"] else None


def mis_cursos(ch, resultados):
    """Para la portada del chofer: sus cursos asignados (con estado) + los abiertos que no tiene asignados."""
    asign = cumplimiento([r for r in resultados if r["dni"] == ch["dni"]], dni=ch["dni"])
    habilitados = {c["slug"]: c for c in cursos.listar(solo_habilitados=True)}
    orden = {"atrasado": 0, "vencido": 1, "pendiente": 2, "por_vencer": 3, "aprobado": 4}
    asignados = sorted(({"slug": a["curso"], "titulo": a["curso_titulo"],
                         "descripcion": habilitados[a["curso"]].get("descripcion", ""), "estado": a["estado"],
                         "estado_txt": a["estado_txt"], "limite": a["limite"], "vence": a["vence"],
                         "aprobado_el": a["aprobado_el"], "intentos": a["intentos"]}
                        for a in asign if a["curso"] in habilitados), key=lambda x: orden[x["estado"]])
    ya = {a["slug"] for a in asignados}
    abiertos = [{"slug": c["slug"], "titulo": c["titulo"], "descripcion": c.get("descripcion", "")}
                for c in habilitados.values() if c.get("acceso", "abierto") == "abierto" and c["slug"] not in ya]
    return {"asignados": asignados, "abiertos": abiertos}
