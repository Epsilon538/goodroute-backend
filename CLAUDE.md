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

- `main.py`: crea la app y CORS; al arrancar lanza el precálculo de KPI en un hilo (si hay `DATABASE_URL`).
- `rutas.py`: endpoints y helpers de validación, en dos routers: `router_v1` (prefijo `/api/v1`,
  simula el backoffice) y `router` (endpoints propios de GoodRoute). No genera datos ni escribe SQL.
- `datos.py`: técnicos, clientes y OTs simulados en memoria. `FuenteDatos` (instancia `fuente`) expone
  `tecnicos`, `clientes`, `ordenes`, `documentos_inicio`, `comprobantes`, `buscar_tecnico`,
  `buscar_orden`, `documento_inicio_de`, `comprobantes_de`, `asignaciones` (historia de `asignacion`),
  `semilla` y `reiniciar(semilla=None)` (el arranque usa `SEMILLA_INICIAL`).
- `historial.py`: historial de 30 días (OTs ejecutadas, documentos de inicio, comprobantes y filas de `asignacion`).
- `generadores.py`: constantes y utilidades que comparten `datos.py` e `historial.py`.
- `db.py`: conexión y escrituras en Postgres. Único archivo que inserta en `asignacion`. La API nunca
  modifica el esquema (sin ALTER ni CREATE): los cambios de la tabla los ejecuta el usuario a mano en la BD.
- `precalculo.py`: guarda y lee los KPI en `kpi_valor` (ver sección KPIs).
- `kpis.py`: OTA, OTR, TAR y TPL (solo lectura sobre `asignacion`) y OTX, OTP, TPS y TTR (reciben `fuente`).
- `modelos.py`: constantes (estados, motivos, tipos) y modelos Pydantic de los request.

Los endpoints leen los datos simulados solo a través de `fuente`, nunca desde otra parte.
Si algún día hay un sistema externo real, se reemplaza esa clase.

## Endpoints

Simulan el backoffice (prefijo `/api/v1`, como en el modelo v3.0): GET `/api/v1/tecnicos`,
GET `/api/v1/ordenes?estado=&fecha=`, GET `/api/v1/ordenes/{orden_id}` (detalle con `documentoInicio`
y `comprobantes`), PATCH `/api/v1/ordenes/{orden_id}/asignar-tecnico`.

Propios de GoodRoute: POST `/api/asignar-tecnicos` (lote de una corrida del optimizador),
GET `/api/kpis?fecha=&tecnico_id=` (los 10 KPI), GET `/api/kpis/rango?fecha_desde=&fecha_hasta=&por_tecnico=&tecnico_id=` (rango, una sola llamada) y GET `/api/kpis/{sigla}?fecha=&tecnico_id=` (uno), GET
`/api/asignaciones?fecha=` (historia de la tabla `asignacion`), POST
`/api/asignaciones/cargar-historial` (la inserta en la BD, solo las filas que aún no están: se puede
llamar varias veces sin duplicar), GET `/api/reset?semilla=` y GET `/`.

**Detalle de la OT (HU-26).** `GET /api/v1/ordenes/{id}` agrega `documentoInicio` (null si el técnico no
marcó llegada) y `comprobantes`. Esos dos nombres están por confirmar con el equipo externo. La lista
`GET /api/v1/ordenes` no los incluye. No se simula `PATCH .../disponibilidad`.

## Reglas del negocio y convenciones

- **`asignacion` es un log de eventos:** cada PATCH y cada OT del POST hace un INSERT nuevo, nunca
  un UPDATE.
- **PATCH:** toda edición por este endpoint es manual, no una propuesta del optimizador: se guarda con
  `id_propuesta` null y `propuesta_modificada` false (así TAR la excluye), y `id_aceptada` = técnico
  nuevo. `tecnico_id: null` es "OT descartada" y devuelve la OT a `porAsignar`. El front tampoco
  debe mandar propuesta en estas ediciones.
- **POST:** `id_propuesta` sale de `ruta_propuesta` de cada ruta; `id_aceptada` es el técnico de la
  ruta. `propuesta_modificada` se decide por OT, no por ruta: es true solo si el técnico final difiere
  del propuesto, o si el optimizador no había propuesto esa OT (la dispatcher la agregó a mano, con
  `id_propuesta` null). Reordenar dentro de una ruta no cuenta. `aceptada_sin_modificacion` ya no se usa. `inicio_planificacion` (top-level) y `kilometraje_estimado` (por parada: km desde donde sale
  el técnico hasta esa OT, sin vuelta a la base) se guardan en la fila de cada OT. Las rutas son
  abiertas: `hora_retorno_base` y `distancia_total_km` son opcionales. El PATCH acepta los mismos dos
  campos. Requiere que la tabla `asignacion` tenga las columnas `inicio_planificacion timestamp` y
  `kilometraje_estimado numeric(8,2)`. Estrategia parcial: lo válido se aplica y los errores se reportan sin detener el resto.
  `tipo_modificacion` guarda por OT el tipo de las modificaciones que la afectan (ej. `ot_agregada`), no el JSON de la ruta.
- Asignar un técnico mueve la OT de `porRevisar`, `porAsignar` o `enviadaPlanificacion` a
  `asignada` (`modelos.ESTADOS_ASIGNABLES`) y actualiza `updatedAt`. `asignacionPorConfirmar` ya no se usa
  (decisión del usuario): se sacó de `ESTADOS_OT`.
- **`fechaProgramada` es texto libre en el sistema externo.** Al registrar una asignación se exige
  `YYYY-MM-DD` y `horaProgramada` `HH:MM`; si no, la OT se rechaza (422 en el PATCH, entrada en
  `errores` en el POST) y no se aplica ni se guarda nada.
- Un fallo de BD no tumba el request: el cambio en memoria ya se aplicó y la respuesta informa el error.
- `MOTIVOS_REPROGRAMACION` debe coincidir con el CHECK de la columna `motivo_reprogramacion`.
- **Las horas son UTC.** Las OTs se programan entre 8:00 y 19:00 hora de Chile y se convierten con la
  zona `America/Santiago` (`generadores.programada_utc`), no con un desfase fijo: UTC-3 en verano y
  UTC-4 en invierno (`tzdata` está en `requirements.txt` por Windows).
- **Modelo de datos del backoffice v3.0.** Técnicos y OTs usan sus nombres y valores en camelCase
  (`tecnicoId`, `fechaProgramada`, `porAsignar`, `instalacionSimple`). Los 8 estados de OT están en
  `modelos.ESTADOS_OT`; la API solo genera `porAsignar` y mueve a `asignada`
  (`enviadaPlanificacion` y `parcial` existen en el enum pero nada los asigna todavía). Los request y
  response propios de GoodRoute (PATCH y POST de asignación, `guardado_en_bd`, `ot_id`) siguen en
  snake_case, igual que las columnas de `asignacion`. Fechas de OT y técnico en UTC con sufijo Z.
- **Disponibilidad:** ya no es una entidad por fecha ni hay endpoint. Es un arreglo `disponibilidad`
  de 7 booleanos (lunes a domingo) dentro de cada técnico. Los técnicos no tienen horario fijo, así que
  todos van con los 7 días en `true`.
- Los campos nuevos de datos simulados (RUT, teléfono, email, clientes, contratos) salen de generadores
  propios por entidad (`_rng`), para no alterar la secuencia de los datos que ya existían.
- **Semillas.** Técnicos y clientes usan siempre la semilla 42 (`SEMILLA`), con IDs `uuid5`
  determinísticos; nunca `uuid4`, porque cambiaría los `id_tecnico` en cada reinicio y dejaría
  huérfanos los registros de `kpi_valor`. Las OTs y el historial arrancan siempre con `SEMILLA_INICIAL`
  (2026), así que son los mismos en cada reinicio de la API y coinciden con lo guardado en `asignacion`.
  El id de una OT sale de su fecha (`generadores.numero_ot`), no del orden de generación, y cada fecha usa
  su propio azar: arrancar otro día no cambia las fechas en común. El día anómalo está fijo en el calendario
  (`historial._es_dia_anomalo`). `GET /api/reset` sin semilla sortea otra (`fuente.semilla`) y, con
  `?semilla=N`, la fija; `?semilla=2026` vuelve a los datos del arranque. Con otra semilla, una fila ya
  guardada en `asignacion` con un `OT-xxxx` apunta a otra OT. Mantener esta estabilidad al tocar los
  generadores: no usar `random` global ni numeración secuencial entre días.
- Hay 13 técnicos, 8 clientes internos (sin endpoint, solo alimentan `clienteId` y los datos de
  contacto de cada OT), 90 OTs de hoy en adelante (18 por día durante 5 días) y el historial de 30
  días (OT-0091 en adelante, con volumen distinto según el día de la semana).

## KPIs

Definiciones del catálogo (documento *Estado de KPIs* del PO, HU-24). Todos reciben la fecha
(`YYYY-MM-DD`). Se exponen por `GET /api/kpis` y `GET /api/kpis/{sigla}` (`kpis.calcular` normaliza el
resultado en `numerador`, `denominador` y `valor`; TAR, CVH y UJO salen en %, los promedios en minutos); Si la BD falla, esos endpoints
responden 503 (uno) o listan el KPI en `errores` y devuelven los demás (todos).

**Precálculo (`precalculo.py`).** Los endpoints leen de `kpi_valor`: una fila por día, KPI y técnico, con
`numerador`, `denominador` y `valor` (`id_tecnico` NULL es la fila global). Para varios días se suman las filas
y recién se divide. Se llena sin endpoint, con tres disparadores: (1) al arrancar, en un hilo, los últimos 30
días de los 10 KPI (`precalcular_al_arrancar`; un deploy recalcula con las definiciones nuevas); (2) tras
insertar en `asignacion` (PATCH, POST y cargar historial), OTA, OTR, TAR y TPL de las fechas afectadas
(`actualizar_asignacion`); (3) al consultar, lo que falte se calcula, se devuelve y se guarda (`obtener_rango`).
Los KPI de ejecución de hoy y de fechas futuras no se guardan porque pueden cambiar. Escribir es borrar e insertar
en una sola transacción (no necesita índice único) y repetirlo no duplica. Si `kpi_valor` falla, las lecturas
calculan al vuelo. Requisitos de BD (los pone el usuario, la API no hace DDL): `kpi.sigla` con las 10 siglas
(la FK de `kpi_valor.kpi_sigla`) y `valor` y `denominador` aceptando NULL (los conteos no tienen denominador y
`valor` es NULL sin datos). Si se edita `asignacion` a mano, reiniciar la API refresca `kpi_valor`.
`GET /api/kpis/rango` también devuelve `totales` (`kpis.totalizar`): el total del período por KPI y técnico, sumando numeradores y denominadores y recién ahí dividiendo, para que el front no calcule nada.
Con el parámetro `zona` (valor de `zona` de los técnicos, que es la comuna) devuelve la suma de los técnicos de esa zona (`kpis.agregar_zona`), sin TPL ni OTP.
Los de conteo devuelven un entero; TAR, TPL, TPS, TTR, CVH y UJO devuelven `numerador` y `denominador`, para
agregar entre fechas sumando las partes (en TPL, TPS y TTR el numerador son minutos). `tecnico_id` es
opcional en todos salvo TPL y OTP (CVH y UJO sí).

- **De asignación (SQL sobre `asignacion`, por `fecha_orden_de_trabajo::date`):**
  - `calcular_ota`: filas sin `motivo_reprogramacion` y con técnico aceptado (asignaciones iniciales).
  - `calcular_otr`: filas con `motivo_reprogramacion`. OTA y OTR son excluyentes por fila.
  - `calcular_tar`: propuestas del optimizador que la dispatcher no modificó (`id_propuesta` no nulo y
    `propuesta_modificada = false`) sobre las propuestas del optimizador (filas con `id_propuesta` o
    con `propuesta_modificada = true`). Las ediciones manuales (`id_propuesta` null y flag false) no cuentan.
  - `calcular_tpl`: minutos entre `inicio_planificacion` y `fecha_asignacion` (promedio), confirmado con el PO.
- **De ejecución (`calcular_x(fecha, fuente)`, por `fechaProgramada`):**
  - `calcular_otx`: OTs `finalizada` (`parcial` no cuenta).
  - `calcular_otp`: complemento de OTX: OTs del día que al terminar el día en hora de Chile no estaban
    `finalizada` (el cierre se reconstruye con la fecha del comprobante).
  - `calcular_tps`: documento de inicio a comprobante de cada OT que tiene ambos (también `parcial`).
  - `calcular_ttr`: comprobante de una OT a documento de inicio de la siguiente del mismo técnico en el
    día. Incluye la espera hasta la hora agendada (definición pendiente del PO).
  - `calcular_cvh`: OTs cuyo `documentoInicio.fecha` cae entre `hora programada - TOLERANCIA_ANTES_MIN (30)` y
    `hora programada + TOLERANCIA_DESPUES_MIN (15)` sobre las OTs con documento de inicio. Las OTs sin
    documento (el técnico no llegó) no entran. Definición del PO (ingeniero de optimización): solo la hora
    de inicio con ambas tolerancias y el documento de inicio.
  - `calcular_ujo`: minutos efectivos (documento de inicio a comprobante, como TPS) sobre `JORNADA_MIN` (8:00 a
    19:00 = 660 min, igual para todos) por cada técnico con al menos una OT ese día. Supuestos: técnicos sin
    OTs no entran al denominador; la jornada 8 a 19 es la validada por la dispatcher (el PO dudó entre 19 y 21).
    Con el volumen simulado (18 a 20 OTs por día) da cerca de 8 %.
- **Pendiente:** confirmar con el PO el denominador de CVH y UJO (ver arriba). OTA está por confirmar con la dispatcher. Las filas guardadas antes de este cambio de
  TAR (PATCH con `id_propuesta` del técnico anterior y reprogramaciones con `true`) se cuentan mal;
  hay que limpiarlas o ignorarlas.

## Tests

Todavía no hay. Los datos de prueba de los KPIs están pendientes: no generar tests ni datos de
prueba de KPIs sin que el usuario lo pida. Cuando existan, parchear `db.conectar` (no hay
`get_db_connection`) y usar un schema temporal para no tocar `public.asignacion`.

## Historial (HU-11, HU-26 y HU-28)

Las OTs de los 30 días anteriores a hoy están ya ejecutadas. Los parámetros (duraciones por tipo,
tolerancia de llegada, mezcla de estados, volumen por día de la semana, anomalía) son constantes en
mayúsculas al inicio de `historial.py`; cada uno se documenta con su origen (cliente o supuesto del
equipo) en el documento de parámetros.

- **Orden de generación (por día):** el optimizador propone un técnico, la dispatcher lo acepta o lo
  cambia (`propuesta_modificada`), a veces reprograma (segunda fila manual, con `motivo_reprogramacion`,
  `id_propuesta` null y `propuesta_modificada` false, que no cuenta para TAR) y recién entonces el
  técnico atiende. Cada OT tiene su fila de `asignacion` con `inicio_planificacion`,
  `fecha_generacion`, `fecha_asignacion` y `kilometraje_estimado`; el técnico de la OT es el
  `id_aceptada` de su última fila. Un día laboral (el más reciente con 8+ días de antigüedad) es
  anómalo: más modificaciones, reprogramaciones y revisión lenta.
- **Hora programada:** cada técnico encadena sus OTs del día; la hora de cada una sale del cierre de la
  anterior más el traslado, redondeada hacia arriba a 15 min, para que TTR mida traslados y no esperas.
- **Estados:** `enTerreno` y `asignada` solo pueden ser la última visita del día del técnico. No se
  genera `enviadaCobranza` porque los KPI solo cuentan `finalizada` como cierre.
- **TPS** = fecha del comprobante menos la del documento de inicio. **TTR** = check-in de una visita
  menos el cierre de la anterior del mismo técnico. **OTP** se reconstruye con `createdAt` (siempre
  anterior a la planificación) y la fecha del comprobante que cierra la OT.
- `finalizada` y `parcial` tienen 1 documento de inicio y 1 comprobante; `parcial` cubre solo parte de
  los dispositivos. Los dispositivos solo existen como patentes dentro del comprobante.
- `lat` y `lng` del check-in se sortean alrededor del centro de la región, no de la dirección de la OT.
- El historial se regenera en memoria al arrancar y en cada reset, y no toca la BD. La historia de
  `asignacion` se inserta solo con `POST /api/asignaciones/cargar-historial`, que omite las filas ya
  guardadas. Lo que se pierde al reiniciar son los cambios de estado de las OTs en memoria (PATCH y POST);
  las filas de `asignacion` quedan.
- El frontend filtra `?estado=porAsignar`, así que no ve el historial.
