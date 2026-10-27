"""Historial sintético de OTs ya ejecutadas, con su documento de inicio y sus comprobantes.

Alimenta los KPI TPS, TTR, OTX y OTP del Módulo Analítico (anexo de la HU-26). Cada técnico
atiende sus OTs del día en orden y sin superponerse: el traslado entre el cierre de una visita y
el check-in de la siguiente es lo que mide TTR, y el tiempo entre check-in y cierre, TPS.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from generadores import (
    DIRECCIONES,
    HORA_PRIMERA_OT_UTC,
    HORA_ULTIMA_OT_UTC,
    NAMESPACE,
    ORDENES_POR_REGION,
    azar_de,
    fake,
    iso_utc,
    nueva_orden,
    rut_aleatorio,
)
from modelos import TIPOS_OT

DIAS_HISTORIAL = 14

# (estado, peso). `enviadaCobranza` no se genera: los KPI solo cuentan `finalizada` como cierre.
MEZCLA_ESTADOS = [("finalizada", 75), ("parcial", 10), ("enTerreno", 5), ("asignada", 10)]
ESTADOS, PESOS = zip(*MEZCLA_ESTADOS)

# Centro aproximado de cada región (lat, lng), alrededor del cual caen los check-in.
CENTRO_REGION = {
    "Valparaíso": (-33.05, -71.62),
    "Metropolitana de Santiago": (-33.45, -70.66),
    "Libertador General Bernardo O'Higgins": (-34.17, -70.74),
}
DISPERSION_GRADOS = 0.06
PROBABILIDAD_SIN_UBICACION = 0.05  # el técnico negó el permiso de ubicación

# Rangos en minutos
LLEGADA_RESPECTO_A_PROGRAMADA = (-10, 40)
DURACION_VISITA = (35, 150)
TRASLADO_ENTRE_VISITAS = (20, 75)

CONSONANTES = "BCDFGHJKLPRSTVWXYZ"


def generar_historial(tecnicos: list[dict], clientes: list[dict], primer_numero: int):
    """Devuelve (ordenes, documentos_inicio, comprobantes) de los DIAS_HISTORIAL días previos a hoy."""
    por_region: dict[str, list[dict]] = {}
    for tecnico in tecnicos:
        por_region.setdefault(tecnico["region"], []).append(tecnico)

    ordenes, documentos, comprobantes = [], [], []
    for dias_atras in range(DIAS_HISTORIAL, 0, -1):
        fecha = date.today() - timedelta(days=dias_atras)
        azar = azar_de("historial", dias_atras)
        visitas_por_tecnico: dict[str, list] = {}

        for region, cantidad in ORDENES_POR_REGION:
            for direccion in azar.sample(DIRECCIONES[region], cantidad):
                hora = azar.randint(HORA_PRIMERA_OT_UTC, HORA_ULTIMA_OT_UTC)
                orden = nueva_orden(
                    primer_numero + len(ordenes), azar.choice(TIPOS_OT), direccion,
                    region, fecha, hora, azar.choice(clientes),
                )
                ordenes.append(orden)
                tecnico = azar.choice(por_region[region])
                estado = azar.choices(ESTADOS, PESOS)[0]
                visitas_por_tecnico.setdefault(tecnico["id"], []).append((hora, orden, estado))

        for tecnico_id, visitas in visitas_por_tecnico.items():
            libre_desde = None
            for _, orden, estado in sorted(visitas, key=lambda v: v[0]):
                documento, comprobante, libre_desde = _atender(orden, estado, tecnico_id, azar, libre_desde)
                if documento:
                    documentos.append(documento)
                if comprobante:
                    comprobantes.append(comprobante)

    return ordenes, documentos, comprobantes


def _atender(orden: dict, estado: str, tecnico_id: str, azar, libre_desde):
    """Deja la OT en `estado` con sus fechas y devuelve (documento, comprobante, cuándo queda libre el técnico)."""
    programada = datetime.fromisoformat(
        f"{orden['fechaProgramada']}T{orden['horaProgramada']}:00"
    ).replace(tzinfo=timezone.utc)
    creada = programada - timedelta(hours=azar.randint(24, 96))
    orden.update(estado=estado, tecnicoId=tecnico_id, createdAt=iso_utc(creada), updatedAt=iso_utc(creada))

    if estado == "asignada":  # confirmada, pero el técnico nunca llegó
        orden["updatedAt"] = iso_utc(creada + timedelta(hours=azar.randint(1, 12)))
        return None, None, libre_desde

    llegada = programada + timedelta(minutes=azar.randint(*LLEGADA_RESPECTO_A_PROGRAMADA))
    if libre_desde:
        llegada = max(llegada, libre_desde + timedelta(minutes=azar.randint(*TRASLADO_ENTRE_VISITAS)))
    cierre = llegada + timedelta(minutes=azar.randint(*DURACION_VISITA))
    documento = _documento_inicio(orden, tecnico_id, llegada, azar)

    if estado == "enTerreno":  # hizo check-in y sigue instalando
        orden.update(updatedAt=documento["fecha"], ultimaSincronizacion=documento["fecha"])
        return documento, None, cierre

    comprobante = _comprobante(orden, tecnico_id, cierre, documento, estado, azar)
    sincronizada = iso_utc(cierre + timedelta(minutes=azar.randint(1, 3)))
    orden.update(updatedAt=sincronizada, ultimaSincronizacion=sincronizada)
    return documento, comprobante, cierre


def _documento_inicio(orden: dict, tecnico_id: str, llegada: datetime, azar) -> dict:
    lat, lng = CENTRO_REGION[orden["region"]]
    ubicado = azar.random() >= PROBABILIDAD_SIN_UBICACION
    return {
        "id": str(uuid.uuid5(NAMESPACE, f"documento-inicio-{orden['id']}")),
        "otId": orden["id"],
        "tecnicoId": tecnico_id,
        "fecha": iso_utc(llegada),
        "lat": round(lat + azar.uniform(-DISPERSION_GRADOS, DISPERSION_GRADOS), 5) if ubicado else None,
        "lng": round(lng + azar.uniform(-DISPERSION_GRADOS, DISPERSION_GRADOS), 5) if ubicado else None,
        "nombreRecibe": f"{fake.first_name()} {fake.last_name()}",
        "rutRecibe": rut_aleatorio(azar),
        "firmaImagen": None,
        "observaciones": None,
        "autorizadoPorBO": False,
    }


def _comprobante(orden: dict, tecnico_id: str, cierre: datetime, documento: dict, estado: str, azar) -> dict:
    """`parcial` cubre solo una parte de los dispositivos; el resto queda pendiente."""
    cantidad = azar.randint(2, 3) if estado == "parcial" else azar.randint(1, 3)
    patentes = ["".join(azar.choices(CONSONANTES, k=4)) + f"{azar.randint(0, 99):02d}" for _ in range(cantidad)]
    if estado == "parcial":
        patentes = patentes[: azar.randint(1, cantidad - 1)]
    return {
        "id": str(uuid.uuid5(NAMESPACE, f"comprobante-{orden['id']}")),
        "otId": orden["id"],
        "tecnicoId": tecnico_id,
        "fecha": iso_utc(cierre),
        "direccion": f"{orden['direccionInstalacion']}, {orden['comuna']}",
        "dispositivos": patentes,
        "nombreFirmante": documento["nombreRecibe"],
        "rutFirmante": documento["rutRecibe"],
        "firmaImagen": None,
    }
