"""KPIs operacionales del catálogo (HU-24).

De asignación (OTA, OTR, TAR, TPL): se calculan con SQL sobre `asignacion`. Una fila es un evento de
asignación o de reprogramación. Se filtra por el día de la OT (`fecha_orden_de_trabajo`) y no por
cuándo se registró el evento.

De ejecución (OTX, OTP, TPS, TTR): se calculan sobre las OTs, documentos de inicio y comprobantes
del sistema externo, que se reciben como `fuente` (ver `datos.FuenteDatos`). Se filtra por la
fecha programada de la OT.

Todos reciben la fecha (`YYYY-MM-DD`). Los de conteo devuelven un entero; los de promedio o razón
(TAR, TPL, TPS, TTR) devuelven numerador y denominador por separado, para agregar entre fechas
sumando las partes y no promediando. En TPL, TPS y TTR el numerador son minutos.
`tecnico_id` es opcional: sin él se calcula sobre todos los técnicos.
"""

from datetime import datetime, timezone
from typing import Optional

import db
from generadores import ZONA_CHILE

ESTADO_CIERRE = "finalizada"  # `parcial` no cuenta como cierre (clarificado por el cliente)


def _consultar(query: str, *params) -> tuple:
    with db.conexion() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _filtro_tecnico(tecnico_id: Optional[str]) -> tuple[str, tuple]:
    """Cláusula SQL y parámetros para filtrar por el técnico asignado."""
    return ("AND id_aceptada = %s", (tecnico_id,)) if tecnico_id else ("", ())


# --- KPIs de asignación (SQL sobre `asignacion`) ---

def calcular_ota(fecha: str, tecnico_id: Optional[str] = None) -> int:
    """OTA: asignaciones iniciales, es decir filas con técnico aceptado y sin motivo de reprogramación."""
    filtro, params = _filtro_tecnico(tecnico_id)
    (total,) = _consultar(
        f"""
        SELECT COUNT(*)
        FROM asignacion
        WHERE motivo_reprogramacion IS NULL
          AND id_aceptada IS NOT NULL
          AND fecha_orden_de_trabajo::date = %s
          {filtro}
        """,
        fecha, *params,
    )
    return total


def calcular_otr(fecha: str, tecnico_id: Optional[str] = None) -> int:
    """OTR: reprogramaciones, es decir filas con motivo de reprogramación."""
    filtro, params = _filtro_tecnico(tecnico_id)
    (total,) = _consultar(
        f"""
        SELECT COUNT(*)
        FROM asignacion
        WHERE motivo_reprogramacion IS NOT NULL
          AND fecha_orden_de_trabajo::date = %s
          {filtro}
        """,
        fecha, *params,
    )
    return total


def calcular_tar(fecha: str, tecnico_id: Optional[str] = None) -> dict:
    """TAR: propuestas del optimizador que la dispatcher no modificó sobre las propuestas del optimizador (razón).

    Las ediciones manuales (PATCH) se guardan con `id_propuesta` null y `propuesta_modificada` false,
    y no cuentan. Sí cuentan las filas con propuesta, y las OTs que la dispatcher agregó a mano
    antes de confirmar la corrida (sin propuesta, con `propuesta_modificada` true).
    """
    filtro, params = _filtro_tecnico(tecnico_id)
    numerador, denominador = _consultar(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE id_propuesta IS NOT NULL AND propuesta_modificada = false),
            COUNT(*) FILTER (WHERE id_propuesta IS NOT NULL OR propuesta_modificada = true)
        FROM asignacion
        WHERE fecha_orden_de_trabajo::date = %s
          {filtro}
        """,
        fecha, *params,
    )
    return {"numerador": numerador, "denominador": denominador}


def calcular_tpl(fecha: str) -> dict:
    """TPL: minutos entre el inicio de la planificación y la asignación (promedio).

    Solo cuentan las filas con ambas fechas, que son las de la confirmación de una corrida.
    """
    minutos, cantidad = _consultar(
        """
        SELECT
            COALESCE(SUM(EXTRACT(EPOCH FROM fecha_asignacion - inicio_planificacion)) / 60, 0),
            COUNT(*)
        FROM asignacion
        WHERE inicio_planificacion IS NOT NULL
          AND fecha_asignacion IS NOT NULL
          AND fecha_orden_de_trabajo::date = %s
        """,
        fecha,
    )
    return {"numerador": round(float(minutos), 2), "denominador": cantidad}


# --- KPIs de ejecución (sobre los datos del sistema externo) ---

def _momento(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def _minutos(desde: datetime, hasta: datetime) -> float:
    return (hasta - desde).total_seconds() / 60


def _fecha_comprobante(fuente, orden_id: str) -> Optional[datetime]:
    """Fecha del último comprobante de la OT, o None si no tiene."""
    fechas = [_momento(c["fecha"]) for c in fuente.comprobantes_de(orden_id)]
    return max(fechas) if fechas else None


def _del_dia(fuente, fecha: str, tecnico_id: Optional[str]) -> list[dict]:
    return [
        o for o in fuente.ordenes
        if o["fechaProgramada"] == fecha and (tecnico_id is None or o["tecnicoId"] == tecnico_id)
    ]


def _promedio(minutos: list[float]) -> dict:
    return {"numerador": round(sum(minutos), 2), "denominador": len(minutos)}


def calcular_otx(fecha: str, fuente, tecnico_id: Optional[str] = None) -> int:
    """OTX: OTs en estado `finalizada` con fecha programada ese día."""
    return sum(1 for o in _del_dia(fuente, fecha, tecnico_id) if o["estado"] == ESTADO_CIERRE)


def calcular_otp(fecha: str, fuente) -> int:
    """OTP: OTs con fecha programada ese día que, al terminar el día (hora de Chile), no estaban `finalizada`.

    Es el complemento de OTX. El cierre se reconstruye con la fecha del comprobante, así que una OT
    finalizada después del corte cuenta como pendiente en ese día.
    """
    anio, mes, dia = (int(x) for x in fecha.split("-"))
    corte = datetime(anio, mes, dia, 23, 59, 59, tzinfo=ZONA_CHILE).astimezone(timezone.utc)
    pendientes = 0
    for orden in _del_dia(fuente, fecha, None):
        if _momento(orden["createdAt"]) > corte:
            continue
        cierre = _fecha_comprobante(fuente, orden["id"]) if orden["estado"] == ESTADO_CIERRE else None
        if cierre is None or cierre > corte:
            pendientes += 1
    return pendientes


def calcular_tps(fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """TPS: minutos entre el documento de inicio y el comprobante de cada OT (promedio).

    Cuentan las OTs que tienen ambos, también las `parcial`.
    """
    duraciones = []
    for orden in _del_dia(fuente, fecha, tecnico_id):
        documento = fuente.documento_inicio_de(orden["id"])
        cierre = _fecha_comprobante(fuente, orden["id"])
        if documento and cierre:
            duraciones.append(_minutos(_momento(documento["fecha"]), cierre))
    return _promedio(duraciones)


def calcular_ttr(fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """TTR: minutos entre el comprobante de una OT y el documento de inicio de la siguiente del mismo técnico.

    Las visitas del técnico en el día se ordenan por su check-in; solo se mide el tramo que parte
    de una OT con comprobante. Incluye cualquier espera hasta la hora agendada.
    """
    por_tecnico: dict[str, list[tuple[datetime, dict]]] = {}
    for orden in _del_dia(fuente, fecha, tecnico_id):
        documento = fuente.documento_inicio_de(orden["id"])
        if orden["tecnicoId"] and documento:
            por_tecnico.setdefault(orden["tecnicoId"], []).append((_momento(documento["fecha"]), orden))

    traslados = []
    for visitas in por_tecnico.values():
        visitas.sort(key=lambda v: v[0])
        for (_, anterior), (llegada, _) in zip(visitas, visitas[1:]):
            cierre = _fecha_comprobante(fuente, anterior["id"])
            if cierre:
                traslados.append(_minutos(cierre, llegada))
    return _promedio(traslados)


# --- Catálogo: un punto de entrada para los endpoints ---

# tipo: "conteo" (un entero), "promedio" (minutos sobre cantidad) o "razon" (se informa en %).
CATALOGO = {
    "OTA": {"nombre": "OT Asignadas", "unidad": "OT", "tipo": "conteo", "por_tecnico": True},
    "OTR": {"nombre": "OT Reprogramadas", "unidad": "OT", "tipo": "conteo", "por_tecnico": True},
    "OTX": {"nombre": "OT Realizadas con Éxito", "unidad": "OT", "tipo": "conteo", "por_tecnico": True},
    "OTP": {"nombre": "OT Pendientes", "unidad": "OT", "tipo": "conteo", "por_tecnico": False},
    "TTR": {"nombre": "Tiempo de Traslado", "unidad": "min", "tipo": "promedio", "por_tecnico": True},
    "TPS": {"nombre": "Tiempo Promedio de Servicio", "unidad": "min", "tipo": "promedio", "por_tecnico": True},
    "TAR": {"nombre": "Tasa de Aceptación de Rutas", "unidad": "%", "tipo": "razon", "por_tecnico": True},
    "TPL": {"nombre": "Tiempo de Planificación", "unidad": "min", "tipo": "promedio", "por_tecnico": False},
}


def _crudo(sigla: str, fecha: str, fuente, tecnico_id: Optional[str]):
    if sigla == "OTA":
        return calcular_ota(fecha, tecnico_id)
    if sigla == "OTR":
        return calcular_otr(fecha, tecnico_id)
    if sigla == "OTX":
        return calcular_otx(fecha, fuente, tecnico_id)
    if sigla == "OTP":
        return calcular_otp(fecha, fuente)
    if sigla == "TTR":
        return calcular_ttr(fecha, fuente, tecnico_id)
    if sigla == "TPS":
        return calcular_tps(fecha, fuente, tecnico_id)
    if sigla == "TAR":
        return calcular_tar(fecha, tecnico_id)
    return calcular_tpl(fecha)


def calcular(sigla: str, fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """Calcula un KPI del catálogo y lo devuelve normalizado: numerador, denominador y valor.

    En los de conteo, `valor` es el conteo y no hay denominador. En los de promedio, `valor` es
    numerador / denominador. En TAR es ese cociente en %. `valor` es None si el denominador es 0.
    Lanza ValueError si la sigla no existe o si el KPI no se puede calcular por técnico.
    """
    if sigla not in CATALOGO:
        raise ValueError(f"KPI '{sigla}' no existe. Valores permitidos: {', '.join(CATALOGO)}")
    datos = CATALOGO[sigla]
    if tecnico_id and not datos["por_tecnico"]:
        raise ValueError(f"{sigla} no se calcula por técnico")

    crudo = _crudo(sigla, fecha, fuente, tecnico_id)
    if datos["tipo"] == "conteo":
        numerador, denominador, valor = crudo, None, crudo
    else:
        numerador, denominador = crudo["numerador"], crudo["denominador"]
        factor = 100 if datos["tipo"] == "razon" else 1
        valor = round(factor * numerador / denominador, 2) if denominador else None
    return {
        "sigla": sigla,
        "nombre": datos["nombre"],
        "unidad": datos["unidad"],
        "fecha": fecha,
        "tecnico_id": tecnico_id,
        "numerador": numerador,
        "denominador": denominador,
        "valor": valor,
    }
