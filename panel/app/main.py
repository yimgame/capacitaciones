"""Panel de evaluadores — Capacitación de choferes (Ley 24.449).

Se publica en https://<dominio>/panel/ (el nginx del servicio web hace de proxy).
Cursos, preguntas, sesiones de examen, resultados y usuarios viven en /data/panel.db (sin n8n desde 2026-11-17).
"""
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, cursos, datos, demo, examen, padron, respaldo

BASE = Path(__file__).resolve().parent
PREFIJO = "/panel"
BANCO = BASE / "banco_ley_24449.json"
BANCO_TOTAL = len(__import__("json").loads(BANCO.read_text()))
COOKIE = "panel_sesion"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount(PREFIJO + "/static", StaticFiles(directory=BASE / "static"), name="static")
plantillas = Jinja2Templates(directory=BASE / "templates")
plantillas.env.globals["P"] = PREFIJO
_demo = {}


@app.on_event("startup")
def _inicio():
    auth.iniciar()
    cursos.iniciar()
    examen.iniciar()
    padron.iniciar()
    respaldo.iniciar()


def _ip(req):
    return req.headers.get("cf-connecting-ip") or req.headers.get("x-real-ip") or req.client.host


def _https(req):
    return req.headers.get("x-forwarded-proto") == "https" or "https" in req.headers.get("cf-visitor", "")


def _usuario(req):
    return auth.perfil(auth.usuario_de(req.cookies.get(COOKIE)))


def _mismo_origen(req):
    """Defensa CSRF extra (la cookie ya es SameSite=Strict): el POST tiene que venir de esta misma página."""
    origen = req.headers.get("origin") or req.headers.get("referer")
    if origen and urlparse(origen).netloc != req.headers.get("host"):
        raise HTTPException(403, "Origen no permitido")


def _requiere(req, permiso=None):
    """permiso: usuarios | cursos | gestion | choferes (ver auth.PERMISOS). Sin permiso: cualquier usuario del panel."""
    u = _usuario(req)
    if not u:
        raise HTTPException(303, headers={"Location": PREFIJO + "/login"})
    if permiso and permiso not in u["puede"]:
        raise HTTPException(403, "Tu usuario no tiene permiso para esto")
    cursos.USUARIO.set(u["usuario"])          # queda en el historial de los cursos
    return u


def _solo_transporte(u):
    """El coordinador solo ve / toca su transporte."""
    return u["transporte_id"] if u["rol"] == "coordinador" else None


@app.middleware("http")
async def _cabeceras(req, siguiente):
    r = await siguiente(req)
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    r.headers["Cache-Control"] = "no-store"
    return r


# ------------------------------------------------------------------ login
@app.get(PREFIJO + "/login", response_class=HTMLResponse)
def login_form(req: Request):
    return plantillas.TemplateResponse(req, "login.html", {"error": None})


@app.post(PREFIJO + "/login")
def login(req: Request, usuario: str = Form(""), clave: str = Form("")):
    _mismo_origen(req)
    ip = _ip(req)
    if auth.bloqueado(ip):
        return plantillas.TemplateResponse(req, "login.html", {
            "error": "Demasiados intentos fallidos. Esperá 15 minutos."}, status_code=429)
    token = auth.ingresar(usuario, clave, ip)
    if not token:
        return plantillas.TemplateResponse(req, "login.html", {"error": "Usuario o contraseña incorrectos."},
                                           status_code=401)
    r = RedirectResponse(PREFIJO + "/", 303)
    r.set_cookie(COOKIE, token, max_age=auth.SESION_HORAS * 3600, httponly=True, samesite="strict",
                 secure=_https(req), path=PREFIJO)
    return r


@app.post(PREFIJO + "/salir")
def salir(req: Request):
    _mismo_origen(req)
    auth.salir(req.cookies.get(COOKIE))
    r = RedirectResponse(PREFIJO + "/login", 303)
    r.delete_cookie(COOKIE, path=PREFIJO)
    return r


# ------------------------------------------------------------------ panel
@app.get(PREFIJO, include_in_schema=False)
def _sin_barra():
    return RedirectResponse(PREFIJO + "/", 307)


@app.get(PREFIJO + "/", response_class=HTMLResponse)
def panel(req: Request):
    u = _requiere(req)
    if u["cambiar_clave"]:
        return RedirectResponse(PREFIJO + "/cuenta", 303)
    return plantillas.TemplateResponse(req, "panel.html", {"u": u})


async def _filas(es_demo):
    if es_demo:
        if "filas" not in _demo:
            _demo["filas"] = datos.normalizar(demo.generar(BANCO))
        return _demo["filas"]
    return await datos.cargar()


def _fecha(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


async def _filtradas(req, demo_, desde, hasta, empresa, fuente, edad, curso=""):
    u = _usuario(req)
    try:
        todas = await _filas(demo_ == "1")
    except Exception:
        raise HTTPException(502, "No se pudieron leer los resultados. Probá de nuevo en un rato.")
    tid = _solo_transporte(u) if u else None
    if tid and demo_ != "1":
        todas = [f for f in todas if f.get("transporte_id") == tid]
    return todas, datos.filtrar(todas, _fecha(desde), _fecha(hasta), empresa, fuente, edad, curso)


@app.get(PREFIJO + "/api/resumen")
async def resumen(req: Request, demo: str = "", desde: str = "", hasta: str = "", empresa: str = "",
                  fuente: str = "", edad: str = "", curso: str = ""):
    _requiere(req)
    todas, filas = await _filtradas(req, demo, desde, hasta, empresa, fuente, edad, curso)
    c = cursos.obtener(curso) if curso else None
    out = datos.analizar(filas, len(cursos.banco_del_curso(c)) if c else BANCO_TOTAL)
    out["opciones"] = {
        "empresas": sorted({f["empresa"] for f in todas}),
        "edades": [r[2] for r in datos.RANGOS_EDAD] + ["Sin dato"],
        "fuentes": datos.FUENTES,
        "cursos": {c["slug"]: c["titulo"] for c in cursos.listar()} | {
            f["curso"]: f["curso"] for f in todas if f["curso"] not in {c["slug"] for c in cursos.listar()}},
        "min": todas[0]["fecha"].date().isoformat() if todas else None,
        "max": todas[-1]["fecha"].date().isoformat() if todas else None,
    }
    return JSONResponse(out)


@app.get(PREFIJO + "/api/chofer/{dni}")
async def chofer(req: Request, dni: str, demo: str = ""):
    u = _requiere(req)
    filas = [f for f in await _filas(demo == "1") if f["dni"] == dni]
    if _solo_transporte(u) and demo != "1":
        filas = [f for f in filas if f.get("transporte_id") == _solo_transporte(u)]
    if not filas:
        raise HTTPException(404, "No hay evaluaciones de ese DNI")
    return {
        "resumen": datos.por_chofer(filas)[0],
        "nacimiento": filas[-1]["nacimiento"],
        "evaluaciones": [{
            "fecha": f["fecha"].strftime("%d/%m/%Y %H:%M"), "fuente": datos.FUENTES[f["fuente"]],
            "curso": (cursos.obtener(f["curso"]) or {}).get("titulo", f["curso"]) + (f" (v{f['version']})" if f.get("version") else ""),
            "puntaje": f["puntaje"], "correctas": f["correctas"], "total": f["total"], "aprobado": f["aprobado"],
            "atencion": f["atencion"], "distracciones": f["distracciones"], "salidas": f["salidas"],
            "minutos_estudio": round(f["seg_estudio"] / 60, 1) if f["seg_estudio"] else None,
            "errores": f["errores"]} for f in reversed(filas)],
    }


@app.get(PREFIJO + "/exportar.csv")
async def exportar(req: Request, demo: str = "", desde: str = "", hasta: str = "", empresa: str = "",
                   fuente: str = "", edad: str = "", curso: str = ""):
    _requiere(req)
    _, filas = await _filtradas(req, demo, desde, hasta, empresa, fuente, edad, curso)
    nombre = "choferes-demo.csv" if demo == "1" else f"choferes-{date.today().isoformat()}.csv"
    return Response(datos.csv_choferes(datos.por_chofer(filas)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.post(PREFIJO + "/api/actualizar")
async def actualizar(req: Request):
    _mismo_origen(req)
    _requiere(req)
    await datos.cargar(forzar=True)
    return {"ok": True}


@app.get(PREFIJO + "/ayuda", response_class=HTMLResponse)
def ayuda(req: Request):
    return plantillas.TemplateResponse(req, "ayuda.html", {"u": _requiere(req)})


# ------------------------------------------------------------------ cuenta y usuarios
@app.get(PREFIJO + "/cuenta", response_class=HTMLResponse)
def cuenta(req: Request):
    u = _requiere(req)
    return plantillas.TemplateResponse(req, "cuenta.html", {"u": u, "msg": None, "error": None})


@app.post(PREFIJO + "/cuenta", response_class=HTMLResponse)
def cuenta_post(req: Request, actual: str = Form(""), nueva: str = Form(""), repetir: str = Form("")):
    _mismo_origen(req)
    u = _requiere(req)
    error = "Las contraseñas nuevas no coinciden." if nueva != repetir else auth.cambiar_clave(u["usuario"], actual, nueva)
    if error:
        return plantillas.TemplateResponse(req, "cuenta.html", {"u": u, "msg": None, "error": error}, status_code=400)
    return RedirectResponse(PREFIJO + "/", 303)


def _usuarios_pagina(req, u, nuevo=None, error=None):
    return plantillas.TemplateResponse(req, "usuarios.html", {
        "u": u, "usuarios": auth.listar(), "nuevo": nuevo, "error": error, "roles": auth.ROLES,
        "transportes": padron.transportes(solo_activos=True)}, status_code=400 if error else 200)


@app.get(PREFIJO + "/usuarios", response_class=HTMLResponse)
def usuarios(req: Request):
    return _usuarios_pagina(req, _requiere(req, "usuarios"))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@app.post(PREFIJO + "/usuarios", response_class=HTMLResponse)
def usuarios_post(req: Request, accion: str = Form(...), usuario: str = Form(""), nombre: str = Form(""),
                  rol: str = Form("evaluador"), transporte_id: str = Form("")):
    _mismo_origen(req)
    u = _requiere(req, "usuarios")
    nuevo, error = None, None
    try:
        if accion == "crear":
            nuevo = (usuario.strip().lower(), auth.crear(usuario, nombre, rol, _int(transporte_id)))
        elif usuario == u["usuario"]:
            error = "No podés modificar tu propio usuario desde acá (usá Mi cuenta)."
        elif accion == "rol":
            auth.cambiar_rol(usuario, rol, _int(transporte_id))
        elif accion == "blanquear":
            nuevo = (usuario, auth.blanquear(usuario))
        elif accion in ("activar", "desactivar"):
            auth.activar(usuario, accion == "activar")
    except ValueError as e:
        error = str(e)
    return _usuarios_pagina(req, u, nuevo, error)


# ------------------------------------------------------------------ cursos: lo público (lo lee la página del chofer)
@app.get(PREFIJO + "/publico/cursos")
def cursos_publicos():
    """Portada sin ingresar: solo los cursos abiertos."""
    return [{"slug": c["slug"], "titulo": c["titulo"], "descripcion": c["descripcion"]}
            for c in cursos.listar(solo_habilitados=True) if c.get("acceso", "abierto") == "abierto"]


@app.post(PREFIJO + "/publico/chofer/ingresar")
async def chofer_ingresar(req: Request):
    ip = _ip(req)
    if auth.bloqueado(ip):
        return JSONResponse({"ok": False, "error": "Demasiados intentos. Esperá 15 minutos."}, status_code=429)
    try:
        b = await req.json()
    except ValueError:
        b = {}
    token, ch = padron.ingresar_chofer(b.get("dni"), b.get("nacimiento"))
    if not token:
        auth._fallos.setdefault(ip, []).append(__import__("time").time())
        return JSONResponse({"ok": False, "error": ch}, status_code=401)
    return {"ok": True, "token": token, "nombre": ch["nombre"], "transporte": ch["transporte"]}


@app.post(PREFIJO + "/publico/chofer/cursos")
async def chofer_cursos(req: Request):
    try:
        b = await req.json()
    except ValueError:
        b = {}
    ch = padron.chofer_de_token(b.get("token"))
    if not ch:
        return JSONResponse({"ok": False, "error": "Sesión vencida: volvé a ingresar."}, status_code=401)
    return {"ok": True, "nombre": ch["nombre"], "transporte": ch["transporte"], "falta_celular": not ch["celular"],
            **padron.mis_cursos(ch, examen.resultados())}


@app.post(PREFIJO + "/publico/chofer/celular")
async def chofer_celular(req: Request):
    try:
        b = await req.json()
    except ValueError:
        b = {}
    ch = padron.chofer_de_token(b.get("token"))
    if not ch:
        return JSONResponse({"ok": False, "error": "Sesión vencida: volvé a ingresar."}, status_code=401)
    if not padron.guardar_celular(ch["dni"], b.get("celular")):
        return JSONResponse({"ok": False, "error": "Revisá el número (con característica, ej. 11 2345 6789)."},
                            status_code=400)
    return {"ok": True}


@app.get(PREFIJO + "/publico/curso/{slug}")
def curso_publico(slug: str):
    c = cursos.obtener(slug)
    if not c or not c.get("habilitado"):
        raise HTTPException(404, "Curso no disponible")
    return cursos.publico(c)


@app.post(PREFIJO + "/publico/examen/inicio")
async def examen_inicio(req: Request):
    try:
        cuerpo = await req.json()
    except ValueError:
        cuerpo = {}
    r = examen.inicio(cuerpo)
    return JSONResponse(r, status_code=200 if r["ok"] else 400)


@app.post(PREFIJO + "/publico/examen/fin")
async def examen_fin(req: Request):
    try:
        cuerpo = await req.json()
    except ValueError:
        cuerpo = {}
    r = examen.fin(cuerpo)
    if r["ok"]:
        datos._cache["t"] = 0          # que el panel lo muestre enseguida
    return JSONResponse(r, status_code=200 if r["ok"] else 400)


# ------------------------------------------------------------------ cursos: administración
def _cursos_pagina(req, u, error=None, msg=None, status=200):
    return plantillas.TemplateResponse(req, "cursos.html", {"u": u, "cursos": cursos.listar(), "error": error,
                                                            "msg": msg}, status_code=status)


@app.get(PREFIJO + "/cursos", response_class=HTMLResponse)
def cursos_lista(req: Request):
    return _cursos_pagina(req, _requiere(req, "cursos"))


@app.post(PREFIJO + "/cursos", response_class=HTMLResponse)
def cursos_accion(req: Request, accion: str = Form(...), slug: str = Form(""), titulo: str = Form(""),
                  copiar_de: str = Form("")):
    _mismo_origen(req)
    u = _requiere(req, "cursos")
    try:
        if accion == "crear":
            c = cursos.crear(slug, titulo, copiar_de)
            return RedirectResponse(f"{PREFIJO}/cursos/{c['slug']}", 303)
        c = cursos.obtener(slug)
        if not c:
            raise ValueError("No existe ese curso.")
        if accion in ("habilitar", "deshabilitar"):
            c["habilitado"] = accion == "habilitar"
            cursos.guardar(c)
    except ValueError as e:
        return _cursos_pagina(req, u, error=str(e), status=400)
    return RedirectResponse(PREFIJO + "/cursos", 303)


def _editor(req, u, c, error=None, msg=None, status=200):
    return plantillas.TemplateResponse(req, "curso_editar.html", {
        "u": u, "c": c, "material_texto": cursos.material_a_texto(c["material"]), "atencion": cursos.ATENCION,
        "acceso": cursos.ACCESO, "vigencia": cursos.VIGENCIA, "corte": cursos.fecha_corte(c),
        "asignados": len(padron.dnis_asignados(c["slug"])),
        "error": error, "msg": msg, "extensiones": ",".join(cursos.EXTENSIONES)}, status_code=status)


@app.get(PREFIJO + "/cursos/{slug}", response_class=HTMLResponse)
def curso_editar(req: Request, slug: str, msg: str = ""):
    u = _requiere(req, "cursos")
    c = cursos.obtener(slug)
    if not c:
        raise HTTPException(404, "No existe ese curso")
    return _editor(req, u, c, msg={"guardado": "Cambios guardados.", "video": "Video agregado.", "link": "Link agregado.",
                                   "quitado": "Recurso quitado.",
                                   "version": f"Guardado como versión nueva v{c.get('version')}.",
                                   "version_recursar": f"Guardado como versión nueva v{c.get('version')}: "
                                                       f"{req.query_params.get('n', '0')} chofer(es) lo tienen que volver a hacer.",
                                   "restaurado": "Revisión restaurada."}.get(msg))


@app.post(PREFIJO + "/cursos/{slug}", response_class=HTMLResponse)
async def curso_guardar(req: Request, slug: str):
    _mismo_origen(req)
    u = _requiere(req, "cursos")
    f = await req.form()
    try:
        accion = f.get("accion", "guardar")
        if accion == "youtube":
            cursos.agregar_youtube(slug, f.get("url", ""), f.get("titulo_video", ""))
            return RedirectResponse(f"{PREFIJO}/cursos/{slug}?msg=video", 303)
        if accion == "quitar":
            cursos.quitar_recurso(slug, f.get("vid", ""))
            return RedirectResponse(f"{PREFIJO}/cursos/{slug}?msg=quitado", 303)
        if accion == "link":
            cursos.agregar_link(slug, f.get("url", ""), f.get("titulo_link", ""))
            return RedirectResponse(f"{PREFIJO}/cursos/{slug}?msg=link", 303)
        c = cursos.actualizar(slug, {k: str(v) for k, v in f.items()})
        if f.get("nueva_version") == "1" and f.get("recursar_todos") == "1":
            n = padron.recursar(slug, padron.dnis_asignados(slug), None, u["usuario"])
            return RedirectResponse(f"{PREFIJO}/cursos/{slug}?msg=version_recursar&n={n}", 303)
        if f.get("nueva_version") == "1":
            return RedirectResponse(f"{PREFIJO}/cursos/{slug}?msg=version", 303)
    except ValueError as e:
        c = cursos.obtener(slug)
        if not c:
            raise HTTPException(404, "No existe ese curso")
        return _editor(req, u, c, error=str(e), status=400)
    return RedirectResponse(f"{PREFIJO}/cursos/{slug}?msg=guardado", 303)


# ------------------------------------------------------------------ cursos: historial de versiones
@app.get(PREFIJO + "/cursos/{slug}/historial", response_class=HTMLResponse)
def curso_historial(req: Request, slug: str, faltan: str = ""):
    u = _requiere(req, "cursos")
    c = cursos.obtener(slug)
    if not c:
        raise HTTPException(404, "No existe ese curso")
    return plantillas.TemplateResponse(req, "curso_historial.html", {"u": u, "c": c, "historial": cursos.historial(slug),
                                                                     "faltan": faltan})


@app.post(PREFIJO + "/cursos/{slug}/historial")
def curso_restaurar(req: Request, slug: str, revision: int = Form(...)):
    _mismo_origen(req)
    _requiere(req, "cursos")
    try:
        faltan = cursos.restaurar(slug, revision)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if faltan:
        from urllib.parse import quote
        return RedirectResponse(f"{PREFIJO}/cursos/{slug}/historial?faltan={quote(' · '.join(faltan))}", 303)
    return RedirectResponse(f"{PREFIJO}/cursos/{slug}?msg=restaurado", 303)


# ------------------------------------------------------------------ cursos: preguntas de la evaluación
@app.get(PREFIJO + "/cursos/{slug}/preguntas", response_class=HTMLResponse)
def preguntas_editar(req: Request, slug: str):
    u = _requiere(req, "cursos")
    c = cursos.obtener(slug)
    if not c:
        raise HTTPException(404, "No existe ese curso")
    return plantillas.TemplateResponse(req, "curso_preguntas.html", {
        "u": u, "c": c, "banco_ley_total": BANCO_TOTAL,
        "asignados": len(padron.dnis_asignados(slug)),
        "datos": {"preguntas": c.get("preguntas") or [], "preguntas_por_examen": c.get("preguntas_por_examen", 10),
                  "aprobacion": c.get("aprobacion", 70)}})


@app.get(PREFIJO + "/api/preguntas/ley")
def preguntas_ley(req: Request):
    _requiere(req, "cursos")
    return [{"pregunta": q["pregunta"], "correcta": q["correcta"], "incorrectas": q["incorrectas"]}
            for q in cursos.banco_ley()]


@app.post(PREFIJO + "/api/preguntas/{slug}")
async def preguntas_guardar(req: Request, slug: str):
    _mismo_origen(req)
    _requiere(req, "cursos")
    b = await req.json()
    try:
        c = cursos.guardar_evaluacion(slug, b)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    n = 0
    if b.get("nueva_version") and b.get("recursar_todos"):
        n = padron.recursar(slug, padron.dnis_asignados(slug), None, _usuario(req)["usuario"])
    return {"ok": True, "preguntas": c["preguntas"], "version": c.get("version"), "revision": c.get("revision"),
            "modificado": c.get("modificado"), "recursados": n}


# ------------------------------------------------------------------ padrón: choferes y transportes
def _choferes_pagina(req, u, msg=None, error=None, status=200):
    tid = _solo_transporte(u)
    filtro_t = tid or _int(req.query_params.get("transporte"))
    inactivos = req.query_params.get("inactivos") == "1"
    return plantillas.TemplateResponse(req, "choferes.html", {
        "u": u, "msg": msg, "error": error, "transportes": padron.transportes(),
        "filtro_t": filtro_t, "buscar": req.query_params.get("buscar", ""), "inactivos": inactivos,
        "choferes": padron.choferes(transporte_id=filtro_t, buscar=req.query_params.get("buscar", ""),
                                    incluir_inactivos=inactivos),
        "cursos": cursos.listar(), "reglas": padron.reglas()}, status_code=status)


@app.get(PREFIJO + "/choferes", response_class=HTMLResponse)
def choferes_lista(req: Request, msg: str = ""):
    return _choferes_pagina(req, _requiere(req), msg={"guardado": "Chofer guardado.", "ok": "Listo."}.get(msg))


@app.post(PREFIJO + "/choferes", response_class=HTMLResponse)
async def choferes_accion(req: Request):
    _mismo_origen(req)
    u = _requiere(req, "choferes")
    f = {k: str(v) for k, v in (await req.form()).items()}
    accion = f.get("accion")
    try:
        if accion == "guardar":
            padron.guardar_chofer({"dni": f.get("dni"), "nombre": f.get("nombre"), "celular": f.get("celular"),
                                   "nacimiento": f.get("nacimiento"), "transporte_id": _int(f.get("transporte_id"))},
                                  u["usuario"], _solo_transporte(u))
            return RedirectResponse(PREFIJO + "/choferes?msg=guardado", 303)
        if accion in ("activar", "desactivar"):
            padron.activar_chofer(f.get("dni"), accion == "activar", _solo_transporte(u))
        elif accion in ("transporte_crear", "transporte_editar"):
            if "gestion" not in u["puede"]:
                raise HTTPException(403, "Sin permiso")
            if accion == "transporte_crear":
                if padron.transporte_id_de(f.get("nombre"), crear=False):
                    raise ValueError("Ese transporte ya existe.")
                if not padron.transporte_id_de(f.get("nombre")):
                    raise ValueError("Escribí el nombre del transporte.")
            else:
                padron.renombrar_transporte(_int(f.get("id")), f.get("nombre"), f.get("activo") == "1")
        return RedirectResponse(PREFIJO + "/choferes?msg=ok", 303)
    except ValueError as e:
        return _choferes_pagina(req, u, error=str(e), status=400)


@app.post(PREFIJO + "/api/importar")
async def importar(req: Request):
    _mismo_origen(req)
    u = _requiere(req, "choferes")
    b = await req.json()
    try:
        r = padron.importar(b.get("texto", ""), _int(b.get("transporte_id")), u["usuario"],
                            simular=b.get("simular", True) is not False, solo_transporte=_solo_transporte(u))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    if _solo_transporte(u):
        r["transportes_nuevos"] = []
    datos._cache["t"] = 0
    return {"ok": True, **r}


@app.post(PREFIJO + "/api/importar-sistemas")
async def importar_sistemas(req: Request):
    """Maestro de proveedores + JDE + GCG (el navegador lee los .xlsx y manda solo las columnas útiles)."""
    _mismo_origen(req)
    u = _requiere(req, "gestion")
    b = await req.json()
    r = padron.importar_sistemas(b.get("maestro") or [], b.get("jde") or [], b.get("gcg") or [],
                                 b.get("opciones") or {}, u["usuario"], simular=b.get("simular", True) is not False)
    datos._cache["t"] = 0
    return {"ok": True, **r}


# ------------------------------------------------------------------ cumplimiento, asignaciones y recursadas
@app.get(PREFIJO + "/cumplimiento", response_class=HTMLResponse)
def cumplimiento_pagina(req: Request):
    u = _requiere(req)
    return plantillas.TemplateResponse(req, "cumplimiento.html", {
        "u": u, "cursos": cursos.listar(), "estados": padron.ESTADOS, "jrts": [] if _solo_transporte(u) else padron.jrts(),
        "transportes": [t for t in padron.transportes() if not _solo_transporte(u) or t["id"] == _solo_transporte(u)]})


def _cumplimiento(u, transporte="", curso="", estado="", jrt=""):
    tid = _solo_transporte(u) or _int(transporte)
    filas = padron.cumplimiento(examen.resultados(), transporte_id=tid, curso=curso or None,
                                jrt=None if _solo_transporte(u) else (jrt or None))
    semaforo = padron.resumen_cumplimiento(filas)
    if estado:
        filas = [f for f in filas if f["estado"] == estado or (estado == "falta" and f["estado"] in
                                                               ("pendiente", "atrasado", "vencido"))]
    return filas, semaforo


@app.get(PREFIJO + "/api/cumplimiento")
def cumplimiento_api(req: Request, transporte: str = "", curso: str = "", estado: str = "", jrt: str = ""):
    u = _requiere(req)
    filas, semaforo = _cumplimiento(u, transporte, curso, estado, jrt)
    claves = ("curso", "curso_titulo", "dni", "nombre", "celular", "transporte", "transporte_id", "jrt", "estado",
              "estado_txt", "limite", "vence", "aprobado_el", "intentos", "mejor", "ultimo")
    return {"filas": [{k: f.get(k) for k in claves} for f in filas], "semaforo": semaforo,
            "reglas": [r for r in padron.reglas() if not _solo_transporte(u) or r["transporte_id"] == _solo_transporte(u)]}


@app.get(PREFIJO + "/cumplimiento.csv")
def cumplimiento_csv(req: Request, transporte: str = "", curso: str = "", estado: str = "", jrt: str = ""):
    import csv
    import io
    u = _requiere(req)
    filas, _ = _cumplimiento(u, transporte, curso, estado, jrt)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["JRT", "Transporte", "DNI", "Chofer", "Celular", "Curso", "Estado", "Fecha límite", "Aprobado el", "Vence",
                "Intentos", "Mejor nota"])
    for f in filas:
        w.writerow([f.get("jrt") or "", f["transporte"] or "", f["dni"], f["nombre"], f["celular"] or "", f["curso_titulo"],
                    f["estado_txt"].split(" ", 1)[1], f["limite"] or "", f["aprobado_el"] or "", f["vence"] or "",
                    f["intentos"], "" if f["mejor"] is None else f["mejor"]])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="cumplimiento-{date.today().isoformat()}.csv"'})


@app.post(PREFIJO + "/api/asignaciones")
async def asignaciones(req: Request):
    """{accion: asignar|recursar|quitar|limite|quitar_regla, curso, dnis: [...] | transporte_id, limite, auto, plazo_dias}"""
    _mismo_origen(req)
    u = _requiere(req, "gestion")
    b = await req.json()
    curso = cursos.obtener(str(b.get("curso") or ""))
    if not curso:
        return JSONResponse({"ok": False, "error": "Elegí un curso."}, status_code=400)
    slug, accion = curso["slug"], b.get("accion")
    limite = padron.normalizar_fecha_limite(b.get("limite"))
    tid = _int(b.get("transporte_id"))
    if tid and not padron.transporte(tid):
        return JSONResponse({"ok": False, "error": "No existe ese transporte."}, status_code=400)
    dnis = [d for d in (padron.normalizar_dni(x) for x in (b.get("dnis") or [])) if d]
    if tid and not dnis and accion != "quitar_regla":
        dnis = [ch["dni"] for ch in padron.choferes(transporte_id=tid)]
    if not dnis and accion != "quitar_regla":
        return JSONResponse({"ok": False, "error": "No hay choferes elegidos."}, status_code=400)
    if accion == "asignar":
        if tid and not b.get("dnis"):
            n = padron.asignar_transporte(slug, tid, limite, _int(b.get("plazo_dias")), bool(b.get("auto")), u["usuario"])
        else:
            n = padron.asignar(slug, dnis, limite, u["usuario"])
        msg = f"Curso asignado a {len(dnis)} chofer(es)."
    elif accion == "limite":
        padron.asignar(slug, dnis, limite, u["usuario"])
        msg = "Fecha límite actualizada."
    elif accion == "recursar":
        padron.recursar(slug, dnis, limite, u["usuario"])
        msg = f"{len(dnis)} chofer(es) tienen que volver a hacer «{curso['titulo']}»."
    elif accion == "quitar":
        padron.quitar(slug, dnis)
        msg = f"Se quitó el curso a {len(dnis)} chofer(es)."
    elif accion == "quitar_regla" and tid:
        padron.quitar_regla(slug, tid)
        msg = "Los choferes nuevos de ese transporte ya no reciben el curso solos."
    else:
        return JSONResponse({"ok": False, "error": "Acción inválida."}, status_code=400)
    return {"ok": True, "mensaje": msg}


MAX_ARCHIVO = 4 * 1024 ** 3          # 4 GB por archivo


@app.post(PREFIJO + "/api/subir/{slug}")
async def subir(req: Request, slug: str, nombre: str, offset: int = 0, total: int = 0, titulo: str = ""):
    """Subida por partes (cada POST trae un pedazo): así pasa por Cloudflare, que corta los cuerpos > 100 MB."""
    _mismo_origen(req)
    _requiere(req, "cursos")
    if not cursos.obtener(slug):
        raise HTTPException(404, "No existe ese curso")
    try:
        archivo = cursos.nombre_seguro(nombre)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not 0 < total <= MAX_ARCHIVO:
        raise HTTPException(400, "Archivo vacío o de más de 4 GB")
    carpeta = cursos.RECURSOS / slug
    carpeta.mkdir(parents=True, exist_ok=True)
    parcial = carpeta / (".subiendo-" + archivo)
    actual = parcial.stat().st_size if parcial.exists() else 0
    if offset == 0 and actual:
        parcial.unlink()
        actual = 0
    if offset != actual:
        return JSONResponse({"ok": False, "esperado": actual}, status_code=409)
    escrito = 0
    with open(parcial, "ab") as fh:
        async for pedazo in req.stream():
            escrito += len(pedazo)
            if actual + escrito > total:
                raise HTTPException(400, "Se recibió más de lo anunciado")
            fh.write(pedazo)
    recibido = actual + escrito
    if recibido < total:
        return {"ok": True, "recibido": recibido}
    final = carpeta / archivo
    base, ext = final.stem, final.suffix
    n = 2
    while final.exists():
        final = carpeta / f"{base}-{n}{ext}"
        n += 1
    parcial.rename(final)
    cursos.agregar_recurso(slug, final.name, titulo.strip()[:120])
    return {"ok": True, "recibido": recibido, "listo": True, "archivo": final.name}


@app.get(PREFIJO + "/salud")
def salud():
    return {"ok": True}
