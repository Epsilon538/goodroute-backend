# GoodRoute Backend

Backend del módulo asignador y analítico de GoodRoute, en FastAPI. Entrega técnicos, OTs y
disponibilidad al optimizador de rutas, guarda el histórico de asignaciones en PostgreSQL y
calcula indicadores operacionales (KPIs).

Los técnicos, OTs y disponibilidades se generan de forma simulada en memoria (`datos.py`),
a la espera de conectar el sistema externo real.

## Archivos

| Archivo | Responsabilidad |
|---|---|
| `main.py` | Crea la app, CORS y arranque |
| `rutas.py` | Endpoints |
| `datos.py` | Datos simulados en memoria (`fuente`) |
| `db.py` | Conexión a Postgres e inserción en `asignacion` |
| `kpis.py` | Cálculo de OTA, OTR y TAR |
| `modelos.py` | Constantes (estados, motivos) y modelos de request |
| `direcciones_reales_chile.json` | Direcciones usadas para generar las OTs |

## Ejecutar

```bash
pip install -r requirements.txt
DATABASE_URL=postgresql://... uvicorn main:app --reload --port 8000
```

Swagger en `/docs`. Sin `DATABASE_URL` la API funciona, pero los endpoints de asignación
responden con `guardado_en_bd: false` / `base_de_datos.error`.

En Render: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
