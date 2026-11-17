"""Set de datos ficticio para mostrar el panel (no toca las tablas reales de n8n)."""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOMBRES = ["Juan", "Carlos", "Jorge", "Luis", "Miguel", "Sergio", "Diego", "Pablo", "Marcelo", "Ricardo",
           "Daniel", "Gustavo", "Fernando", "Alejandro", "Martín", "Hernán", "Raúl", "Walter", "Claudio",
           "Matías", "Lucas", "Nicolás", "Federico", "Ramón", "Héctor", "Oscar", "Mariana", "Laura",
           "Silvina", "Romina", "Gabriela", "Patricia"]
APELLIDOS = ["González", "Rodríguez", "Gómez", "Fernández", "López", "Díaz", "Martínez", "Pérez", "García",
             "Sánchez", "Romero", "Sosa", "Álvarez", "Torres", "Ruiz", "Ramírez", "Flores", "Benítez",
             "Acosta", "Medina", "Herrera", "Suárez", "Aguirre", "Giménez", "Gutiérrez", "Pereyra", "Molina",
             "Castro", "Ortiz", "Silva", "Ledesma", "Ojeda"]
# (nombre, cantidad de choferes, calidad 0-1: cuánto saben en promedio, uso de la versión con cámara)
EMPRESAS = [("Transportes del Sur", 38, 0.80, 0.7), ("Logística Pampa", 30, 0.70, 0.5),
            ("Expreso Norte", 26, 0.62, 0.3), ("Rápido Andino", 22, 0.74, 0.8),
            ("Fletes Rivera", 16, 0.55, 0.2), ("TransCuyo", 18, 0.68, 0.6),
            ("Cargas Atlántico", 14, 0.76, 0.5), ("", 8, 0.58, 0.3)]


def generar(banco_path, semilla=2024):
    rnd = random.Random(semilla)
    banco = [q["pregunta"] for q in json.loads(Path(banco_path).read_text())]
    # Algunas preguntas son más difíciles que otras.
    dificultad = {p: min(0.9, max(0.03, rnd.betavariate(2, 5))) for p in banco}
    ahora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    formulario, atencion = [], []
    dnis = set()
    for empresa, n, calidad, uso_camara in EMPRESAS:
        for _ in range(n):
            dni = str(rnd.randint(20_000_000, 45_999_999))
            while dni in dnis:
                dni = str(rnd.randint(20_000_000, 45_999_999))
            dnis.add(dni)
            edad = int(min(68, max(19, rnd.gauss(41, 11))))
            nacimiento = (ahora - timedelta(days=edad * 365.25 + rnd.randint(0, 360))).date().isoformat()
            nombre = f"{rnd.choice(NOMBRES)} {rnd.choice(APELLIDOS)}"
            celular = f"11 {rnd.randint(1500, 6999)} {rnd.randint(1000, 9999)}"
            # La experiencia suma hasta los ~50; después baja un poco.
            habilidad = calidad + (0.06 if 35 <= edad <= 52 else -0.04 if edad < 26 else 0) + rnd.gauss(0, 0.08)
            dia = ahora - timedelta(days=rnd.randint(0, 115))
            for intento in range(5):
                camara = rnd.random() < uso_camara
                fecha = dia + timedelta(hours=rnd.choice([7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21]) + 3 - dia.hour,
                                        minutes=rnd.randint(0, 59))
                fecha = min(fecha, ahora - timedelta(minutes=rnd.randint(5, 600)))
                aten = None
                h = habilidad + 0.05 * intento
                if camara:
                    aten = int(min(100, max(15, rnd.gauss(80 if habilidad > 0.65 else 66, 14))))
                    h += (aten - 70) / 220
                preguntas = rnd.sample(banco, 10)
                falladas = [p for p in preguntas if rnd.random() < dificultad[p] * (2.55 - 2.2 * h)]
                correctas = 10 - len(falladas)
                puntaje = correctas * 10
                fila = {"fecha": fecha.isoformat().replace("+00:00", "Z"), "nombre": nombre, "dni": dni,
                        "celular": celular, "empresa": empresa, "correctas": correctas, "total": 10,
                        "puntaje": puntaje, "aprobado": puntaje >= 70, "errores": " | ".join(falladas),
                        "fecha_nacimiento": nacimiento}
                if camara:
                    estudio = int(max(60, rnd.gauss(420, 200)))
                    fila.update(consentimiento=True, atencion_pct=aten,
                                distracciones=max(0, int(rnd.gauss((100 - aten) / 8, 1.5))),
                                salidas_pantalla=max(0, int(rnd.gauss((100 - aten) / 20, 1))),
                                segundos_estudio=estudio, segundos_sin_atencion=int(estudio * (100 - aten) / 100))
                    if rnd.random() < 0.03:     # algún caso raro para que aparezcan alertas
                        fila["segundos_estudio"] = 65
                    atencion.append(fila)
                else:
                    formulario.append(fila)
                if fila["aprobado"] or rnd.random() < 0.15:
                    break
                dia = fecha + timedelta(days=rnd.choice([0, 0, 1, 2, 3, 7]))
                if dia > ahora:
                    break
    for i, f in enumerate(formulario + atencion, 1):
        f["id"] = i
    return {"formulario": formulario, "atencion": atencion}
