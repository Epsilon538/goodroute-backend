from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rutas import router, router_v1


app = FastAPI(
    title="GoodRoute Backend",
    description=(
        "Backend del modulo asignador y analitico de GoodRoute: entrega tecnicos "
        "y OTs al optimizador de rutas, registra las asignaciones "
        "y calcula indicadores operacionales."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(router_v1)
