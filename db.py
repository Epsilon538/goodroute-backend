"""Acceso a PostgreSQL (Render). La tabla `asignacion` es un log de eventos: solo se inserta.

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
