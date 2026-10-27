"""Datos simulados del sistema externo (técnicos, OTs y disponibilidad), en memoria."""

import json
import random
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from faker import Faker

from modelos import TIPOS_OT

SEMILLA = 42
# Namespace fijo: uuid4 ignora random.seed y cambiaría los IDs en cada reinicio.
NAMESPACE = uuid.UUID("6f1c2b8e-3d4a-4e5f-9a7b-1c2d3e4f5a6b")

DIAS_ORDENES = 5
DIAS_DISPONIBILIDAD = 14

# Las horas de la API son UTC. Las OTs parten entre 8:00 y 20:00 hora de Chile
# (la última termina antes de las 21:00); se convierten sumando el desfase.
DESFASE_UTC_CHILE = 3
HORA_PRIMERA_OT_CHILE = 8
HORA_ULTIMA_OT_CHILE = 20

# (tipo, región, cantidad)
TECNICOS_POR_GRUPO = [
    ("interno", "Valparaíso", 4),
    ("externo", "Valparaíso", 3),
    ("externo", "Metropolitana de Santiago", 3),
    ("externo", "Libertador General Bernardo O'Higgins", 3),
]

# (región, OTs por día)
ORDENES_POR_REGION = [
    ("Valparaíso", 8),
    ("Metropolitana de Santiago", 5),
    ("Libertador General Bernardo O'Higgins", 5),
]

# { región: [ {lugar, direccion, comuna} ] }
DIRECCIONES = json.loads(
    (Path(__file__).parent / "direcciones_reales_chile.json").read_text(encoding="utf-8")
)

fake = Faker("es_CL")


def _fijar_semillas() -> None:
    random.seed(SEMILLA)
    Faker.seed(SEMILLA)


def _generar_tecnicos() -> list[dict]:
    tecnicos = []
    for tipo, region, cantidad in TECNICOS_POR_GRUPO:
        comunas = [d["comuna"] for d in DIRECCIONES[region]]
        for _ in range(cantidad):
            tecnicos.append({
                "id": str(uuid.uuid5(NAMESPACE, f"tecnico-{len(tecnicos) + 1}")),
                "nombre": fake.first_name(),
                "apellidos": f"{fake.last_name()} {fake.last_name()}",
                "tipo": tipo,
                "zona": random.choice(comunas),
                "region": region,
            })
    return tecnicos


def _generar_ordenes() -> list[dict]:
    """Todas las OTs nacen `por_asignar` y sin técnico."""
    ordenes = []
    hoy = date.today()
    for offset in range(DIAS_ORDENES):
        fecha = hoy + timedelta(days=offset)
        for region, cantidad in ORDENES_POR_REGION:
            for direccion in random.sample(DIRECCIONES[region], cantidad):
                hora = random.randint(
                    HORA_PRIMERA_OT_CHILE + DESFASE_UTC_CHILE,
                    HORA_ULTIMA_OT_CHILE + DESFASE_UTC_CHILE,
                )
                ordenes.append({
                    "id": f"OT-{len(ordenes) + 1:04d}",
                    "tipo": random.choice(TIPOS_OT),
                    "estado": "por_asignar",
                    "tecnico_id": None,
                    "direccion_instalacion": direccion["direccion"],
                    "comuna": direccion["comuna"],
                    "region": region,
                    "fecha_programada": fecha.isoformat(),
                    "hora_programada": f"{hora:02d}:00",
                })
    return ordenes


def _generar_disponibilidades(tecnicos: list[dict]) -> list[dict]:
    """Los técnicos no tienen horario fijo: todos están disponibles todos los días."""
    hoy = date.today()
    return [
        {
            "id": str(uuid.uuid5(NAMESPACE, f"disponibilidad-{tecnico['id']}-{fecha.isoformat()}")),
            "tecnico_id": tecnico["id"],
            "fecha": fecha.isoformat(),
            "disponible": True,
        }
        for tecnico in tecnicos
        for fecha in (hoy + timedelta(days=offset) for offset in range(DIAS_DISPONIBILIDAD))
    ]


class FuenteDatos:
    """Punto único de acceso a los datos simulados.

    Las OTs se devuelven por referencia: los endpoints las modifican en el lugar.
    Para usar un sistema externo real, basta reemplazar esta clase por otra con
    los mismos métodos.
    """

    def __init__(self) -> None:
        self.reiniciar()

    def reiniciar(self) -> None:
        _fijar_semillas()
        self.tecnicos = _generar_tecnicos()
        self.ordenes = _generar_ordenes()
        self.disponibilidades = _generar_disponibilidades(self.tecnicos)

    def buscar_tecnico(self, tecnico_id: str) -> Optional[dict]:
        return next((t for t in self.tecnicos if t["id"] == tecnico_id), None)

    def buscar_orden(self, orden_id: str) -> Optional[dict]:
        return next((o for o in self.ordenes if o["id"] == orden_id), None)


fuente = FuenteDatos()
