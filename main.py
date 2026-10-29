import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import precalculo
from datos import fuente
from rutas import router, router_v1


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Al arrancar, guarda en kpi_valor los KPI de los últimos 30 días, en segundo plano."""
    if os.environ.get("DATABASE_URL"):
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
