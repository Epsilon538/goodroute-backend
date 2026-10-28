"""Constantes y utilidades compartidas por los generadores de datos simulados."""

import json
import random
import re
import unicodedata
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from faker import Faker

from modelos import ESTADO_POR_ASIGNAR

# Semilla de técnicos y clientes: fija, para que sus IDs no cambien entre resets.
# Las OTs y el historial usan una semilla distinta en cada reset (ver `FuenteDatos.reiniciar`).
SEMILLA = 42
# Namespace fijo: uuid4 ignora random.seed y cambiaría los IDs en cada reinicio.
NAMESPACE = uuid.UUID("6f1c2b8e-3d4a-4e5f-9a7b-1c2d3e4f5a6b")

# La API entrega las horas en UTC; la jornada se define en hora de Chile y se convierte con la
# zona horaria, porque el desfase cambia entre horario de verano (UTC-3) y de invierno (UTC-4).
ZONA_CHILE = ZoneInfo("America/Santiago")
HORA_PRIMERA_OT = 8
HORA_ULTIMA_OT = 19

# Semilla con la que arranca la API. Con ella las OTs y el historial son siempre los mismos al reiniciar,
# y coinciden con lo que ya se guardó en la BD. `/api/reset` sin semilla sortea una distinta.
SEMILLA_INICIAL = 2026

# El id de cada OT sale de su fecha, no del orden de generación: así no cambia con el día en que arranca la API.
FECHA_BASE_IDS = date(2026, 1, 1)
OTS_MAX_POR_DIA = 40

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


def fijar_semillas(semilla: int = SEMILLA) -> None:
    random.seed(semilla)
    Faker.seed(semilla)


def numero_ot(fecha: date, indice_del_dia: int) -> int:
    """Número de OT estable: depende solo de la fecha y de la posición de la OT dentro del día (desde 0)."""
    assert 0 <= indice_del_dia < OTS_MAX_POR_DIA, "demasiadas OTs en un día"
    return (fecha - FECHA_BASE_IDS).days * OTS_MAX_POR_DIA + indice_del_dia + 1


def azar_de(semilla: int, *partes) -> random.Random:
    """Generador propio por entidad, para no alterar la secuencia global de `random`."""
    return random.Random("-".join(str(p) for p in (semilla, *partes)))


def programada_utc(fecha: date, hora: int, minuto: int = 0) -> datetime:
    """Hora de Chile de ese día convertida a UTC."""
    return datetime(fecha.year, fecha.month, fecha.day, hora, minuto, tzinfo=ZONA_CHILE).astimezone(timezone.utc)


def iso_utc(momento: datetime) -> str:
    """Timestamp ISO 8601 en UTC con sufijo Z."""
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ahora_utc() -> str:
    return iso_utc(datetime.now(timezone.utc))


def rut_aleatorio(azar: random.Random) -> str:
    """RUT chileno con dígito verificador válido, formato XX.XXX.XXX-X."""
    cuerpo = azar.randint(10_000_000, 25_000_000)
    factores = [2, 3, 4, 5, 6, 7, 2, 3]
    suma = sum(int(d) * f for d, f in zip(reversed(str(cuerpo)), factores))
    resto = 11 - suma % 11
    digito = "0" if resto == 11 else "K" if resto == 10 else str(resto)
    return f"{cuerpo:,}".replace(",", ".") + f"-{digito}"


def telefono_aleatorio(azar: random.Random) -> str:
    return f"+56 9 {azar.randint(1000, 9999)} {azar.randint(1000, 9999)}"


def nombre_usuario(nombre: str, apellidos: str) -> str:
    """'Ana' + 'Soto Pérez' -> 'ana.soto'."""
    base = unicodedata.normalize("NFKD", f"{nombre}.{apellidos.split()[0]}")
    return re.sub(r"[^a-z.]", "", base.encode("ascii", "ignore").decode().lower())


def nueva_orden(numero: int, tipo: str, direccion: dict, region: str,
                programada: datetime, cliente: dict, azar: random.Random) -> dict:
    """OT recién ingresada: sin técnico, sin problemas reportados y con facturación pendiente.

    `programada` es un instante UTC; entre 8:00 y 19:00 de Chile la fecha UTC y la chilena coinciden.
    """
    ahora = ahora_utc()
    return {
        "id": f"OT-{numero:04d}",
        "padreId": None,
        "tipo": tipo,
        "estado": ESTADO_POR_ASIGNAR,
        "clienteId": cliente["id"],
        "contratoId": f"CT-{azar.randint(1000, 9999)}",
        "tecnicoId": None,
        "direccionFactura": cliente["direccionFactura"],
        "direccionInstalacion": direccion["direccion"],
        "comuna": direccion["comuna"],
        "region": region,
        "contactoNombre": cliente["contactoNombre"],
        "contactoTelefono": cliente["contactoTelefono"],
        "contactoEmail": cliente["contactoEmail"],
        "fechaProgramada": programada.date().isoformat(),
        "horaProgramada": programada.strftime("%H:%M"),
        "problemaInicioReportado": False,
        "motivoNoInicio": None,
        "facturacion": {
            "estado": "pendiente",
            "montoNeto": None,
            "folio": None,
            "fechaEmision": None,
            "baseCalculo": None,
        },
        "ultimaSincronizacion": None,
        "createdAt": ahora,
        "updatedAt": ahora,
    }
