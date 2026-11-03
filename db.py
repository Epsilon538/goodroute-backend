"""Acceso a PostgreSQL (Render). La tabla `asignacion` es un log de eventos: la API solo inserta, salvo el reset
del entorno simulado, que la vacía y vuelve a cargar el historial de la semilla (HU-37).

La API nunca modifica el esquema: los cambios a la tabla se ejecutan a mano en la BD.
"""

import os
from contextlib import contextmanager

import psycopg2

# fecha_asignacion y fecha_generacion usan la hora del servidor si vienen en None.
SQL_INSERTAR_ASIGNACION = """
    INSERT INTO asignacion (
        id_orden_trabajo, id_propuesta, id_aceptada,
        fecha_asignacion, fecha_orden_de_trabajo,
        motivo_reprogramacion, usuario_dispatcher,
        fecha_generacion, propuesta_modificada, tipo_modificacion,
        inicio_planificacion, kilometraje_estimado
    ) VALUES (
        %(id_orden_trabajo)s, %(id_propuesta)s, %(id_aceptada)s,
        COALESCE(%(fecha_asignacion)s::timestamp, NOW()::timestamp),
        %(fecha_orden_de_trabajo)s,
        %(motivo_reprogramacion)s, %(usuario_dispatcher)s,
        COALESCE(%(fecha_generacion)s::timestamp, NOW()::timestamp),
        %(propuesta_modificada)s, %(tipo_modificacion)s,
        %(inicio_planificacion)s::timestamp, %(kilometraje_estimado)s::numeric
    )
"""


def conectar():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("La variable de entorno DATABASE_URL no esta configurada.")
    # Render entrega 'postgres://', psycopg2 espera 'postgresql://'
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)


@contextmanager
def conexion():
    """Conexión con commit al salir, rollback si hay error, y cierre siempre."""
    conn = conectar()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def insertar_asignaciones(registros: list[dict]) -> None:
    """Los campos que no vengan en un registro se guardan como NULL."""
    completos = [
        {"inicio_planificacion": None, "kilometraje_estimado": None, **registro} for registro in registros
    ]
    with conexion() as conn, conn.cursor() as cur:
        cur.executemany(SQL_INSERTAR_ASIGNACION, completos)


def reiniciar_registro(historial: list[dict]) -> tuple[int, int]:
    """Deja `asignacion` con solo el historial de la semilla y vacía `kpi_valor`, en una sola transacción (HU-37).

    Borra también las confirmaciones hechas desde el módulo web. Solo cambia datos, no el esquema.
    Devuelve (filas borradas de `asignacion`, filas del historial insertadas).
    """
    completos = [
        {"inicio_planificacion": None, "kilometraje_estimado": None, **registro} for registro in historial
    ]
    with conexion() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM asignacion")
        borradas = cur.rowcount
        cur.executemany(SQL_INSERTAR_ASIGNACION, completos)
        cur.execute("DELETE FROM kpi_valor")
    return borradas, len(completos)


def leer_confirmaciones(desde: str) -> list[tuple]:
    """(id de OT, técnico aceptado) de las filas de `asignacion` cuya OT es de `desde` en adelante, en el orden
    en que se registraron. Sirve para reaplicarlas en memoria al arrancar (HU-37)."""
    with conexion() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_orden_trabajo, id_aceptada FROM asignacion "
            "WHERE fecha_orden_de_trabajo::date >= %s::date "
            "ORDER BY fecha_asignacion NULLS FIRST, id",
            (desde,),
        )
        return cur.fetchall()


# Igual que SQL_INSERTAR_ASIGNACION, pero omite la fila si ya existe una igual (misma OT, técnicos, motivo y
# fecha de asignación). Sirve para cargar el historial las veces que haga falta sin duplicarlo.
SQL_ASIGNACION_SI_NO_EXISTE = """
    INSERT INTO asignacion (
        id_orden_trabajo, id_propuesta, id_aceptada,
        fecha_asignacion, fecha_orden_de_trabajo,
        motivo_reprogramacion, usuario_dispatcher,
        fecha_generacion, propuesta_modificada, tipo_modificacion,
        inicio_planificacion, kilometraje_estimado
    )
    SELECT
        %(id_orden_trabajo)s, %(id_propuesta)s, %(id_aceptada)s,
        COALESCE(%(fecha_asignacion)s::timestamp, NOW()::timestamp),
        %(fecha_orden_de_trabajo)s,
        %(motivo_reprogramacion)s, %(usuario_dispatcher)s,
        COALESCE(%(fecha_generacion)s::timestamp, NOW()::timestamp),
        %(propuesta_modificada)s, %(tipo_modificacion)s,
        %(inicio_planificacion)s::timestamp, %(kilometraje_estimado)s::numeric
    WHERE NOT EXISTS (
        SELECT 1 FROM asignacion
        WHERE id_orden_trabajo = %(id_orden_trabajo)s
          AND id_aceptada IS NOT DISTINCT FROM %(id_aceptada)s
          AND id_propuesta IS NOT DISTINCT FROM %(id_propuesta)s
          AND motivo_reprogramacion IS NOT DISTINCT FROM %(motivo_reprogramacion)s
          AND fecha_asignacion = %(fecha_asignacion)s::timestamp
    )
"""


def insertar_asignaciones_nuevas(registros: list[dict]) -> int:
    """Inserta solo las filas que aún no están en `asignacion`. Devuelve cuántas insertó."""
    completos = [
        {"inicio_planificacion": None, "kilometraje_estimado": None, **registro} for registro in registros
    ]
    insertados = 0
    with conexion() as conn, conn.cursor() as cur:
        for registro in completos:
            cur.execute(SQL_ASIGNACION_SI_NO_EXISTE, registro)
            insertados += cur.rowcount
    return insertados
