"""KPIs calculados sobre `asignacion`.

Una misma OT puede tener varias filas (asignación, reprogramaciones, descarte),
por eso se cuenta con COUNT(DISTINCT id_orden_trabajo). Se filtra por el día de
la OT (`fecha_orden_de_trabajo`) y no por cuándo se registró el evento.

Los KPIs de razón devuelven numerador y denominador por separado, para agregar
entre fechas sumando las partes y no promediando porcentajes.
"""

import db


def _consultar(query: str, *params) -> tuple:
    with db.conexion() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def calcular_ota(fecha: str) -> int:
    """OTA: OTs distintas con técnico aceptado (suma). Una OT reasignada cuenta una vez."""
    (total,) = _consultar(
        """
        SELECT COUNT(DISTINCT id_orden_trabajo)
        FROM asignacion
        WHERE id_aceptada IS NOT NULL
          AND fecha_orden_de_trabajo::date = %s
        """,
        fecha,
    )
    return total


def calcular_otr(fecha: str) -> dict:
    """OTR: OTs reprogramadas sobre OTs asignadas (razón). Las descartadas cuentan como reprogramadas."""
    numerador, denominador = _consultar(
        """
        SELECT
            COUNT(DISTINCT id_orden_trabajo) FILTER (WHERE motivo_reprogramacion IS NOT NULL),
            COUNT(DISTINCT id_orden_trabajo)
        FROM asignacion
        WHERE fecha_orden_de_trabajo::date = %s
          AND id_orden_trabajo IN (
              SELECT id_orden_trabajo
              FROM asignacion
              WHERE id_aceptada IS NOT NULL
                AND fecha_orden_de_trabajo::date = %s
          )
        """,
        fecha,
        fecha,
    )
    return {"numerador": numerador, "denominador": denominador}


def calcular_tar(fecha: str) -> dict:
    """TAR: propuestas del optimizador aceptadas sin cambios sobre el total (razón).

    Solo filas sin motivo de reprogramación: en las reprogramaciones manuales
    `id_propuesta` guarda el técnico anterior, no la propuesta del optimizador.
    """
    numerador, denominador = _consultar(
        """
        SELECT
            COUNT(*) FILTER (WHERE id_propuesta = id_aceptada),
            COUNT(*)
        FROM asignacion
        WHERE motivo_reprogramacion IS NULL
          AND fecha_orden_de_trabajo::date = %s
        """,
        fecha,
    )
    return {"numerador": numerador, "denominador": denominador}
