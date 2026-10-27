"""Historial sintético de OTs ya ejecutadas, con su documento de inicio, sus comprobantes y las
asignaciones que las originaron.

Alimenta los KPI de la tabla de asignaciones (OTA, OTR, TAR, TPL; HU-11) y los de ejecución
(TPS, TTR, OTX, OTP; HU-26 y HU-28). El orden de generación es el del negocio: el optimizador
propone un técnico, la dispatcher lo acepta o lo cambia, a veces reprograma, y recién entonces
el técnico atiende sus OTs del día en orden, con traslados entre una visita y la siguiente.
"""

import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from generadores import (
    DIRECCIONES,
    HORA_PRIMERA_OT,
    HORA_ULTIMA_OT,
    NAMESPACE,
    ORDENES_POR_REGION,
    azar_de,
    fake,
    iso_utc,
    nueva_orden,
    programada_utc,
    rut_aleatorio,
)
from modelos import MOTIVOS_REPROGRAMACION, TIPOS_OT

DIAS_HISTORIAL = 30

# Total de OTs por día de la semana (lunes = 0). Se reparte entre regiones en la proporción de ORDENES_POR_REGION.
OTS_POR_DIA_SEMANA = [20, 18, 18, 18, 16, 8, 3]

# --- Duración de las visitas (minutos) ---
DURACION_BASE_MIN = {"instalacionSimple": 40, "instalacionConCorte": 40, "mantencion": 30, "retiro": 30}
VARIACION_DURACION_MIN = 5  # ± alrededor de la base
PROBABILIDAD_VISITA_LARGA = 0.08
EXTRA_VISITA_LARGA_MIN = (15, 40)

# --- Traslados y hora programada ---
TRASLADO_MIN = (20, 75)  # desde la base (primera visita) o desde la visita anterior
VELOCIDAD_MEDIA_KMH = 35  # convierte el traslado en kilometraje estimado
REDONDEO_HORA_MIN = 15  # la hora programada se redondea hacia arriba a este múltiplo

# --- Llegada respecto de la hora programada (minutos) ---
TOLERANCIA_ANTES_MIN = 30
TOLERANCIA_DESPUES_MIN = 15
PROBABILIDAD_ATRASO = 0.12  # llegadas fuera de la tolerancia
ATRASO_EXTRA_MIN = (1, 30)  # cuánto se pasa de la tolerancia

# --- Estado final de cada OT ---
# `enviadaCobranza` no se genera: los KPI solo cuentan `finalizada` como cierre.
# `enTerreno` (sigue instalando) y `asignada` (nunca llegó) solo pueden ser la última visita del día
# del técnico: si fuera una del medio, la siguiente no tendría sentido.
MEZCLA_ULTIMA_VISITA = [("finalizada", 75), ("parcial", 10), ("enTerreno", 5), ("asignada", 10)]
MEZCLA_OTRAS_VISITAS = [("finalizada", 75), ("parcial", 10)]
ESTADOS_ULTIMA, PESOS_ULTIMA = zip(*MEZCLA_ULTIMA_VISITA)
ESTADOS_OTRAS, PESOS_OTRAS = zip(*MEZCLA_OTRAS_VISITAS)

# --- Asignaciones (HU-11) ---
DISPATCHERS = ["Camila Soto", "Matías Vergara"]
PROBABILIDAD_MODIFICADA = 0.15  # la dispatcher cambia al técnico propuesto
PROBABILIDAD_REPROGRAMACION = 0.08  # después de confirmar, la OT pasa a otro técnico
INICIO_PLANIFICACION_HORA = (14, 17)  # hora de Chile del día anterior a la OT
GENERACION_MIN = (1, 3)  # inicio de planificación -> corrida del optimizador
REVISION_MIN = (5, 25)  # corrida del optimizador -> confirmación de la dispatcher
MARGEN_REPROGRAMACION_MIN = (60, 900)  # después de la confirmación original

# Anomalía: el día laboral más reciente con al menos DIAS_ATRAS_ANOMALIA días de antigüedad.
DIAS_ATRAS_ANOMALIA = 8
PROBABILIDAD_MODIFICADA_ANOMALA = 0.6
PROBABILIDAD_REPROGRAMACION_ANOMALA = 0.55
REVISION_ANOMALA_MIN = (60, 120)
MOTIVOS_ANOMALIA = ["Técnico No Disponible", "Problema de Ruta"]

TIPO_MODIFICACION = "OT asignada a otro técnico"

# --- Ubicación del check-in ---
# Centro aproximado de cada región (lat, lng), alrededor del cual caen los check-in.
CENTRO_REGION = {
    "Valparaíso": (-33.05, -71.62),
    "Metropolitana de Santiago": (-33.45, -70.66),
    "Libertador General Bernardo O'Higgins": (-34.17, -70.74),
}
DISPERSION_GRADOS = 0.06
PROBABILIDAD_SIN_UBICACION = 0.05  # el técnico negó el permiso de ubicación

CONSONANTES = "BCDFGHJKLPRSTVWXYZ"


class Historial(NamedTuple):
    ordenes: list[dict]
    documentos_inicio: list[dict]
    comprobantes: list[dict]
    asignaciones: list[dict]


def generar_historial(tecnicos: list[dict], clientes: list[dict], primer_numero: int, semilla: int) -> Historial:
    """Los DIAS_HISTORIAL días previos a hoy."""
    por_region: dict[str, list[dict]] = {}
    for tecnico in tecnicos:
        por_region.setdefault(tecnico["region"], []).append(tecnico)

    hoy = date.today()
    dia_anomalo = _dia_anomalo(hoy)
    resultado = Historial([], [], [], [])
    for dias_atras in range(DIAS_HISTORIAL, 0, -1):
        fecha = hoy - timedelta(days=dias_atras)
        _generar_dia(fecha, dias_atras == dia_anomalo, por_region, clientes, primer_numero, semilla, resultado)
    return resultado


def _dia_anomalo(hoy: date) -> int:
    dias_atras = DIAS_ATRAS_ANOMALIA
    while (hoy - timedelta(days=dias_atras)).weekday() >= 5:
        dias_atras += 1
    return dias_atras


def _repartir(total: int) -> list[int]:
    """Reparte `total` entre las regiones según ORDENES_POR_REGION (mayor resto)."""
    pesos = [cantidad for _, cantidad in ORDENES_POR_REGION]
    exactos = [total * p / sum(pesos) for p in pesos]
    cantidades = [int(e) for e in exactos]
    por_resto = sorted(range(len(pesos)), key=lambda i: exactos[i] - cantidades[i], reverse=True)
    for i in por_resto[: total - sum(cantidades)]:
        cantidades[i] += 1
    return cantidades


def _generar_dia(fecha, anomalo, por_region, clientes, primer_numero, semilla, resultado: Historial) -> None:
    azar = azar_de(semilla, "historial", fecha.isoformat())
    prob_modificada = PROBABILIDAD_MODIFICADA_ANOMALA if anomalo else PROBABILIDAD_MODIFICADA
    prob_reprogramacion = PROBABILIDAD_REPROGRAMACION_ANOMALA if anomalo else PROBABILIDAD_REPROGRAMACION

    inicio = programada_utc(
        fecha - timedelta(days=1), azar.randint(*INICIO_PLANIFICACION_HORA), azar.randint(0, 59)
    )
    generacion = inicio + timedelta(minutes=azar.randint(*GENERACION_MIN))
    asignacion = generacion + timedelta(minutes=azar.randint(*(REVISION_ANOMALA_MIN if anomalo else REVISION_MIN)))
    dispatcher = azar.choice(DISPATCHERS)

    # 1. Qué OTs hay y quién las atiende al final (propuesto -> aceptado -> reprogramado)
    visitas = []
    cantidades = _repartir(OTS_POR_DIA_SEMANA[fecha.weekday()])
    for (region, _), cantidad in zip(ORDENES_POR_REGION, cantidades):
        tecnicos = list(por_region[region])
        azar.shuffle(tecnicos)
        for i, direccion in enumerate(azar.sample(DIRECCIONES[region], cantidad)):
            propuesto = tecnicos[i % len(tecnicos)]
            modificada = azar.random() < prob_modificada
            aceptado = azar.choice([t for t in tecnicos if t is not propuesto]) if modificada else propuesto
            reprogramada = azar.random() < prob_reprogramacion
            final = azar.choice([t for t in tecnicos if t is not aceptado]) if reprogramada else aceptado
            tipo = azar.choice(TIPOS_OT)
            visitas.append({
                "region": region, "direccion": direccion, "tipo": tipo, "cliente": azar.choice(clientes),
                "propuesto": propuesto, "aceptado": aceptado, "final": final,
                "modificada": modificada, "reprogramada": reprogramada,
                "duracion": _duracion(tipo, azar), "traslado": azar.randint(*TRASLADO_MIN),
            })

    # 2. Hora programada: cada técnico encadena sus visitas (cierre + traslado, redondeado)
    por_tecnico: dict[str, list[dict]] = {}
    for visita in visitas:
        por_tecnico.setdefault(visita["final"]["id"], []).append(visita)
    for cadena in por_tecnico.values():
        _programar(cadena, fecha, azar)

    # 3. OTs numeradas por hora programada; cada técnico las atiende en ese orden
    visitas.sort(key=lambda v: v["programada"])
    for visita in visitas:
        visita["orden"] = nueva_orden(
            primer_numero + len(resultado.ordenes), visita["tipo"], visita["direccion"], visita["region"],
            visita["programada"], visita["cliente"], azar,
        )
        resultado.ordenes.append(visita["orden"])

    tiempos = (inicio, generacion, asignacion, dispatcher, anomalo)
    for cadena in por_tecnico.values():
        cadena.sort(key=lambda v: v["programada"])
        previo_cierre = None
        for i, visita in enumerate(cadena):
            ultima = i == len(cadena) - 1
            estado = azar.choices(*((ESTADOS_ULTIMA, PESOS_ULTIMA) if ultima else (ESTADOS_OTRAS, PESOS_OTRAS)))[0]
            filas = _asignaciones(visita, tiempos, azar)
            resultado.asignaciones.extend(filas)
            previo_cierre = _atender(visita, estado, filas, tiempos, previo_cierre, azar, resultado)


def _duracion(tipo: str, azar) -> int:
    minutos = DURACION_BASE_MIN[tipo] + azar.randint(-VARIACION_DURACION_MIN, VARIACION_DURACION_MIN)
    if azar.random() < PROBABILIDAD_VISITA_LARGA:
        minutos += azar.randint(*EXTRA_VISITA_LARGA_MIN)
    return minutos


def _programar(cadena: list[dict], fecha: date, azar) -> None:
    """Fija `programada` en cada visita: la primera al azar, las demás desde la anterior."""
    azar.shuffle(cadena)
    desplazamientos = [0]
    for anterior, actual in zip(cadena, cadena[1:]):
        libre = desplazamientos[-1] + anterior["duracion"] + actual["traslado"]
        desplazamientos.append(math.ceil(libre / REDONDEO_HORA_MIN) * REDONDEO_HORA_MIN)

    primera, ultima = HORA_PRIMERA_OT * 60, HORA_ULTIMA_OT * 60
    maxima = max(primera, ultima - desplazamientos[-1])
    inicio = REDONDEO_HORA_MIN * azar.randint(primera // REDONDEO_HORA_MIN, maxima // REDONDEO_HORA_MIN)
    for visita, desplazamiento in zip(cadena, desplazamientos):
        minutos = inicio + desplazamiento
        visita["programada"] = programada_utc(fecha, minutos // 60, minutos % 60)


def _asignaciones(visita: dict, tiempos: tuple, azar) -> list[dict]:
    """Filas de la tabla `asignacion` de esta OT: la confirmación y, si hubo, la reprogramación."""
    inicio, generacion, asignacion, dispatcher, anomalo = tiempos
    orden, final = visita["orden"], visita["final"]
    km_visita = _kilometraje(visita["traslado"])
    fecha_ot = f"{orden['fechaProgramada']}T{orden['horaProgramada']}:00"
    filas = [{
        "id_orden_trabajo": orden["id"],
        "id_propuesta": visita["propuesto"]["id"],
        "id_aceptada": visita["aceptado"]["id"],
        "fecha_asignacion": iso_utc(asignacion),
        "fecha_orden_de_trabajo": fecha_ot,
        "motivo_reprogramacion": None,
        "usuario_dispatcher": dispatcher,
        "fecha_generacion": iso_utc(generacion),
        "inicio_planificacion": iso_utc(inicio),
        "propuesta_modificada": visita["modificada"],
        "tipo_modificacion": TIPO_MODIFICACION if visita["modificada"] else None,
        "kilometraje_estimado": km_visita if not visita["reprogramada"] else _kilometraje(azar.randint(*TRASLADO_MIN)),
    }]
    if visita["reprogramada"]:
        tarde = min(
            asignacion + timedelta(minutes=azar.randint(*MARGEN_REPROGRAMACION_MIN)),
            visita["programada"] - timedelta(minutes=60),
        )
        motivos = MOTIVOS_ANOMALIA if anomalo else MOTIVOS_REPROGRAMACION
        filas.append({
            "id_orden_trabajo": orden["id"],
            "id_propuesta": visita["aceptado"]["id"],
            "id_aceptada": final["id"],
            "fecha_asignacion": iso_utc(tarde),
            "fecha_orden_de_trabajo": fecha_ot,
            "motivo_reprogramacion": azar.choice(motivos),
            "usuario_dispatcher": dispatcher,
            "fecha_generacion": iso_utc(tarde),
            "inicio_planificacion": None,
            "propuesta_modificada": True,
            "tipo_modificacion": TIPO_MODIFICACION,
            "kilometraje_estimado": km_visita,
        })
    return filas


def _kilometraje(traslado_min: int) -> float:
    return round(traslado_min / 60 * VELOCIDAD_MEDIA_KMH, 1)


def _atender(visita: dict, estado: str, filas: list[dict], tiempos: tuple, previo_cierre, azar, resultado: Historial):
    """Deja la OT en `estado` con sus fechas y devuelve cuándo cierra la visita (o el cierre anterior)."""
    inicio = tiempos[0]
    orden, programada, tecnico_id = visita["orden"], visita["programada"], visita["final"]["id"]
    creada = inicio - timedelta(hours=azar.randint(1, 72))
    ultima_asignacion = max(fila["fecha_asignacion"] for fila in filas)
    orden.update(estado=estado, tecnicoId=tecnico_id, createdAt=iso_utc(creada), updatedAt=ultima_asignacion)

    if estado == "asignada":  # confirmada, pero el técnico nunca llegó
        return previo_cierre

    llegada = programada + timedelta(minutes=_desfase_llegada(azar))
    if previo_cierre:
        llegada = max(llegada, previo_cierre + timedelta(minutes=visita["traslado"]))
    cierre = llegada + timedelta(minutes=visita["duracion"])
    documento = _documento_inicio(orden, tecnico_id, llegada, azar)
    resultado.documentos_inicio.append(documento)

    if estado == "enTerreno":  # hizo check-in y sigue instalando
        orden.update(updatedAt=documento["fecha"], ultimaSincronizacion=documento["fecha"])
        return cierre

    resultado.comprobantes.append(_comprobante(orden, tecnico_id, cierre, documento, estado, azar))
    sincronizada = iso_utc(cierre + timedelta(minutes=azar.randint(1, 3)))
    orden.update(updatedAt=sincronizada, ultimaSincronizacion=sincronizada)
    return cierre


def _desfase_llegada(azar) -> int:
    """Minutos respecto de la hora programada: dentro de la tolerancia, salvo una proporción que se atrasa."""
    if azar.random() < PROBABILIDAD_ATRASO:
        return TOLERANCIA_DESPUES_MIN + azar.randint(*ATRASO_EXTRA_MIN)
    return azar.randint(-TOLERANCIA_ANTES_MIN, TOLERANCIA_DESPUES_MIN)


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
