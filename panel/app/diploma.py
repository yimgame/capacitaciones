"""Diploma en PDF del curso aprobado (pedido en la reunión del 18/11: "al final que entregue un PDF diploma").

PDF armado a mano (sin librerías): A4 apaisado, fuentes estándar Helvetica con WinAnsi (acentos y ñ andan).
Cada diploma tiene un código de verificación: el link /panel/publico/diploma/<código> lo vuelve a generar desde la base,
así que un diploma impreso se puede verificar con el link.
"""
import unicodedata
from datetime import date

ANCHO, ALTO = 842, 595            # A4 apaisado, en puntos
AZUL, GRIS, TXT = "0.106 0.310 0.702", "0.40 0.44 0.52", "0.11 0.16 0.22"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

# Anchos de las fuentes estándar (AFM de Adobe, 1/1000 del tamaño) para los caracteres 32..126.
_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, *[556] * 10, 278, 278, 584,
        584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722,
        667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556,
        222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_NEG = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, *[556] * 10, 333, 333, 584,
        584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722,
        667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611,
        278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
FUENTES = {"R": ("Helvetica", _REG), "B": ("Helvetica-Bold", _NEG), "I": ("Helvetica-Oblique", _REG)}


def _ancho(texto, fuente, tam):
    tabla = FUENTES[fuente][1]
    total = 0
    for ch in texto:
        base = unicodedata.normalize("NFKD", ch)[0]          # á -> a (mismo ancho, alcanza para centrar)
        o = ord(base)
        total += tabla[o - 32] if 32 <= o <= 126 else 556
    return total * tam / 1000


def _cadena(texto):
    b = texto.encode("cp1252", "replace")
    return "(" + b.decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


class _Hoja:
    def __init__(self):
        self.ops = []

    def texto(self, t, y, tam, fuente="R", color=TXT, x=None, max_ancho=740):
        while tam > 9 and _ancho(t, fuente, tam) > max_ancho:     # nombres o cursos largos: achica la letra
            tam -= 1
        if x is None:
            x = (ANCHO - _ancho(t, fuente, tam)) / 2
        self.ops.append(f"BT /{fuente} {tam} Tf {color} rg {x:.1f} {y:.1f} Td {_cadena(t)} Tj ET")

    def linea(self, x1, y1, x2, y2, color=GRIS, grosor=0.8):
        self.ops.append(f"q {color} RG {grosor} w {x1} {y1} m {x2} {y2} l S Q")

    def rect(self, x, y, w, h, color=AZUL, grosor=1, relleno=False):
        self.ops.append(f"q {color} {'rg' if relleno else 'RG'} {grosor} w {x} {y} {w} {h} re {'f' if relleno else 'S'} Q")


def fecha_larga(d):
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def _dni(dni):
    s = str(dni or "")
    return f"{int(s):,}".replace(",", ".") if s.isdigit() and len(s) <= 9 else s


def pdf(d):
    """d: nombre, dni, curso, fecha (date), puntaje, version, vence (date|None), codigo, emisor, firma_nombre,
    firma_cargo, url (de verificación)."""
    h = _Hoja()
    h.rect(24, 24, ANCHO - 48, ALTO - 48, grosor=3)
    h.rect(34, 34, ANCHO - 68, ALTO - 68, grosor=0.8)
    h.rect(34, ALTO - 44, ANCHO - 68, 10, relleno=True)
    h.texto((d.get("emisor") or "Capacitación de choferes").upper(), 500, 13, "B", GRIS)
    h.texto("Certificado de capacitación", 450, 32, "B", AZUL)
    h.texto("Se certifica que", 405, 14, "I", GRIS)
    h.texto(d["nombre"], 358, 32, "B", max_ancho=680)
    h.linea(170, 346, ANCHO - 170, 346)
    if d.get("dni"):
        h.texto(f"DNI {_dni(d['dni'])}", 326, 13, "R", GRIS)
    h.texto("aprobó la capacitación", 290, 14, "I", GRIS)
    h.texto(f"«{d['curso']}»", 252, 24, "B", AZUL, max_ancho=700)
    detalle = f"con una calificación de {d['puntaje']}%, el {fecha_larga(d['fecha'])}"
    if d.get("version"):
        detalle += f" (versión {d['version']} del curso)"
    h.texto(detalle, 216, 12.5)
    if d.get("vence"):
        h.texto(f"Válido hasta el {d['vence'].strftime('%d/%m/%Y')}", 196, 12.5, "B")
    if d.get("firma_nombre"):
        h.linea(ANCHO / 2 - 110, 128, ANCHO / 2 + 110, 128, TXT)
        h.texto(d["firma_nombre"], 112, 12, "B")
        if d.get("firma_cargo"):
            h.texto(d["firma_cargo"], 97, 10.5, "R", GRIS)
    h.texto(f"Código de verificación: {d['codigo']}", 62, 9.5, "B", GRIS)
    if d.get("url"):
        h.texto(f"Verificalo en {d['url']}", 49, 8.5, "R", GRIS)
    contenido = "\n".join(h.ops).encode("latin-1")

    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {ANCHO} {ALTO}] /Contents 4 0 R "
        f"/Resources << /Font << /R 5 0 R /B 6 0 R /I 7 0 R >> >> >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream",
        *[f"<< /Type /Font /Subtype /Type1 /BaseFont /{FUENTES[k][0]} /Encoding /WinAnsiEncoding >>".encode()
          for k in ("R", "B", "I")],
        b"<< /Title " + _cadena(f"Diploma - {d['curso']} - {d['nombre']}").encode("latin-1") +
        b" /Producer (Capacitacion de choferes) >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    pos = []
    for i, o in enumerate(objetos, start=1):
        pos.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{p:010d} 00000 n \n".encode() for p in pos)
    out += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R /Info {len(objetos)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


if __name__ == "__main__":         # prueba: python -m app.diploma > /tmp/d.pdf
    import sys
    sys.stdout.buffer.write(pdf({"nombre": "Juan Pérez Muñoz", "dni": "30123456", "curso": "Seguridad vial",
                                 "fecha": date.today(), "puntaje": 90, "version": "v2", "vence": date(2027, 11, 24),
                                 "codigo": "K7Q4-MX2P-9TRA", "emisor": "Capacitación de choferes",
                                 "firma_nombre": "Marianela Ejemplo", "firma_cargo": "Jefa de capacitación",
                                 "url": "https://capacitacion.ejemplo.com/panel/publico/diploma/K7Q4-MX2P-9TRA"}))
