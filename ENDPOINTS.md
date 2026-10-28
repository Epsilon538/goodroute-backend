# Endpoints de GoodRoute Backend

API en FastAPI del módulo asignador y analítico de GoodRoute. Swagger en `/docs`, ReDoc en `/redoc` y el esquema en `/openapi.json`.

Los endpoints que simulan el backoffice llevan el prefijo `/api/v1` y usan el modelo de datos v3.0: campos y valores en camelCase, fechas en UTC con sufijo `Z`. Los endpoints propios de GoodRoute no llevan prefijo. Los cuerpos de request y las respuestas de asignación, asignaciones y KPI van en snake_case, igual que las columnas de la tabla `asignacion`.

## Resumen

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/` | Estado del servicio y lista de endpoints |
| GET | `/api/reset` | Regenera técnicos, clientes, OTs e historial |
| GET | `/api/v1/tecnicos` | Lista los técnicos |
| GET | `/api/v1/ordenes` | Lista las OTs, con filtros opcionales |
| GET | `/api/v1/ordenes/{orden_id}` | Detalle de una OT con documento de inicio y comprobantes |
| PATCH | `/api/v1/ordenes/{orden_id}/asignar-tecnico` | Asigna, reprograma o descarta una OT (edición manual) |
| POST | `/api/asignar-tecnicos` | Confirma una corrida completa del optimizador |
| GET | `/api/asignaciones` | Historia simulada de la tabla `asignacion` |
| POST | `/api/asignaciones/cargar-historial` | Inserta esa historia en la BD |
| GET | `/api/kpis` | Los 8 KPI de una fecha |
| GET | `/api/kpis/rango` | Los KPI de un rango de fechas, con desglose opcional por técnico |
| GET | `/api/kpis/{sigla}` | Un KPI de una fecha |

## Raíz y administración

### `GET /`

Devuelve `mensaje`, `version`, `docs` y `endpoints_disponibles`.

### `GET /api/reset`

Regenera en memoria técnicos, clientes, OTs e historial. Los técnicos y clientes salen siempre iguales (semilla 42). Las OTs y el historial dependen de la semilla: la API arranca con la 2026, así que son las mismas en cada reinicio y coinciden con lo guardado en la BD. Un reset sin semilla genera otras.

| Parámetro | Tipo | Descripción |
| --- | --- | --- |
| `semilla` | entero, opcional | Semilla de las OTs y el historial. Sin ella se sortea una nueva. Con la misma semilla se repiten. `2026` vuelve a los datos del arranque |

Respuesta: `mensaje`, `semilla`, `tecnicos`, `ordenes`, `asignaciones_historial`.

Las filas que ya están en la BD no se tocan. Con una semilla distinta de 2026, un `id_orden_trabajo` guardado antes corresponde a otra OT.

## Backoffice simulado (`/api/v1`)

### `GET /api/v1/tecnicos`

Lista de técnicos. Cada uno trae `id`, `nombre`, `apellidos`, `rut`, `tipo` (`interno` o `externo`), `zona`, `ciudad`, `comuna`, `region`, `telefono`, `email`, `usuario`, `activo` y `disponibilidad`.

`disponibilidad` es un arreglo de 7 booleanos de lunes a domingo. Hoy todos los técnicos tienen los 7 días en `true`.

### `GET /api/v1/ordenes`

Lista de OTs.

| Parámetro | Tipo | Descripción |
| --- | --- | --- |
| `estado` | texto, opcional | Uno de los estados de abajo |
| `fecha` | `YYYY-MM-DD`, opcional | Fecha programada |

Los filtros se pueden combinar. Un valor inválido responde 422.

Cada OT trae `id`, `padreId`, `tipo`, `estado`, `clienteId`, `contratoId`, `tecnicoId`, `direccionFactura`, `direccionInstalacion`, `comuna`, `region`, `contactoNombre`, `contactoTelefono`, `contactoEmail`, `fechaProgramada`, `horaProgramada`, `problemaInicioReportado`, `motivoNoInicio`, `facturacion`, `ultimaSincronizacion`, `createdAt` y `updatedAt`.

`horaProgramada` va en UTC (formato `HH:MM`). Las OTs se programan entre 8:00 y 19:00 hora de Chile, convertidas con la zona `America/Santiago`.

Estados: `porRevisar`, `porAsignar`, `enviadaPlanificacion`, `asignacionPorConfirmar`, `asignada`, `enTerreno`, `parcial`, `finalizada`, `enviadaCobranza`.

Tipos: `instalacionSimple`, `instalacionConCorte`, `mantencion`, `retiro`.

Las OTs de hoy en adelante nacen `porAsignar`. Las de los 30 días anteriores son historial ya ejecutado. Para ver solo las pendientes de asignar, usar `?estado=porAsignar`.

### `GET /api/v1/ordenes/{orden_id}`

Detalle de una OT: todos los campos de la lista más:

- `documentoInicio`: `id`, `otId`, `tecnicoId`, `fecha`, `lat`, `lng`, `nombreRecibe`, `rutRecibe`, `firmaImagen`, `observaciones`, `autorizadoPorBO`. Es `null` si el técnico no marcó llegada.
- `comprobantes`: lista con `id`, `otId`, `tecnicoId`, `fecha`, `direccion`, `dispositivos`, `nombreFirmante`, `rutFirmante`, `firmaImagen`.

Los nombres `documentoInicio` y `comprobantes` están por confirmar con el sistema externo. Si la OT no existe responde 404.

### `PATCH /api/v1/ordenes/{orden_id}/asignar-tecnico`

Registra una edición manual de la asignación de una OT. Siempre inserta una fila nueva en `asignacion`.

Cuerpo:

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `tecnico_id` | texto o `null` | Técnico nuevo. `null` descarta la OT y la devuelve a `porAsignar` |
| `motivo_reprogramacion` | texto, opcional | Uno de los motivos de abajo |
| `revisado_por` | objeto, opcional | `usuario_id`, `nombre`, `rol` |
| `tipo_modificacion` | texto, opcional | `OT asignada a otro técnico`, `OT reordenada` u `OT descartada` |
| `inicio_planificacion` | fecha ISO, opcional | Cuándo empezó la planificación |
| `kilometraje_estimado` | número, opcional | Km desde donde sale el técnico hasta esa OT |

Motivos: `Cliente No Disponible`, `Falta de Coordinación`, `Técnico No Disponible`, `Falta de Tiempo`, `Problema de Ruta`, `Problema Técnico`, `Dirección Incorrecta`, `Cliente Solicitó Cambio`, `Otro`.

Toda edición por este endpoint es manual, no una propuesta del optimizador: la fila se guarda con `id_propuesta` en `NULL` y `propuesta_modificada` en `false`, por lo que no cuenta para TAR.

Asignar mueve la OT de `porRevisar`, `porAsignar` o `enviadaPlanificacion` a `asignacionPorConfirmar`.

Respuesta: la OT actualizada más `guardado_en_bd` (y `db_error` si falló). Un fallo de BD no tumba el request: el cambio en memoria ya se aplicó.

Errores: 404 si la OT o el técnico no existen, 422 si el motivo o el tipo de modificación no son válidos, o si `fechaProgramada` u `horaProgramada` de la OT no tienen el formato esperado.

## Asignación de una corrida

### `POST /api/asignar-tecnicos`

Confirma una corrida del optimizador ya revisada por la dispatcher. Aplica la `ruta_final` de cada técnico y guarda una fila por OT.

Cuerpo:

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `fecha_planificacion` | texto | Fecha que se planificó |
| `inicio_planificacion` | fecha ISO, opcional | Cuándo empezó la planificación |
| `generado_en` | fecha ISO | Cuándo el optimizador generó la propuesta |
| `revisado_en` | fecha ISO | Cuándo la dispatcher confirmó |
| `revisado_por` | objeto | `usuario_id`, `nombre`, `rol` |
| `resumen_corrida` | objeto | `total_ots`, `ots_asignadas`, `ots_pendientes`, `total_tecnicos`, `tecnicos_utilizados`, `distancia_total_km` (opcional), `fuente_matriz` |
| `rutas` | lista | Una por técnico, ver abajo |
| `pendientes` | lista, opcional | OTs sin asignar |

Cada ruta: `tecnico_id`, `tecnico_nombre`, `zona_base`, `distancia_total_km` (opcional), `capacidad_uso`, `hora_salida_base`, `hora_retorno_base` (opcional), `ruta_propuesta` (ids de OT), `ruta_final` (ids de OT), `aceptada_sin_modificacion`, `modificaciones` y `paradas`.

Cada parada: `ot_id`, `secuencia`, `tipo`, `direccion`, `hora_estimada_llegada`, `espera_min` y `kilometraje_estimado` (opcional: km desde donde sale el técnico, sin vuelta a la base, hasta esa OT).

Las rutas son abiertas: los técnicos no vuelven a la base, por eso `hora_retorno_base` y `distancia_total_km` son opcionales.

Cómo se guarda cada fila de `asignacion`:

- `id_propuesta` sale de `ruta_propuesta` de la ruta donde estaba la OT, o `NULL` si el optimizador no la había propuesto.
- `id_aceptada` es el técnico de la ruta final.
- `propuesta_modificada` se decide por OT: es `true` solo si el técnico final difiere del propuesto, o si el optimizador no había propuesto esa OT. Reordenar dentro de una ruta no cuenta. El campo `aceptada_sin_modificacion` ya no influye en el flag.
- `tipo_modificacion` guarda, por OT, el tipo de las modificaciones de `modificaciones` que la afectan (por ejemplo `ot_agregada`), sin repetir y separados por coma. Es `NULL` si ninguna la afecta. Las modificaciones que no nombran ninguna OT se aplican a todas las OTs de esa ruta.
- `inicio_planificacion` y `kilometraje_estimado` (el de la parada de esa OT) van en cada fila.

Estrategia parcial: lo válido se aplica y los errores se reportan sin detener el resto.

Respuesta: `mensaje`, `fecha_planificacion`, `revisado_por`, `total_recibidas`, `total_actualizadas`, `actualizadas` (lista de `ot_id`, `tecnico_id`, `estado`), `errores` (lista de `ot_id`, `error`) y `base_de_datos` (`insertados`, `error`).

## Historia de asignaciones

### `GET /api/asignaciones`

Historia simulada de la tabla `asignacion` (30 días), en el formato de las columnas de la tabla. Incluye la confirmación de cada OT y, cuando hubo, la reprogramación manual. No incluye lo que se registra en vivo con el PATCH o el POST.

| Parámetro | Tipo | Descripción |
| --- | --- | --- |
| `fecha` | `YYYY-MM-DD`, opcional | Fecha de la OT |

Cada fila trae `id_orden_trabajo`, `id_propuesta`, `id_aceptada`, `fecha_asignacion`, `fecha_orden_de_trabajo`, `motivo_reprogramacion`, `usuario_dispatcher`, `fecha_generacion`, `inicio_planificacion`, `propuesta_modificada`, `tipo_modificacion` y `kilometraje_estimado`.

### `POST /api/asignaciones/cargar-historial`

Inserta en la tabla `asignacion` las filas de `GET /api/asignaciones` que aún no estén. No tiene parámetros.

Se puede llamar las veces que haga falta: una fila que ya existe (misma OT, técnicos, motivo y fecha de asignación) se omite. Como la API arranca siempre con la misma semilla, tras un reinicio el historial es el mismo y una nueva carga no duplica nada. Tras un reset con otra semilla, el historial es distinto y sus filas se agregan aparte.

Respuesta: `insertados`, `ya_existian` y `error`.

## KPI

Los KPI del catálogo (HU-24). No escriben en `kpi_valor`.

| Sigla | Nombre | Fuente | Qué calcula |
| --- | --- | --- | --- |
| OTA | OT Asignadas | `asignacion` | Filas con técnico aceptado y sin motivo de reprogramación |
| OTR | OT Reprogramadas | `asignacion` | Filas con motivo de reprogramación |
| OTX | OT Realizadas con Éxito | OTs del sistema externo | OTs en estado `finalizada` (`parcial` no cuenta) |
| OTP | OT Pendientes | OTs del sistema externo | OTs del día que al terminarlo, en hora de Chile, no estaban `finalizada` |
| TTR | Tiempo de Traslado | OTs del sistema externo | Minutos entre el comprobante de una OT y el documento de inicio de la siguiente del mismo técnico |
| TPS | Tiempo Promedio de Servicio | OTs del sistema externo | Minutos entre el documento de inicio y el comprobante de cada OT |
| TAR | Tasa de Aceptación de Rutas | `asignacion` | Propuestas del optimizador sin modificar sobre propuestas del optimizador, en % |
| TPL | Tiempo de Planificación | `asignacion` | Minutos entre `inicio_planificacion` y `fecha_asignacion` |

Las ediciones manuales (`id_propuesta` en `NULL` y `propuesta_modificada` en `false`) no cuentan para TAR.

### `GET /api/kpis`

Los 8 KPI de una fecha.

| Parámetro | Tipo | Descripción |
| --- | --- | --- |
| `fecha` | `YYYY-MM-DD`, obligatorio | Fecha de las OTs |
| `tecnico_id` | texto, opcional | Calcula solo sobre ese técnico |

Respuesta: `fecha`, `tecnico_id`, `kpis` (lista), `errores` (lista de `sigla` y `error`) y `no_calculados_por_tecnico`. Si falla el cálculo de alguno (por ejemplo sin conexión a la BD), aparece en `errores` y los demás se devuelven igual. Con `tecnico_id`, TPL y OTP no se calculan y quedan en `no_calculados_por_tecnico`.

### `GET /api/kpis/rango`

Los KPI de todas las fechas de un rango en una sola llamada. Los de asignación (OTA, OTR, TAR, TPL) salen de una única consulta para todo el rango.

| Parámetro | Tipo | Descripción |
| --- | --- | --- |
| `fecha_desde` | `YYYY-MM-DD`, obligatorio | Primera fecha, inclusive |
| `fecha_hasta` | `YYYY-MM-DD`, obligatorio | Última fecha, inclusive |
| `por_tecnico` | booleano, por defecto `false` | Agrega las filas de cada técnico |
| `tecnico_id` | texto, opcional | Devuelve solo las filas de ese técnico |

Respuesta: `fecha_desde`, `fecha_hasta`, `por_tecnico`, `tecnico_id`, `kpis` y `errores`. `kpis` es una lista plana y cada elemento tiene la misma forma que en `GET /api/kpis` (`sigla`, `nombre`, `unidad`, `fecha`, `tecnico_id`, `numerador`, `denominador`, `valor`).

- Sin `por_tecnico` ni `tecnico_id`: una fila por fecha y KPI sobre todos los técnicos (`tecnico_id` es `null`).
- Con `por_tecnico=true`: esas filas globales y, además, una por fecha, técnico y KPI que se calcule por técnico (sin TPL ni OTP).
- Con `tecnico_id`: solo las filas de ese técnico.
- Si falla la BD, OTA, OTR, TAR y TPL quedan en `errores` y los demás KPI se devuelven igual.

Errores: 422 si una fecha es inválida, si `fecha_desde` es posterior a `fecha_hasta`, si el rango pasa de 93 días o si se pasan `por_tecnico` y `tecnico_id` juntos; 404 si el técnico no existe.

### `GET /api/kpis/{sigla}`

Un KPI de una fecha. `sigla` es una de OTA, OTR, OTX, OTP, TTR, TPS, TAR o TPL. Mismos parámetros que el anterior.

Errores: 422 si la sigla o la fecha son inválidas, o si se pide TPL u OTP por técnico; 404 si el técnico no existe; 503 si falla el cálculo (por ejemplo la BD).

### Forma de cada KPI

Cada KPI trae `sigla`, `nombre`, `unidad`, `fecha`, `tecnico_id`, `numerador`, `denominador` y `valor`.

- **Conteos (OTA, OTR, OTX, OTP):** `valor` es el conteo, `denominador` es `null`, unidad `OT`.
- **Promedios (TTR, TPS, TPL):** `valor` es numerador dividido por denominador, en minutos.
- **TAR:** `valor` es ese cociente en %.
- Si el denominador es 0, `valor` es `null`.

El numerador y el denominador permiten agregar varias fechas sumando las partes, en vez de promediar los `valor`.

## Notas de operación

- La BD es PostgreSQL (variable `DATABASE_URL`). Sin ella la API arranca, pero los endpoints que escriben o leen `asignacion` responden con error o con `guardado_en_bd: false`.
- La API nunca modifica el esquema de la BD. La tabla `asignacion` necesita las columnas `inicio_planificacion` (timestamp) y `kilometraje_estimado` (numeric(8,2)).
- Los datos de técnicos, clientes y OTs viven en memoria y se regeneran en cada arranque o reset. El arranque es siempre igual (semilla 2026). Los cambios de estado de las OTs hechos por PATCH o POST se pierden al reiniciar; las filas de `asignacion` quedan.
