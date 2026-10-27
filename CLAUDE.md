# CLAUDE.md

Guía para Claude Code al trabajar en este repositorio.

## Qué es

Backend en FastAPI del módulo asignador y analítico de GoodRoute (sistema de asignación y ruteo
de técnicos para Control Position SpA). Entrega técnicos y OTs al optimizador
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
- `rutas.py`: endpoints y helpers de validación, en dos routers: `router_v1` (prefijo `/api/v1`,
  simula el backoffice) y `router` (endpoints propios de GoodRoute). No genera datos ni escribe SQL.
- `datos.py`: técnicos, clientes y OTs simulados en memoria. `FuenteDatos` (instancia `fuente`) expone
  `tecnicos`, `clientes`, `ordenes`, `documentos_inicio`, `comprobantes`, `buscar_tecnico`,
  `buscar_orden`, `documento_inicio_de`, `comprobantes_de` y `reiniciar()`.
- `historial.py`: historial de 14 días (OTs ejecutadas, documentos de inicio y comprobantes).
- `generadores.py`: constantes y utilidades que comparten `datos.py` e `historial.py`.
- `db.py`: conexión y escrituras en Postgres. Único archivo que inserta en `asignacion`.
- `kpis.py`: OTA, OTR y TAR (solo lectura sobre `asignacion`).
- `modelos.py`: constantes (estados, motivos, tipos) y modelos Pydantic de los request.

Los endpoints leen los datos simulados solo a través de `fuente`, nunca desde otra parte.
Si algún día hay un sistema externo real, se reemplaza esa clase.

## Endpoints

Simulan el backoffice (prefijo `/api/v1`, como en el modelo v3.0): GET `/api/v1/tecnicos`,
GET `/api/v1/ordenes?estado=&fecha=`, GET `/api/v1/ordenes/{orden_id}` (detalle con `documentoInicio`
y `comprobantes`), PATCH `/api/v1/ordenes/{orden_id}/asignar-tecnico`.

Propios de GoodRoute: POST `/api/asignar-tecnicos` (lote de una corrida del optimizador),
GET `/api/reset` y GET `/`.

**Detalle de la OT (HU-26).** `GET /api/v1/ordenes/{id}` agrega `documentoInicio` (null si el técnico no
marcó llegada) y `comprobantes`. Esos dos nombres están por confirmar con el equipo externo. La lista
`GET /api/v1/ordenes` no los incluye. No se simula `PATCH .../disponibilidad`.

## Reglas del negocio y convenciones

- **`asignacion` es un log de eventos:** cada PATCH y cada OT del POST hace un INSERT nuevo, nunca
  un UPDATE.
- **PATCH:** `id_propuesta` = técnico anterior, `id_aceptada` = técnico nuevo. `tecnico_id: null`
  es "OT descartada" y devuelve la OT a `porAsignar`.
- **POST:** `id_propuesta` sale de `ruta_propuesta` de cada ruta; `id_aceptada` es el técnico de la
  ruta. Estrategia parcial: lo válido se aplica y los errores se reportan sin detener el resto.
  `tipo_modificacion` es el JSON de `modificaciones` truncado a 100 caracteres.
- Asignar un técnico mueve la OT de `porRevisar`, `porAsignar` o `enviadaPlanificacion` a
  `asignacionPorConfirmar` (`modelos.ESTADOS_ASIGNABLES`) y actualiza `updatedAt`.
- **`fechaProgramada` es texto libre en el sistema externo.** Al registrar una asignación se exige
  `YYYY-MM-DD` y `horaProgramada` `HH:MM`; si no, la OT se rechaza (422 en el PATCH, entrada en
  `errores` en el POST) y no se aplica ni se guarda nada.
- Un fallo de BD no tumba el request: el cambio en memoria ya se aplicó y la respuesta informa el error.
- `MOTIVOS_REPROGRAMACION` debe coincidir con el CHECK de la columna `motivo_reprogramacion`.
- **Las horas son UTC.** Las OTs parten entre 8:00 y 20:00 hora de Chile (la última termina antes
  de las 21:00), que se devuelven como 11:00 a 23:00 UTC (desfase fijo de +3).
- **Modelo de datos del backoffice v3.0.** Técnicos y OTs usan sus nombres y valores en camelCase
  (`tecnicoId`, `fechaProgramada`, `porAsignar`, `instalacionSimple`). Los 9 estados de OT están en
  `modelos.ESTADOS_OT`; la API solo genera `porAsignar` y mueve a `asignacionPorConfirmar`
  (`enviadaPlanificacion` y `parcial` existen en el enum pero nada los asigna todavía). Los request y
  response propios de GoodRoute (PATCH y POST de asignación, `guardado_en_bd`, `ot_id`) siguen en
  snake_case, igual que las columnas de `asignacion`. Fechas de OT y técnico en UTC con sufijo Z.
- **Disponibilidad:** ya no es una entidad por fecha ni hay endpoint. Es un arreglo `disponibilidad`
  de 7 booleanos (lunes a domingo) dentro de cada técnico. Los técnicos no tienen horario fijo, así que
  todos van con los 7 días en `true`.
- Los campos nuevos de datos simulados (RUT, teléfono, email, clientes, contratos) salen de generadores
  propios por entidad (`_rng`), para no alterar la secuencia de los datos que ya existían.
- Datos simulados con semilla fija (`random.seed(42)`, `Faker.seed(42)`) pero con fechas relativas
  a hoy. Los IDs usan `uuid5` determinístico; nunca `uuid4`, porque cambiaría los `id_tecnico` en
  cada reinicio y dejaría huérfanos los registros de `kpi_valor`.
- Hoy hay 13 técnicos, 8 clientes internos (sin endpoint, solo alimentan `clienteId` y los datos de
  contacto de cada OT), 90 OTs de hoy en adelante (18 por día durante 5 días) y 252 de historial
  (OT-0091 en adelante).

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

## Historial (HU-26)

Las OTs de los 14 días anteriores a hoy (18 por día) están ya ejecutadas, con 75% `finalizada`,
10% `parcial`, 5% `enTerreno` (check-in sin cierre) y 10% `asignada` (el técnico nunca llegó). No
se genera `enviadaCobranza` porque los KPI solo cuentan `finalizada` como cierre.

- **TPS** = fecha del comprobante menos la del documento de inicio. **TTR** = check-in de una visita
  menos el cierre de la anterior del mismo técnico: cada técnico atiende sus OTs del día en orden y
  con al menos 20 minutos de traslado, sin superponerse. **OTP** se reconstruye con `createdAt` (siempre
  anterior a la hora programada) y la fecha del comprobante que cierra la OT.
- `finalizada` y `parcial` tienen 1 documento de inicio y 1 comprobante; `parcial` cubre solo parte de
  los dispositivos. Los dispositivos solo existen como patentes dentro del comprobante.
- Los datos son deterministas (`azar_de("historial", dias_atras)`), así que `reset` los reproduce, y
  no alteran las OTs de hoy en adelante ni su secuencia de azar.
- El frontend filtra `?estado=porAsignar`, así que no ve el historial. Una lista sin filtros devuelve
  las 342 OTs.
- Si el servidor se consulta entre las 21:00 y las 23:00 hora de Chile, algunos cierres de las últimas
  OTs de ayer (programadas a las 23:00 UTC) quedan con fecha unas horas en el futuro.
