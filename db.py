"""Acceso a PostgreSQL (Render). La tabla `asignacion` es un log de eventos: solo se inserta."""

import logging
import os
from contextlib import contextmanager

import psycopg2

log = logging.getLogger(__name__)

# Los eventos como "OT descartada" no traen técnico ni otros datos, así que
# solo `id` y `propuesta_modificada` se mantienen NOT NULL.
COLUMNAS_NULLABLE = [
    "id_propuesta",
    "id_aceptada",
    "id_orden_trabajo",
    "fecha_asignacion",
    "fecha_orden_de_trabajo",
    "motivo_reprogramacion",
    "usuario_dispatcher",
    "fecha_generacion",
    "tipo_modificacion",
]

# fecha_asignacion y fecha_generacion usan la hora del servidor si vienen en None.
SQL_INSERTAR_ASIGNACION = """
    INSERT INTO asignacion (
        id_orden_trabajo, id_propuesta, id_aceptada,
        fecha_asignacion, fecha_orden_de_trabajo,
        motivo_reprogramacion, usuario_dispatcher,
        fecha_generacion, propuesta_modificada, tipo_modificacion
    ) VALUES (
        %(id_orden_trabajo)s, %(id_propuesta)s, %(id_aceptada)s,
        COALESCE(%(fecha_asignacion)s::timestamp, NOW()::timestamp),
        %(fecha_orden_de_trabajo)s,
        %(motivo_reprogramacion)s, %(usuario_dispatcher)s,
        COALESCE(%(fecha_generacion)s::timestamp, NOW()::timestamp),
        %(propuesta_modificada)s, %(tipo_modificacion)s
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
    with conexion() as conn, conn.cursor() as cur:
        cur.executemany(SQL_INSERTAR_ASIGNACION, registros)


def asegurar_columnas_nullable() -> None:
    """Se ejecuta al iniciar. Es idempotente; si falla, la API arranca igual."""
    try:
        with conexion() as conn, conn.cursor() as cur:
            for columna in COLUMNAS_NULLABLE:
                cur.execute(f"ALTER TABLE asignacion ALTER COLUMN {columna} DROP NOT NULL")
    except Exception as e:
        log.warning("No se pudo ajustar el esquema de 'asignacion' al iniciar: %s", e)
