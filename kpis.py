"""KPIs operacionales del catálogo (HU-24).

De asignación (OTA, OTR, TAR, TPL): se calculan con SQL sobre `asignacion`. Una fila es un evento de
asignación o de reprogramación. Se filtra por el día de la OT (`fecha_orden_de_trabajo`) y no por
cuándo se registró el evento. OTA, TAR y TPL cuentan cada OT una vez, con su última confirmación (HU-37);
OTR cuenta cada reprogramación.

De ejecución (OTX, OTP, TPS, TTR, CVH, UJO): se calculan sobre las OTs, documentos de inicio y comprobantes
del sistema externo, que se reciben como `fuente` (ver `datos.FuenteDatos`). Se filtra por la
fecha programada de la OT.

Todos reciben la fecha (`YYYY-MM-DD`). Los de conteo devuelven un entero; los de promedio o razón
(TAR, TPL, TPS, TTR) devuelven numerador y denominador por separado, para agregar entre fechas
sumando las partes y no promediando. En TPL, TPS y TTR el numerador son minutos.
`tecnico_id` es opcional: sin él se calcula sobre todos los técnicos.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

import db
from generadores import JORNADA_MIN, TOLERANCIA_ANTES_MIN, TOLERANCIA_DESPUES_MIN, ZONA_CHILE

ESTADO_CIERRE = "finalizada"  # `parcial` no cuenta como cierre (clarificado por el cliente)


# --- KPIs de asignación (SQL sobre `asignacion`) ---

def _de_asignacion(fecha: str, tecnico_id: Optional[str]) -> dict:
    """Grupo de los KPI de asignación de un día, para todos los técnicos o para uno."""
    grupos = [g for (dia, tecnico), g in _asignacion_por_dia_y_tecnico(fecha, fecha).items()
              if tecnico_id is None or tecnico == tecnico_id]
    return {clave: sum(g[clave] for g in grupos) for clave in ("ota", "otr", "tar_num", "tar_den", "tpl_min", "tpl_n")}


def calcular_ota(fecha: str, tecnico_id: Optional[str] = None) -> int:
    """OTA: OTs distintas con un técnico asignado según su última confirmación (HU-37).

    Una confirmación es una fila sin motivo de reprogramación (corrida confirmada o reasignación manual).
    Si una OT se confirma varias veces cuenta una sola vez, para el técnico de la última. Si la última la
    descarta (sin técnico), no cuenta.
    """
    return _de_asignacion(fecha, tecnico_id)["ota"]


def calcular_otr(fecha: str, tecnico_id: Optional[str] = None) -> int:
    """OTR: reprogramaciones, es decir filas con motivo de reprogramación. Cuenta cada una, aunque una misma
    OT se reprograme varias veces (HU-37, respuesta del PO)."""
    return _de_asignacion(fecha, tecnico_id)["otr"]


def calcular_tar(fecha: str, tecnico_id: Optional[str] = None) -> dict:
    """TAR: OTs propuestas por el optimizador cuyo técnico final es el propuesto, sobre las OTs propuestas (razón).

    Se mira cada OT una vez (HU-37): el técnico final es el de su última confirmación y la propuesta es la última
    del optimizador. Entran al denominador las OTs con propuesta y las que la dispatcher agregó a mano a una
    corrida (`propuesta_modificada` true). Una reasignación manual que cambia al técnico propuesto la deja como
    modificada. Las OTs sin propuesta del optimizador ni corrida (solo ediciones manuales) no cuentan.
    """
    grupo = _de_asignacion(fecha, tecnico_id)
    return {"numerador": grupo["tar_num"], "denominador": grupo["tar_den"]}


def calcular_tpl(fecha: str) -> dict:
    """TPL: minutos entre el inicio de la planificación y la asignación (promedio por OT).

    Cada OT cuenta una vez, con su última confirmación que tenga ambas fechas (HU-37).
    """
    grupo = _de_asignacion(fecha, None)
    return {"numerador": round(grupo["tpl_min"], 2), "denominador": grupo["tpl_n"]}


# --- KPIs de ejecución (sobre los datos del sistema externo) ---

def _momento(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def _minutos(desde: datetime, hasta: datetime) -> float:
    return (hasta - desde).total_seconds() / 60


def _fecha_comprobante(fuente, orden_id: str) -> Optional[datetime]:
    """Fecha del último comprobante de la OT, o None si no tiene."""
    fechas = [_momento(c["fecha"]) for c in fuente.comprobantes_de(orden_id)]
    return max(fechas) if fechas else None


def _del_dia(fuente, fecha: str, tecnico_id: Optional[str]) -> list[dict]:
    return [
        o for o in fuente.ordenes
        if o["fechaProgramada"] == fecha and (tecnico_id is None or o["tecnicoId"] == tecnico_id)
    ]


def _promedio(minutos: list[float]) -> dict:
    return {"numerador": round(sum(minutos), 2), "denominador": len(minutos)}


def calcular_otx(fecha: str, fuente, tecnico_id: Optional[str] = None) -> int:
    """OTX: OTs en estado `finalizada` con fecha programada ese día."""
    return sum(1 for o in _del_dia(fuente, fecha, tecnico_id) if o["estado"] == ESTADO_CIERRE)


def calcular_otp(fecha: str, fuente) -> int:
    """OTP: OTs con fecha programada ese día que, al terminar el día (hora de Chile), no estaban `finalizada`.

    Es el complemento de OTX. El cierre se reconstruye con la fecha del comprobante, así que una OT
    finalizada después del corte cuenta como pendiente en ese día.
    """
    anio, mes, dia = (int(x) for x in fecha.split("-"))
    corte = datetime(anio, mes, dia, 23, 59, 59, tzinfo=ZONA_CHILE).astimezone(timezone.utc)
    pendientes = 0
    for orden in _del_dia(fuente, fecha, None):
        if _momento(orden["createdAt"]) > corte:
            continue
        cierre = _fecha_comprobante(fuente, orden["id"]) if orden["estado"] == ESTADO_CIERRE else None
        if cierre is None or cierre > corte:
            pendientes += 1
    return pendientes


def calcular_tps(fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """TPS: minutos entre el documento de inicio y el comprobante de cada OT (promedio).

    Cuentan las OTs que tienen ambos, también las `parcial`.
    """
    duraciones = []
    for orden in _del_dia(fuente, fecha, tecnico_id):
        documento = fuente.documento_inicio_de(orden["id"])
        cierre = _fecha_comprobante(fuente, orden["id"])
        if documento and cierre:
            duraciones.append(_minutos(_momento(documento["fecha"]), cierre))
    return _promedio(duraciones)


def calcular_ttr(fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """TTR: minutos entre el comprobante de una OT y el documento de inicio de la siguiente del mismo técnico.

    Las visitas del técnico en el día se ordenan por su check-in; solo se mide el tramo que parte
    de una OT con comprobante. Incluye cualquier espera hasta la hora agendada.
    """
    por_tecnico: dict[str, list[tuple[datetime, dict]]] = {}
    for orden in _del_dia(fuente, fecha, tecnico_id):
        documento = fuente.documento_inicio_de(orden["id"])
        if orden["tecnicoId"] and documento:
            por_tecnico.setdefault(orden["tecnicoId"], []).append((_momento(documento["fecha"]), orden))

    traslados = []
    for visitas in por_tecnico.values():
        visitas.sort(key=lambda v: v[0])
        for (_, anterior), (llegada, _) in zip(visitas, visitas[1:]):
            cierre = _fecha_comprobante(fuente, anterior["id"])
            if cierre:
                traslados.append(_minutos(cierre, llegada))
    return _promedio(traslados)


def calcular_cvh(fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """CVH: OTs que se iniciaron dentro de su ventana horaria sobre las OTs que se iniciaron (razón).

    La ventana es la hora programada con ambas tolerancias (hasta TOLERANCIA_ANTES_MIN antes y
    TOLERANCIA_DESPUES_MIN después) y lo que se compara es la fecha del documento de inicio. Las OTs sin
    documento de inicio (el técnico nunca llegó) no entran ni en el numerador ni en el denominador.
    """
    cumplen = total = 0
    for orden in _del_dia(fuente, fecha, tecnico_id):
        documento = fuente.documento_inicio_de(orden["id"])
        if not documento:
            continue
        programada = _momento(f"{orden['fechaProgramada']}T{orden['horaProgramada']}:00Z")
        llegada = _momento(documento["fecha"])
        total += 1
        if programada - timedelta(minutes=TOLERANCIA_ANTES_MIN) <= llegada <= programada + timedelta(minutes=TOLERANCIA_DESPUES_MIN):
            cumplen += 1
    return {"numerador": cumplen, "denominador": total}


def calcular_ujo(fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """UJO: tiempo efectivo de trabajo sobre la jornada configurada (razón).

    El numerador son los minutos entre el documento de inicio y el comprobante de cada OT (como TPS).
    El denominador es la jornada (`JORNADA_MIN`, igual para todos) por cada técnico que tuvo al menos una
    OT ese día: un técnico sin OTs no entra. Para agregar varios días se suman numeradores y denominadores.
    """
    minutos_por_tecnico: dict[str, float] = {}
    for orden in _del_dia(fuente, fecha, tecnico_id):
        if not orden["tecnicoId"]:
            continue
        acumulado = minutos_por_tecnico.setdefault(orden["tecnicoId"], 0.0)
        documento = fuente.documento_inicio_de(orden["id"])
        cierre = _fecha_comprobante(fuente, orden["id"])
        if documento and cierre:
            minutos_por_tecnico[orden["tecnicoId"]] = acumulado + _minutos(_momento(documento["fecha"]), cierre)
    return {
        "numerador": round(sum(minutos_por_tecnico.values()), 2),
        "denominador": len(minutos_por_tecnico) * JORNADA_MIN,
    }


# --- Catálogo: un punto de entrada para los endpoints ---

# tipo: "conteo" (un entero), "promedio" (minutos sobre cantidad) o "razon" (se informa en %).
CATALOGO = {
    "OTA": {"nombre": "OT Asignadas", "unidad": "OT", "tipo": "conteo", "por_tecnico": True},
    "OTR": {"nombre": "OT Reprogramadas", "unidad": "OT", "tipo": "conteo", "por_tecnico": True},
    "OTX": {"nombre": "OT Realizadas con Éxito", "unidad": "OT", "tipo": "conteo", "por_tecnico": True},
    "OTP": {"nombre": "OT Pendientes", "unidad": "OT", "tipo": "conteo", "por_tecnico": False},
    "TTR": {"nombre": "Tiempo de Traslado", "unidad": "min", "tipo": "promedio", "por_tecnico": True},
    "TPS": {"nombre": "Tiempo Promedio de Servicio", "unidad": "min", "tipo": "promedio", "por_tecnico": True},
    "TAR": {"nombre": "Tasa de Aceptación de Rutas", "unidad": "%", "tipo": "razon", "por_tecnico": True},
    "TPL": {"nombre": "Tiempo de Planificación", "unidad": "min", "tipo": "promedio", "por_tecnico": False},
    "CVH": {"nombre": "Cumplimiento de Ventana Horaria", "unidad": "%", "tipo": "razon", "por_tecnico": True},
    "UJO": {"nombre": "Utilización de Jornada", "unidad": "%", "tipo": "razon", "por_tecnico": True},
}


def _crudo(sigla: str, fecha: str, fuente, tecnico_id: Optional[str]):
    if sigla == "OTA":
        return calcular_ota(fecha, tecnico_id)
    if sigla == "OTR":
        return calcular_otr(fecha, tecnico_id)
    if sigla == "OTX":
        return calcular_otx(fecha, fuente, tecnico_id)
    if sigla == "OTP":
        return calcular_otp(fecha, fuente)
    if sigla == "TTR":
        return calcular_ttr(fecha, fuente, tecnico_id)
    if sigla == "TPS":
        return calcular_tps(fecha, fuente, tecnico_id)
    if sigla == "TAR":
        return calcular_tar(fecha, tecnico_id)
    if sigla == "CVH":
        return calcular_cvh(fecha, fuente, tecnico_id)
    if sigla == "UJO":
        return calcular_ujo(fecha, fuente, tecnico_id)
    return calcular_tpl(fecha)


def calcular(sigla: str, fecha: str, fuente, tecnico_id: Optional[str] = None) -> dict:
    """Calcula un KPI del catálogo y lo devuelve normalizado: numerador, denominador y valor.

    En los de conteo, `valor` es el conteo y no hay denominador. En los de promedio, `valor` es
    numerador / denominador. En TAR es ese cociente en %. `valor` es None si el denominador es 0.
    Lanza ValueError si la sigla no existe o si el KPI no se puede calcular por técnico.
    """
    if sigla not in CATALOGO:
        raise ValueError(f"KPI '{sigla}' no existe. Valores permitidos: {', '.join(CATALOGO)}")
    datos = CATALOGO[sigla]
    if tecnico_id and not datos["por_tecnico"]:
        raise ValueError(f"{sigla} no se calcula por técnico")

    return _normalizar(sigla, fecha, tecnico_id, _crudo(sigla, fecha, fuente, tecnico_id))


def _normalizar(sigla: str, fecha: str, tecnico_id: Optional[str], crudo) -> dict:
    """Arma la respuesta de un KPI a partir de su resultado crudo (entero, o numerador y denominador)."""
    datos = CATALOGO[sigla]
    if datos["tipo"] == "conteo":
        numerador, denominador, valor = crudo, None, crudo
    else:
        numerador, denominador = crudo["numerador"], crudo["denominador"]
        factor = 100 if datos["tipo"] == "razon" else 1
        valor = round(factor * numerador / denominador, 2) if denominador else None
    return {
        "sigla": sigla,
        "nombre": datos["nombre"],
        "unidad": datos["unidad"],
        "fecha": fecha,
        "tecnico_id": tecnico_id,
        "numerador": numerador,
        "denominador": denominador,
        "valor": valor,
    }


# --- Rango de fechas: varias fechas (y técnicos) en una sola pasada ---

SIGLAS_ASIGNACION = ["OTA", "OTR", "TAR", "TPL"]


# Una fila por OT y día (OTA, TAR, TPL) más una por reprogramación (OTR), agrupadas por técnico. La última
# confirmación de una OT es la de `fecha_asignacion` más reciente (y, a igual fecha, la de mayor id).
SQL_ASIGNACION_POR_DIA_Y_TECNICO = """
    WITH confirmaciones AS (
        SELECT
            fecha_orden_de_trabajo::date AS dia, id_orden_trabajo, id_aceptada, id_propuesta, propuesta_modificada,
            EXTRACT(EPOCH FROM fecha_asignacion - inicio_planificacion) / 60 AS minutos_plan,
            ROW_NUMBER() OVER (
                PARTITION BY fecha_orden_de_trabajo::date, id_orden_trabajo
                ORDER BY fecha_asignacion DESC NULLS LAST, id DESC
            ) AS orden
        FROM asignacion
        WHERE motivo_reprogramacion IS NULL
          AND fecha_orden_de_trabajo::date BETWEEN %(desde)s AND %(hasta)s
    ),
    por_ot AS (
        SELECT
            dia, id_orden_trabajo,
            (ARRAY_AGG(id_aceptada ORDER BY orden))[1] AS tecnico,
            (ARRAY_AGG(id_propuesta ORDER BY orden) FILTER (WHERE id_propuesta IS NOT NULL))[1] AS propuesta,
            BOOL_OR(id_propuesta IS NOT NULL OR propuesta_modificada) AS en_tar,
            (ARRAY_AGG(minutos_plan ORDER BY orden) FILTER (WHERE minutos_plan IS NOT NULL))[1] AS minutos_plan
        FROM confirmaciones
        GROUP BY dia, id_orden_trabajo
    ),
    eventos AS (
        SELECT
            dia, tecnico, 1 AS ota, 0 AS otr,
            CASE WHEN en_tar AND tecnico = propuesta THEN 1 ELSE 0 END AS tar_num,
            CASE WHEN en_tar THEN 1 ELSE 0 END AS tar_den,
            minutos_plan
        FROM por_ot
        WHERE tecnico IS NOT NULL
        UNION ALL
        SELECT fecha_orden_de_trabajo::date, id_aceptada, 0, 1, 0, 0, NULL
        FROM asignacion
        WHERE motivo_reprogramacion IS NOT NULL
          AND fecha_orden_de_trabajo::date BETWEEN %(desde)s AND %(hasta)s
    )
    SELECT dia, tecnico, SUM(ota), SUM(otr), SUM(tar_num), SUM(tar_den),
           COALESCE(SUM(minutos_plan), 0), COUNT(minutos_plan)
    FROM eventos
    GROUP BY dia, tecnico
"""


def _asignacion_por_dia_y_tecnico(desde: str, hasta: str) -> dict:
    """Agrega `asignacion` por día de la OT y técnico, con una sola consulta.

    Devuelve {(fecha, tecnico): {ota, otr, tar_num, tar_den, tpl_min, tpl_n}}. En OTA, TAR y TPL cada OT cuenta
    una vez, para el técnico de su última confirmación; en OTR cuenta cada reprogramación, para su técnico
    (que puede ser None). El global de un día es la suma de todos sus grupos.
    """
    with db.conexion() as conn, conn.cursor() as cur:
        cur.execute(SQL_ASIGNACION_POR_DIA_Y_TECNICO, {"desde": desde, "hasta": hasta})
        filas = cur.fetchall()
    return {
        (str(dia), tecnico): {
            "ota": int(ota), "otr": int(otr), "tar_num": int(tar_num), "tar_den": int(tar_den),
            "tpl_min": float(tpl_min), "tpl_n": int(tpl_n),
        }
        for dia, tecnico, ota, otr, tar_num, tar_den, tpl_min, tpl_n in filas
    }


def _crudo_asignacion(sigla: str, grupos: list[dict]):
    """Resultado crudo de un KPI de asignación sumando los grupos (técnicos) que corresponden."""
    suma = lambda clave: sum(g[clave] for g in grupos)
    if sigla == "OTA":
        return suma("ota")
    if sigla == "OTR":
        return suma("otr")
    if sigla == "TAR":
        return {"numerador": suma("tar_num"), "denominador": suma("tar_den")}
    return {"numerador": round(suma("tpl_min"), 2), "denominador": suma("tpl_n")}  # TPL


def fechas_entre(desde: str, hasta: str) -> list[str]:
    inicio, fin = date.fromisoformat(desde), date.fromisoformat(hasta)
    return [(inicio + timedelta(days=i)).isoformat() for i in range((fin - inicio).days + 1)]


def calcular_rango(
    desde: str,
    hasta: str,
    fuente,
    tecnico_id: Optional[str] = None,
    por_tecnico: bool = False,
    siglas: Optional[list[str]] = None,
    fechas: Optional[list[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """Calcula los KPI de cada fecha entre `desde` y `hasta` (inclusive), o solo de las `fechas` indicadas.

    Sin `tecnico_id` ni `por_tecnico`: una fila por fecha y KPI, sobre todos los técnicos.
    Con `por_tecnico`: además, una fila por fecha, técnico y KPI que se calcule por técnico.
    Con `tecnico_id`: solo las filas de ese técnico.
    `siglas` limita los KPI (por defecto, todos).

    Los de asignación salen de una sola consulta SQL para todo el rango. Si esa consulta falla, sus
    KPI se reportan una vez en `errores` y los demás se devuelven igual.
    Devuelve (kpis, errores); cada KPI tiene la misma forma que `calcular`.
    """
    fechas = fechas if fechas is not None else fechas_entre(desde, hasta)
    siglas = [sigla for sigla in CATALOGO if siglas is None or sigla in siglas]
    if tecnico_id:
        tecnicos = [tecnico_id]
    elif por_tecnico:
        tecnicos = [None] + [t["id"] for t in fuente.tecnicos]
    else:
        tecnicos = [None]

    errores = []
    por_dia: Optional[dict[str, dict]] = None  # {fecha: {id_aceptada: grupo}}
    if fechas and any(sigla in SIGLAS_ASIGNACION for sigla in siglas):
        try:
            por_dia = {}
            for (dia, tecnico), grupo in _asignacion_por_dia_y_tecnico(min(fechas), max(fechas)).items():
                por_dia.setdefault(dia, {})[tecnico] = grupo
        except Exception as e:
            por_dia = None
            errores = [{"sigla": sigla, "error": str(e)} for sigla in siglas
                       if sigla in SIGLAS_ASIGNACION and not (tecnico_id and not CATALOGO[sigla]["por_tecnico"])]

    resultados = []
    for fecha in fechas:
        grupos_del_dia = (por_dia or {}).get(fecha, {})
        for tecnico in tecnicos:
            for sigla in siglas:
                if tecnico and not CATALOGO[sigla]["por_tecnico"]:
                    continue
                if sigla in SIGLAS_ASIGNACION:
                    if por_dia is None:
                        continue
                    grupos = list(grupos_del_dia.values()) if tecnico is None else [grupos_del_dia[tecnico]] if tecnico in grupos_del_dia else []
                    crudo = _crudo_asignacion(sigla, grupos)
                else:
                    crudo = _crudo(sigla, fecha, fuente, tecnico)
                resultados.append(_normalizar(sigla, fecha, tecnico, crudo))
    return resultados, errores


def totalizar(filas: list[dict]) -> list[dict]:
    """Totales del período: un resultado por KPI (y por técnico) a partir de las filas diarias.

    Suma numeradores y denominadores de todas las fechas y recién entonces calcula `valor`, igual que
    `_normalizar` (nunca promedia los `valor` diarios). Cada total lleva `sigla`, `nombre`, `unidad`,
    `tecnico_id`, `dias` (fechas sumadas), `numerador`, `denominador` y `valor`. El global (`tecnico_id`
    null) va primero y luego cada técnico, con los KPI en el orden del catálogo.
    """
    orden_sigla = {sigla: i for i, sigla in enumerate(CATALOGO)}
    orden_tecnico: dict[Optional[str], int] = {None: -1}
    sumas: dict[tuple, dict] = {}
    for fila in filas:
        tecnico = fila["tecnico_id"]
        orden_tecnico.setdefault(tecnico, len(orden_tecnico))
        suma = sumas.setdefault((tecnico, fila["sigla"]), {"numerador": 0, "denominador": 0, "dias": 0})
        suma["numerador"] += fila["numerador"]
        suma["denominador"] += fila["denominador"] or 0
        suma["dias"] += 1

    totales = []
    for (tecnico, sigla), suma in sorted(sumas.items(), key=lambda x: (orden_tecnico[x[0][0]], orden_sigla[x[0][1]])):
        if CATALOGO[sigla]["tipo"] == "conteo":
            crudo = int(suma["numerador"])
        else:
            crudo = {"numerador": round(suma["numerador"], 2), "denominador": round(suma["denominador"], 2)}
        total = _normalizar(sigla, "", tecnico, crudo)
        del total["fecha"]
        total["dias"] = suma["dias"]
        totales.append(total)
    return totales


def agregar_zona(filas: list[dict], tecnico_ids: list[str], con_tecnicos: bool = False) -> list[dict]:
    """KPI de una zona a partir de las filas por técnico: suma numeradores y denominadores de sus técnicos.

    `filas` son las filas diarias con el desglose por técnico (`calcular_rango` o `obtener_rango` con
    `por_tecnico=True`). Devuelve, por fecha, una fila por KPI con `tecnico_id` null (el total de la zona,
    solo los KPI que se calculan por técnico: sin TPL ni OTP) y, si `con_tecnicos`, después las filas de
    cada técnico de la zona.
    """
    ids = set(tecnico_ids)
    por_fecha: dict[str, list[dict]] = {}
    for fila in filas:
        if fila["tecnico_id"] in ids:
            por_fecha.setdefault(fila["fecha"], []).append(fila)

    resultados = []
    for fecha, del_dia in por_fecha.items():
        sumas: dict[str, list] = {}
        for fila in del_dia:
            suma = sumas.setdefault(fila["sigla"], [0, 0])
            suma[0] += fila["numerador"]
            suma[1] += fila["denominador"] or 0
        for sigla in CATALOGO:
            if sigla not in sumas:
                continue
            numerador, denominador = sumas[sigla]
            if CATALOGO[sigla]["tipo"] == "conteo":
                crudo = int(numerador)
            else:
                crudo = {"numerador": round(numerador, 2), "denominador": round(denominador, 2)}
            resultados.append(_normalizar(sigla, fecha, None, crudo))
        if con_tecnicos:
            resultados.extend(del_dia)
    return resultados
