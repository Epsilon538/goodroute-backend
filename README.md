# GoodRoute Backend

Backend del módulo asignador y analítico de GoodRoute, en FastAPI. Entrega técnicos y OTs al
optimizador de rutas, guarda el histórico de asignaciones en PostgreSQL y
calcula indicadores operacionales (KPIs).

Los técnicos, clientes y OTs se generan de forma simulada en memoria (`datos.py`), con el formato
del modelo de datos del backoffice v3.0, a la espera de conectar el sistema externo real.

## Archivos

| Archivo | Responsabilidad |
|---|---|
| `main.py` | Crea la app, CORS y arranque |
| `rutas.py` | Endpoints |
| `datos.py` | Técnicos, clientes y OTs simulados en memoria (`fuente`) |
| `historial.py` | Historial de 14 días: OTs ejecutadas con documento de inicio y comprobantes |
| `generadores.py` | Constantes y utilidades compartidas por los generadores |
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

## Alcance respecto al modelo del backoffice v3.0

Se simulan técnicos, OTs, `disponibilidad` semanal (en el técnico), `DocumentoInicio` y
`Comprobante`. Las OTs de hoy en adelante nacen `porAsignar`; las de los 14 días anteriores son
historial ya ejecutado, para calcular TPS, TTR, OTX y OTP. `GET /api/v1/ordenes/{id}` devuelve la OT
con su `documentoInicio` y sus `comprobantes` (los nombres de esas dos propiedades están por
confirmar con el equipo externo).

No se simulan `Dispositivo`, `HistorialEvento` ni `CostoOT`, y el detalle de la OT los omite. Todas
las OTs llegan con `padreId` null, así que no hay OTs hijas. Los clientes existen solo de forma
interna, para completar `clienteId` y los datos de contacto; no hay endpoint de clientes.
