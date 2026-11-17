"""Carga, normaliza y analiza los resultados de la capacitación de choferes."""
import statistics
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import examen, padron

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
APROBACION = 70
RANGOS_EDAD = [(16, 25, "16-25"), (26, 35, "26-35"), (36, 45, "36-45"), (46, 55, "46-55"), (56, 120, "56+")]
RANGOS_ATENCION = [(0, 49, "< 50%"), (50, 69, "50-69%"), (70, 84, "70-84%"), (85, 100, "85-100%")]
FUENTES = {"formulario": "Sin cámara", "camara": "Con control de atención"}

_cache = {"t": 0.0, "filas": []}


def _clave_empresa(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", " ").split())


def _edad(nacimiento, fecha):
    try:
        n = date.fromisoformat(nacimiento)
    except (TypeError, ValueError):
        return None
    f = fecha.date()
    return f.year - n.year - ((f.month, f.day) < (n.month, n.day))


def normalizar(crudo):
    """Une las dos tablas en una lista de filas con el mismo formato."""
    filas = []
    for fuente_tabla, lista in (("formulario", crudo.get("formulario", [])), ("camara", crudo.get("atencion", []))):
        for r in lista:
            fuente = fuente_tabla
            try:
                fecha = datetime.fromisoformat(str(r["fecha"]).replace("Z", "+00:00")).astimezone(TZ)
            except (KeyError, ValueError):
                continue
            # desde 2026-10-28 la página también se hace sin cámara (modo "sin") y hay varios cursos
            if fuente == "camara" and r.get("modo") == "sin":
                fuente = "formulario"
            camara = fuente == "camara"
            filas.append({
                "fuente": fuente, "fecha": fecha, "curso": r.get("curso") or "cursomanejo",
                "nombre": (r.get("nombre") or "").strip().title(),
                "dni": str(r.get("dni") or ""), "celular": r.get("celular") or "",
                "empresa_orig": (r.get("empresa") or "").strip(),
                "nacimiento": r.get("fecha_nacimiento") or "",
                "edad": _edad(r.get("fecha_nacimiento"), fecha),
                "correctas": int(r.get("correctas") or 0), "total": int(r.get("total") or 0),
                "puntaje": int(r.get("puntaje") or 0), "aprobado": bool(r.get("aprobado")),
                "errores": [e.strip() for e in (r.get("errores") or "").split(" | ") if e.strip()],
                "atencion": int(r["atencion_pct"]) if camara and r.get("atencion_pct") is not None else None,
                "distracciones": int(r.get("distracciones") or 0) if camara else None,
                "salidas": int(r.get("salidas_pantalla") or 0) if camara else None,
                "seg_estudio": int(r.get("segundos_estudio") or 0) or None,
            })
    # Misma empresa escrita distinto ("Transp. Sur" / "transp sur") -> se muestra la forma más usada.
    formas = defaultdict(Counter)
    for f in filas:
        formas[_clave_empresa(f["empresa_orig"])][f["empresa_orig"]] += 1
    for f in filas:
        k = _clave_empresa(f["empresa_orig"])
        f["empresa"] = formas[k].most_common(1)[0][0] if k else "Sin empresa"
    filas.sort(key=lambda f: f["fecha"])
    return filas


async def cargar(forzar=False):
    """Resultados guardados por examen.py en panel.db (hasta 2026-11-17 venían de las tablas de n8n: eran pruebas)."""
    if not forzar and time.time() - _cache["t"] < 10:
        return _cache["filas"]
    filas = normalizar({"atencion": examen.resultados()})
    # El transporte sale del padrón cuando el chofer está cargado (no del texto que escribió).
    padron_ = {ch["dni"]: ch for ch in padron.choferes(incluir_inactivos=True)}
    for f in filas:
        ch = padron_.get(f["dni"])
        f["transporte_id"] = ch["transporte_id"] if ch else None
        if ch and ch["transporte"]:
            f["empresa"] = ch["transporte"]
    _cache.update(t=time.time(), filas=filas)
    return _cache["filas"]


def rango_edad(edad):
    if edad is None:
        return "Sin dato"
    for a, b, nombre in RANGOS_EDAD:
        if a <= edad <= b:
            return nombre
    return "Sin dato"


def filtrar(filas, desde=None, hasta=None, empresa="", fuente="", edad="", curso=""):
    out = []
    for f in filas:
        d = f["fecha"].date()
        if desde and d < desde or hasta and d > hasta:
            continue
        if empresa and f["empresa"] != empresa or fuente and f["fuente"] != fuente:
            continue
        if curso and f["curso"] != curso:
            continue
        if edad and rango_edad(f["edad"]) != edad:
            continue
        out.append(f)
    return out


def _prom(xs, dec=0):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    m = statistics.mean(xs)
    return round(m) if dec == 0 else round(m, dec)


def _pct(a, b):
    return round(a * 100 / b) if b else None


def _grupo(filas):
    """Indicadores de un grupo de evaluaciones."""
    return {
        "evaluaciones": len(filas),
        "choferes": len({f["dni"] for f in filas}),
        "aprobacion": _pct(sum(f["aprobado"] for f in filas), len(filas)),
        "puntaje": _prom([f["puntaje"] for f in filas]),
        "atencion": _prom([f["atencion"] for f in filas]),
    }


def por_chofer(filas):
    grupos = defaultdict(list)
    for f in filas:
        grupos[f["dni"]].append(f)
    out = []
    for dni, ev in grupos.items():
        ev.sort(key=lambda f: f["fecha"])
        ultimo = ev[-1]
        primer_ok = next((i for i, f in enumerate(ev, 1) if f["aprobado"]), None)
        out.append({
            "dni": dni, "nombre": ultimo["nombre"], "empresa": ultimo["empresa"], "celular": ultimo["celular"],
            "edad": ultimo["edad"], "intentos": len(ev), "aprobado": primer_ok is not None,
            "intentos_hasta_aprobar": primer_ok, "mejor": max(f["puntaje"] for f in ev),
            "ultimo": ultimo["puntaje"], "ultima_fecha": ultimo["fecha"].strftime("%Y-%m-%d %H:%M"),
            "atencion": _prom([f["atencion"] for f in ev]),
            "desaprobados": sum(not f["aprobado"] for f in ev),
        })
    out.sort(key=lambda c: c["ultima_fecha"], reverse=True)
    return out


def analizar(filas, banco_total=0):
    if not filas:
        return {"vacio": True}
    choferes = por_chofer(filas)
    camara = [f for f in filas if f["fuente"] == "camara"]
    aprobados = [c for c in choferes if c["aprobado"]]

    kpis = _grupo(filas)
    kpis.update({
        "choferes_aprobados": _pct(len(aprobados), len(choferes)),
        "primer_intento": _pct(sum(c["intentos_hasta_aprobar"] == 1 for c in aprobados), len(choferes)),
        "intentos_prom": _prom([c["intentos_hasta_aprobar"] for c in aprobados], 1),
        "pendientes": len(choferes) - len(aprobados),
        "con_camara": _pct(len(camara), len(filas)),
        "distracciones": _prom([f["distracciones"] for f in camara], 1),
        "minutos_estudio": _prom([f["seg_estudio"] / 60 for f in camara if f["seg_estudio"]], 1),
        "con_edad": _pct(sum(f["edad"] is not None for f in filas), len(filas)),
        "edad_prom": _prom([c["edad"] for c in choferes]),
    })

    # Serie temporal: por día hasta 62 días de rango, después por semana (lunes).
    primero, ultimo = filas[0]["fecha"].date(), filas[-1]["fecha"].date()
    semanal = (ultimo - primero).days > 62
    clave = (lambda d: d - timedelta(days=d.weekday())) if semanal else (lambda d: d)
    serie = defaultdict(lambda: [0, 0])
    for f in filas:
        k = clave(f["fecha"].date())
        serie[k][0] += 1
        serie[k][1] += f["aprobado"]
    dias, d = [], clave(primero)
    while d <= ultimo:
        dias.append(d)
        d += timedelta(days=7 if semanal else 1)
    tiempo = {"semanal": semanal, "etiquetas": [x.strftime("%d/%m") for x in dias],
              "evaluaciones": [serie[x][0] for x in dias], "aprobadas": [serie[x][1] for x in dias]}

    def agrupado(clave_fn, orden=None):
        g = defaultdict(list)
        for f in filas:
            g[clave_fn(f)].append(f)
        claves = orden if orden else sorted(g, key=lambda k: -len(g[k]))
        return [{"nombre": k, **_grupo(g[k])} for k in claves if g.get(k)]

    empresas = agrupado(lambda f: f["empresa"])
    edades = agrupado(lambda f: rango_edad(f["edad"]), [r[2] for r in RANGOS_EDAD] + ["Sin dato"])
    fuentes = [dict(x, nombre=FUENTES[x["nombre"]]) for x in agrupado(lambda f: f["fuente"], list(FUENTES))]

    def rango_aten(a):
        return next(n for lo, hi, n in RANGOS_ATENCION if lo <= a <= hi)
    atencion = [dict(x) for x in agrupado(lambda f: rango_aten(f["atencion"]) if f["atencion"] is not None else None,
                                          [r[2] for r in RANGOS_ATENCION])]

    histograma = [0] * 11
    for f in filas:
        histograma[min(10, max(0, f["puntaje"] // 10))] += 1

    dias_semana = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    por_dia = Counter(f["fecha"].weekday() for f in filas)
    por_hora = Counter(f["fecha"].hour for f in filas)

    fallas = Counter(e for f in filas for e in f["errores"])
    # Cada pregunta sale en ~total/banco de los exámenes: se estima cuántas veces apareció.
    preguntas_por_examen = _prom([f["total"] for f in filas]) or 10
    apariciones = len(filas) * preguntas_por_examen / banco_total if banco_total else None
    preguntas = [{"pregunta": p, "fallas": n,
                  "pct": min(100, round(n * 100 / apariciones)) if apariciones else None}
                 for p, n in fallas.most_common(15)]

    dispersion = [{"x": f["atencion"], "y": f["puntaje"], "nombre": f["nombre"]} for f in camara]

    # Alertas para el evaluador.
    alertas = []
    for c in choferes:
        if not c["aprobado"] and c["desaprobados"] >= 3:
            alertas.append({"tipo": "reprobados", "dni": c["dni"], "nombre": c["nombre"], "empresa": c["empresa"],
                            "texto": f"{c['desaprobados']} intentos sin aprobar (mejor nota {c['mejor']}%)"})
    for f in camara:
        if f["aprobado"] and f["atencion"] is not None and f["atencion"] < 50:
            alertas.append({"tipo": "atencion", "dni": f["dni"], "nombre": f["nombre"], "empresa": f["empresa"],
                            "texto": f"Aprobó con {f['puntaje']}% pero con atención de {f['atencion']}% "
                                     f"({f['fecha'].strftime('%d/%m %H:%M')})"})
    for f in camara:
        if f["seg_estudio"] is not None and f["seg_estudio"] < 90 and f["aprobado"]:
            alertas.append({"tipo": "estudio", "dni": f["dni"], "nombre": f["nombre"], "empresa": f["empresa"],
                            "texto": f"Aprobó estudiando solo {f['seg_estudio']} s ({f['fecha'].strftime('%d/%m %H:%M')})"})

    hallazgos = _hallazgos(kpis, empresas, edades, atencion, preguntas)

    return {
        "vacio": False, "kpis": kpis, "tiempo": tiempo, "empresas": empresas, "edades": edades,
        "fuentes": fuentes, "atencion": atencion, "histograma": histograma,
        "dias": {"etiquetas": dias_semana, "valores": [por_dia[i] for i in range(7)]},
        "horas": {"etiquetas": [f"{h:02d}" for h in range(24)], "valores": [por_hora[h] for h in range(24)]},
        "preguntas": preguntas, "dispersion": dispersion, "choferes": choferes,
        "alertas": alertas[:50], "hallazgos": hallazgos,
    }


def _hallazgos(kpis, empresas, edades, atencion, preguntas):
    """Frases cortas con lo más destacable, para leer de un vistazo (HTML: todo dato de usuario va escapado)."""
    from html import escape
    out = []
    empresas = [dict(e, nombre=escape(e["nombre"])) for e in empresas]
    emp = [e for e in empresas if e["evaluaciones"] >= 5 and e["nombre"] != "Sin empresa"]
    if len(emp) >= 2:
        mejor = max(emp, key=lambda e: e["aprobacion"])
        peor = min(emp, key=lambda e: e["aprobacion"])
        if mejor["aprobacion"] - peor["aprobacion"] >= 10:
            out.append(f"<b>{mejor['nombre']}</b> tiene la mejor aprobación ({mejor['aprobacion']}%); "
                       f"<b>{peor['nombre']}</b> la más baja ({peor['aprobacion']}%).")
    ed = [e for e in edades if e["nombre"] != "Sin dato" and e["evaluaciones"] >= 5]
    if len(ed) >= 2:
        mejor = max(ed, key=lambda e: e["puntaje"])
        peor = min(ed, key=lambda e: e["puntaje"])
        if mejor["puntaje"] - peor["puntaje"] >= 5:
            out.append(f"Por edad, el grupo <b>{mejor['nombre']}</b> saca mejores notas (promedio {mejor['puntaje']}%) "
                       f"que <b>{peor['nombre']}</b> ({peor['puntaje']}%).")
    at = [a for a in atencion if a["evaluaciones"] >= 5]
    if len(at) >= 2 and at[-1]["aprobacion"] is not None and at[0]["aprobacion"] is not None:
        dif = at[-1]["aprobacion"] - at[0]["aprobacion"]
        if dif >= 10:
            out.append(f"Los que estudian con atención <b>{at[-1]['nombre']}</b> aprueban un <b>{at[-1]['aprobacion']}%</b>, "
                       f"contra {at[0]['aprobacion']}% de los que tienen <b>{at[0]['nombre']}</b>.")
    if kpis.get("primer_intento") is not None:
        out.append(f"El <b>{kpis['primer_intento']}%</b> de los choferes aprueba en el primer intento; "
                   f"quedan <b>{kpis['pendientes']}</b> sin aprobar todavía.")
    if preguntas:
        p = preguntas[0]
        out.append(f"La pregunta más difícil: «{escape(p['pregunta'])}» ({p['fallas']} errores).")
    return out


def csv_choferes(choferes):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["DNI", "Nombre", "Empresa", "Celular", "Edad", "Intentos", "Aprobado", "Intentos hasta aprobar",
                "Mejor nota", "Última nota", "Última evaluación", "Atención promedio"])
    for c in choferes:
        w.writerow([c["dni"], c["nombre"], c["empresa"], c["celular"], c["edad"] or "", c["intentos"],
                    "Sí" if c["aprobado"] else "No", c["intentos_hasta_aprobar"] or "", c["mejor"], c["ultimo"],
                    c["ultima_fecha"], "" if c["atencion"] is None else c["atencion"]])
    return "﻿" + buf.getvalue()   # BOM: Excel abre bien las tildes
