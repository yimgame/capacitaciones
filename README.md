# Capacitaciones

Plataforma simple para capacitar choferes de transporte: cursos con material, videos y evaluación, control de
atención opcional con la cámara del celular, padrón de transportes y choferes, asignaciones con vencimiento y un
panel para medir cómo vienen.

Todo es **HTML + CSS + JavaScript** en el navegador y un panel en **Python (FastAPI)** con **una sola base SQLite**.
Sin servicios externos.

## Qué hace

**Para el chofer** (`/`)
- Entra con **DNI y fecha de nacimiento** y ve *sus* capacitaciones: pendientes, aprobadas, por vencer, vencidas.
- Cada curso (`/<curso>`): material, videos/PDF/imágenes, tiempo mínimo de estudio y evaluación con preguntas al azar.
- **Control de atención** opcional: la cámara frontal estima si está mirando la pantalla.
  *Todo se procesa en el celular* (MediaPipe): no se graba ni se envía imagen; al servidor solo llegan números
  (porcentaje de atención, distracciones, salidas de la página). Requiere consentimiento.

**Para los capacitadores** (`/panel/`)
- **Cursos**: material, videos (subida por partes, hasta 4 GB), YouTube, modo de atención, tiempo mínimo,
  **preguntas y respuestas propias** (editor o **carga masiva** desde planilla/texto: "1. ¿…? a) … b) …" con hoja
  de clave, o columnas Pregunta | Correcta | Incorrectas), o el banco de la Ley Nacional de Tránsito 24.449; cantidad de preguntas,
  % para aprobar, **acceso abierto o solo asignados**, **vigencia** (anual, cada N meses o sin vencimiento).
- **Versiones de cada curso**: historial de cada guardado (fecha, hora, quién, qué cambió) con restaurar;
  versión v1, v2… cuando se publica una nueva (opcional: que todos los asignados lo vuelvan a hacer); cada
  resultado guarda la versión que rindió el chofer. Vigencia contada desde que aprueba cada chofer o desde la
  fecha de la versión (fecha igual para todos), o hasta una fecha fija.
- **Material**: texto con links, videos (subidos o YouTube), PDF que se ven en la misma pantalla, imágenes y links.
- **Choferes**: alta de a uno o **importación de archivos** (.xlsx, .xls, .csv, .txt) o pegado desde Excel,
  con revisión antes de guardar.
- **Importar desde los sistemas**: maestro de proveedores (transportes, nº de proveedor, JRT, CUIT, contacto),
  exporte de choferes del ERP (DNI → proveedor) y exporte del sistema de documentación de contratistas (DNI, CUIL,
  estado, contratista). Se cruzan solos: el transporte de cada chofer sale del proveedor y, si no, del contratista.
  Los archivos se leen en el navegador; al servidor llegan solo las columnas que usa la capacitación.
- **Celular**: si el padrón no lo tiene, se le pide al chofer cuando entra.
- **Cumplimiento**: semáforo por transporte (filtro por JRT), estado por chofer y curso, **asignar / recursar / cambiar fecha
  límite** a choferes marcados o a todo un transporte (también a los que se carguen después), recordatorio por
  WhatsApp, exportar a Excel.
- **Panel de resultados**: aprobación por transporte, edad, atención vs. nota, preguntas que más fallan, alertas.

**Roles**: administrador · capacitador (cursos, choferes, asignaciones) · evaluador (solo mira) ·
coordinador de transporte (solo ve y carga los choferes de su transporte).

## Instalar

```sh
cp .env.example .env        # poné una contraseña en PANEL_ADMIN_PASSWORD
docker compose up -d --build
```

Abrí `http://localhost:8097/panel/`, entrá con `admin` y la contraseña del `.env` (te pide cambiarla).
La base (`panel.db`) **se crea sola** la primera vez, con dos cursos de ejemplo deshabilitados.

Para publicarlo en internet poné adelante un proxy con HTTPS (la cámara del navegador solo funciona con HTTPS).

## Estructura

```
web/            index.html (portada del chofer) · curso.html (página de cada curso)
nginx.conf      sirve web/, /recursos/ y hace de proxy a /panel/
panel/          Dockerfile + app/ (FastAPI)
  app/main.py       rutas
  app/cursos.py     cursos, material, videos, preguntas
  app/examen.py     arma y corrige la evaluación, guarda resultados
  app/padron.py     transportes, choferes, asignaciones, vigencia, ingreso del chofer
  app/datos.py      estadísticas del panel
  app/auth.py       usuarios, roles, sesiones (Argon2id)
  app/respaldo.py   backup diario de la base (/data/respaldos, 30 días)
recursos/       videos y archivos subidos (no va al repo)
```

## Datos y privacidad

- Nada de producción va al repo: `.env`, la base y `recursos/` están en `.gitignore`.
- Las respuestas correctas nunca llegan al navegador: el examen se arma y se corrige en el servidor.
- Del control de atención solo se guardan números, nunca imágenes ni datos de la cara.
