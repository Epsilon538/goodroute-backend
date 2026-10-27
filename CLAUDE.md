# CLAUDE.md

Guía para Claude Code al trabajar en este repositorio.

## Qué es

Backend en FastAPI del módulo asignador y analítico de GoodRoute (sistema de asignación y ruteo
de técnicos para Control Position SpA). Entrega técnicos, OTs y disponibilidad al optimizador
de rutas, registra las asignaciones en PostgreSQL (Render) y calcula KPIs.

Código, identificadores, comentarios y respuestas de la API van en español. Mantenerlo así.

## Comandos

```bash
pip install -r requirements.txt
DATABASE_URL=postgresql://... uvicorn main:app --reload --port 8000   # Swagger en /docs
```

Sin `DATABASE_URL` la API arranca. Los GET funcionan; el PATCH y el POST responden con
`guardado_en_bd: false` / `base_de_datos.error`. No hay linter, build ni tests por ahora.
Render arranca con `uvicorn main:app --host 0.0.0.0 --port $PORT`.

## Estructura (cada archivo, una responsabilidad)

- `main.py`: crea la app, CORS y `lifespan` (llama a `db.asegurar_columnas_nullable()`).
- `rutas.py`: endpoints y helpers de validación. No genera datos ni escribe SQL.
- `datos.py`: datos simulados en memoria. `FuenteDatos` (instancia `fuente`) expone
  `tecnicos`, `ordenes`, `disponibilidades`, `buscar_tecnico`, `buscar_orden` y `reiniciar()`.
- `db.py`: conexión y escrituras en Postgres. Único archivo que inserta en `asignacion`.
- `kpis.py`: OTA, OTR y TAR (solo lectura sobre `asignacion`).
- `modelos.py`: constantes (estados, motivos, tipos) y modelos Pydantic de los request.

Los endpoints leen los datos simulados solo a través de `fuente`, nunca desde otra parte.
Si algún día hay un sistema externo real, se reemplaza esa clase.

## Endpoints

GET `/`, GET `/api/reset`, GET `/api/tecnicos`, GET `/api/ordenes?estado=&fecha=`,
GET `/api/disponibilidad?fecha=`, PATCH `/api/ordenes/{orden_id}/tecnico`,
POST `/api/asignar-tecnicos`.

Otros sistemas (frontend, optimizador) consumen estas rutas y formatos: no cambiar el contrato
sin avisar.

## Reglas del negocio y convenciones

- **`asignacion` es un log de eventos:** cada PATCH y cada OT del POST hace un INSERT nuevo, nunca
  un UPDATE.
- **PATCH:** `id_propuesta` = técnico anterior, `id_aceptada` = técnico nuevo. `tecnico_id: null`
  es "OT descartada" y devuelve la OT a `por_asignar`.
- **POST:** `id_propuesta` sale de `ruta_propuesta` de cada ruta; `id_aceptada` es el técnico de la
  ruta. Estrategia parcial: lo válido se aplica y los errores se reportan sin detener el resto.
  `tipo_modificacion` es el JSON de `modificaciones` truncado a 100 caracteres.
- Asignar un técnico mueve la OT de `por_revisar`/`por_asignar` a `asignacion_por_confirmar`.
- Un fallo de BD no tumba el request: el cambio en memoria ya se aplicó y la respuesta informa el error.
- `MOTIVOS_REPROGRAMACION` debe coincidir con el CHECK de la columna `motivo_reprogramacion`.
- **Las horas son UTC.** Las OTs parten entre 8:00 y 20:00 hora de Chile (la última termina antes
  de las 21:00), que se devuelven como 11:00 a 23:00 UTC (desfase fijo de +3).
- **Disponibilidad:** los técnicos no tienen horario fijo, así que todos están disponibles todos
  los días (14 días desde hoy). Se mantiene el campo `disponible` (siempre `true`) para no romper
  a quien lo consume.
- Datos simulados con semilla fija (`random.seed(42)`, `Faker.seed(42)`) pero con fechas relativas
  a hoy. Los IDs usan `uuid5` determinístico; nunca `uuid4`, porque cambiaría los `id_tecnico` en
  cada reinicio y dejaría huérfanos los registros de `kpi_valor`.
- Hoy hay 13 técnicos y 90 OTs (18 por día durante 5 días).

## KPIs

- `calcular_ota`, `calcular_otr` y `calcular_tar` filtran por `fecha_orden_de_trabajo::date`, sin
  filtro por técnico (una OT reasignada cuenta una vez). OTR y TAR devuelven `numerador` y
  `denominador` por separado, para agregar sumando partes y no promediando porcentajes.
- No están expuestos por endpoint ni escriben en `kpi_valor`; eso lo hace otro servicio.
- **Posible sesgo en TAR (pendiente de decidir):** el denominador incluye las asignaciones por PATCH
  sin motivo, cuyo `id_propuesta` es el técnico anterior (o NULL en una primera asignación) y no la
  propuesta del optimizador. No modificar sin confirmarlo con el dueño del proyecto.

## Tests

Todavía no hay. Los datos de prueba de los KPIs están pendientes: no generar tests ni datos de
prueba de KPIs sin que el usuario lo pida. Cuando existan, parchear `db.conectar` (no hay
`get_db_connection`) y usar un schema temporal para no tocar `public.asignacion`.
