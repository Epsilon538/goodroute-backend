from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import db
from rutas import router, router_v1


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.asegurar_columnas_nullable()
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
