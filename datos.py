"""Datos simulados del backoffice (técnicos, clientes y OTs), en memoria.

El formato sigue el modelo de datos del backoffice v3.0: campos en camelCase,
fechas en UTC (ISO 8601 con sufijo Z) y disponibilidad como patrón semanal.
Las OTs de hoy en adelante nacen `porAsignar`; las anteriores son historial (`historial.py`).
Técnicos y clientes son siempre los mismos; las OTs cambian en cada reset (semilla distinta).
"""

import random
import uuid
from datetime import date, timedelta
from typing import Optional

from generadores import (
    DIRECCIONES,
    HORA_PRIMERA_OT,
    HORA_ULTIMA_OT,
    NAMESPACE,
    ORDENES_POR_REGION,
    SEMILLA,
    azar_de,
    fake,
    fijar_semillas,
    nombre_usuario,
    nueva_orden,
    programada_utc,
    rut_aleatorio,
    telefono_aleatorio,
)
from historial import generar_historial
from modelos import TIPOS_OT

DIAS_ORDENES = 5
CANTIDAD_CLIENTES = 8

# (tipo, región, cantidad)
TECNICOS_POR_GRUPO = [
    ("interno", "Valparaíso", 4),
    ("externo", "Valparaíso", 3),
    ("externo", "Metropolitana de Santiago", 3),
    ("externo", "Libertador General Bernardo O'Higgins", 3),
]


def _generar_tecnicos() -> list[dict]:
    """Sin horario fijo: la disponibilidad semanal (lunes a domingo) es todo true."""
    tecnicos = []
    for tipo, region, cantidad in TECNICOS_POR_GRUPO:
        comunas = [d["comuna"] for d in DIRECCIONES[region]]
        for _ in range(cantidad):
            numero = len(tecnicos) + 1
            nombre = fake.first_name()
            apellidos = f"{fake.last_name()} {fake.last_name()}"
            zona = random.choice(comunas)
            azar = azar_de(SEMILLA, "tecnico", numero)
            usuario = nombre_usuario(nombre, apellidos)
            tecnicos.append({
                "id": str(uuid.uuid5(NAMESPACE, f"tecnico-{numero}")),
                "nombre": nombre,
                "apellidos": apellidos,
                "rut": rut_aleatorio(azar),
                "tipo": tipo,
                "zona": zona,
                "ciudad": zona,
                "comuna": zona,
                "region": region,
                "telefono": telefono_aleatorio(azar),
                "email": f"{usuario}@example.com",
                "usuario": usuario,
                "activo": True,
                "disponibilidad": [True] * 7,
            })
    return tecnicos


def _generar_clientes() -> list[dict]:
    clientes = []
    for numero in range(1, CANTIDAD_CLIENTES + 1):
        azar = azar_de(SEMILLA, "cliente", numero)
        nombre, apellido = fake.first_name(), fake.last_name()
        region = azar.choice(list(DIRECCIONES.values()))
        clientes.append({
            "id": str(uuid.uuid5(NAMESPACE, f"cliente-{numero}")),
            "razonSocial": fake.company(),
            "rut": rut_aleatorio(azar),
            "direccionFactura": azar.choice(region)["direccion"],
            "contactoNombre": f"{nombre} {apellido}",
            "contactoTelefono": telefono_aleatorio(azar),
            "contactoEmail": f"{nombre_usuario(nombre, apellido)}@example.com",
        })
    return clientes


def _generar_ordenes(clientes: list[dict], semilla: int) -> list[dict]:
    ordenes = []
    hoy = date.today()
    for offset in range(DIAS_ORDENES):
        fecha = hoy + timedelta(days=offset)
        for region, cantidad in ORDENES_POR_REGION:
            for direccion in random.sample(DIRECCIONES[region], cantidad):
                hora = random.randint(HORA_PRIMERA_OT, HORA_ULTIMA_OT)
                tipo = random.choice(TIPOS_OT)
                numero = len(ordenes) + 1
                azar = azar_de(semilla, "ot", numero)
                ordenes.append(nueva_orden(
                    numero, tipo, direccion, region, programada_utc(fecha, hora), azar.choice(clientes), azar,
                ))
    return ordenes


class FuenteDatos:
    """Punto único de acceso a los datos simulados.

    Las OTs se devuelven por referencia: los endpoints las modifican en el lugar.
    Para usar un sistema externo real, basta reemplazar esta clase por otra con
    los mismos métodos.
    """

    def __init__(self) -> None:
        self.reiniciar()

    def reiniciar(self, semilla: Optional[int] = None) -> None:
        """Técnicos y clientes salen siempre iguales; OTs e historial dependen de `semilla`.

        Sin `semilla` se sortea una nueva; queda en `self.semilla` para poder reproducir la corrida.
        """
        fijar_semillas()
        self.tecnicos = _generar_tecnicos()
        self.clientes = _generar_clientes()

        self.semilla = semilla if semilla is not None else random.SystemRandom().randrange(1, 2**31)
        fijar_semillas(self.semilla)
        self.ordenes = _generar_ordenes(self.clientes, self.semilla)
        historial = generar_historial(
            self.tecnicos, self.clientes, primer_numero=len(self.ordenes) + 1, semilla=self.semilla
        )
        self.ordenes += historial.ordenes
        self.documentos_inicio = historial.documentos_inicio
        self.comprobantes = historial.comprobantes
        self.asignaciones = historial.asignaciones
        self.historial_cargado_en_bd = False

    def buscar_tecnico(self, tecnico_id: str) -> Optional[dict]:
        return next((t for t in self.tecnicos if t["id"] == tecnico_id), None)

    def buscar_orden(self, orden_id: str) -> Optional[dict]:
        return next((o for o in self.ordenes if o["id"] == orden_id), None)

    def documento_inicio_de(self, orden_id: str) -> Optional[dict]:
        return next((d for d in self.documentos_inicio if d["otId"] == orden_id), None)

    def comprobantes_de(self, orden_id: str) -> list[dict]:
        return [c for c in self.comprobantes if c["otId"] == orden_id]


fuente = FuenteDatos()
