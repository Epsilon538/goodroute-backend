import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import db
import precalculo
from datos import fuente
from rutas import router, router_v1


log = logging.getLogger("uvicorn.error")


def recargar_confirmaciones() -> None:
    """Reaplica en memoria las asignaciones guardadas de hoy en adelante (HU-37, opción A del PO).

    Las OTs viven en memoria y un reinicio las devolvería a `porAsignar`, aunque sus confirmaciones sigan en la
    BD. Así, después de un reinicio, el Asignador y el dashboard siguen coincidiendo. Nunca lanza.
    """
    try:
        cambiadas = fuente.aplicar_confirmaciones(db.leer_confirmaciones(date.today().isoformat()))
        log.info("Confirmaciones recargadas desde la BD: %s OTs actualizadas en memoria.", cambiadas)
    except Exception as e:
        log.warning("No se pudieron recargar las confirmaciones desde la BD: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Al arrancar, recarga las confirmaciones guardadas y precalcula en kpi_valor los últimos 30 días (en segundo plano)."""
    if os.environ.get("DATABASE_URL"):
        recargar_confirmaciones()
        threading.Thread(target=precalculo.precalcular_al_arrancar, args=(fuente,), daemon=True).start()
    yield


app = FastAPI(
    title="GoodRoute Backend",
    description=(
        "Backend del modulo asignador y analitico de GoodRoute: entrega tecnicos "
        "y OTs al optimizador de rutas, registra las asignaciones "
        "y calcula indicadores operacionales."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(router_v1)
