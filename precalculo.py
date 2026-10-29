"""Precálculo de KPI en la tabla `kpi_valor`: una fila por día, KPI y técnico.

`id_tecnico` en NULL es la fila global (el total de todos los técnicos). Cada fila guarda `numerador` y
`denominador` además de `valor`, para poder sumar varios días y recién después dividir.

Se llena solo, con tres disparadores:
- al arrancar la API: los últimos DIAS_VENTANA días de los 10 KPI (`precalcular_al_arrancar`);
- al confirmar o editar una asignación y al cargar el historial: OTA, OTR, TAR y TPL de las fechas afectadas
  (`actualizar_asignacion`);
- al consultar: lo que falte se calcula, se devuelve y se guarda (`obtener_rango`).

Los KPI de ejecución (OTX, OTP, TTR, TPS, CVH, UJO) de hoy y de fechas futuras no se guardan, porque todavía
pueden cambiar: se calculan al vuelo. Si la BD falla, las lecturas calculan al vuelo y nada se rompe.
"""

import logging
from datetime import date, timedelta
from typing import Optional

import db
import kpis
from kpis import CATALOGO, SIGLAS_ASIGNACION

DIAS_VENTANA = 30
TAMANO_LOTE = 400
log = logging.getLogger("uvicorn.error")


# --- Escritura ---

def guardar(filas: list[dict]) -> None:
    """Reemplaza en `kpi_valor` las filas de esas fechas y KPI. Borra e inserta en una sola transacción.

    Cada fila trae `sigla`, `fecha` (YYYY-MM-DD), `tecnico_id`, `numerador`, `denominador` y `valor`, como las
    devuelve `kpis.calcular_rango`. Repetir la llamada no duplica nada.
    """
    if not filas:
        return
    fechas = sorted({f["fecha"] for f in filas})
    siglas = sorted({f["sigla"] for f in filas})
    with db.conexion() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM kpi_valor WHERE fecha::date IN ({}) AND kpi_sigla IN ({})".format(
                ", ".join(["%s::date"] * len(fechas)), ", ".join(["%s"] * len(siglas))
            ),
            (*fechas, *siglas),
        )
        for i in range(0, len(filas), TAMANO_LOTE):
            lote = filas[i:i + TAMANO_LOTE]
            cur.execute(
                "INSERT INTO kpi_valor (kpi_sigla, fecha, id_tecnico, numerador, denominador, valor, fecha_calculo) "
                "VALUES " + ", ".join(["(%s, %s::date, %s, %s, %s, %s, NOW())"] * len(lote)),
                [x for f in lote for x in (f["sigla"], f["fecha"], f["tecnico_id"], f["numerador"], f["denominador"], f["valor"])],
            )


def _persistir(filas: list[dict]) -> None:
    """Guarda lo que ya no va a cambiar: los KPI de asignación y los de ejecución de días pasados."""
    hoy = date.today().isoformat()
    try:
        guardar([f for f in filas if f["sigla"] in SIGLAS_ASIGNACION or f["fecha"] < hoy])
    except Exception as e:
        log.warning("No se pudo guardar el precálculo de KPI en kpi_valor: %s", e)


def precalcular_ventana(fuente, dias: int = DIAS_VENTANA) -> Optional[str]:
    """Calcula y guarda los 10 KPI (global y por técnico) de los `dias` días anteriores a hoy.

    Devuelve el mensaje de error, o None si salió bien.
    """
    hoy = date.today()
    fechas = [(hoy - timedelta(days=i)).isoformat() for i in range(dias, 0, -1)]
    try:
        filas, errores = kpis.calcular_rango(fechas[0], fechas[-1], fuente, por_tecnico=True, fechas=fechas)
        if errores:
            return errores[0]["error"]
        guardar(filas)
    except Exception as e:
        return str(e)
    return None


def precalcular_al_arrancar(fuente) -> None:
    """Se ejecuta en un hilo al arrancar la API. Registra el resultado en el log y nunca lanza."""
    error = precalcular_ventana(fuente)
    if error:
        log.warning("Precálculo de KPI al arrancar: no se pudo guardar (%s)", error)
    else:
        log.info("Precálculo de KPI al arrancar: últimos %s días guardados en kpi_valor.", DIAS_VENTANA)


def actualizar_asignacion(fechas: list[str], fuente) -> Optional[str]:
    """Recalcula OTA, OTR, TAR y TPL de esas fechas (global y por técnico) y los guarda.

    Se llama después de insertar en `asignacion`. Devuelve el mensaje de error, o None si salió bien.
    """
    fechas = sorted(set(fechas))
    if not fechas:
        return None
    try:
        filas, errores = kpis.calcular_rango(
            fechas[0], fechas[-1], fuente, por_tecnico=True, siglas=SIGLAS_ASIGNACION, fechas=fechas
        )
        if errores:
            return errores[0]["error"]
        guardar(filas)
    except Exception as e:
        return str(e)
    return None


# --- Lectura ---

def _leer(desde: str, hasta: str, tecnico_id: Optional[str], por_tecnico: bool) -> list[tuple]:
    base = "SELECT kpi_sigla, fecha, id_tecnico, numerador, denominador FROM kpi_valor WHERE fecha::date BETWEEN %s AND %s"
    if tecnico_id:
        sql, params = base + " AND id_tecnico = %s", (desde, hasta, tecnico_id)
    elif por_tecnico:
        sql, params = base, (desde, hasta)
    else:
        sql, params = base + " AND id_tecnico IS NULL", (desde, hasta)
    with db.conexion() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _numero(valor):
    """Entero si no tiene decimales, flotante si los tiene."""
    valor = float(valor)
    return int(valor) if valor.is_integer() else valor


def _kpi_guardado(sigla: str, fecha: str, tecnico_id: Optional[str], numerador, denominador) -> dict:
    if CATALOGO[sigla]["tipo"] == "conteo":
        crudo = int(numerador)
    else:
        crudo = {"numerador": _numero(numerador), "denominador": _numero(denominador or 0)}
    return kpis._normalizar(sigla, fecha, tecnico_id, crudo)


def obtener_rango(
    desde: str,
    hasta: str,
    fuente,
    tecnico_id: Optional[str] = None,
    por_tecnico: bool = False,
    siglas: Optional[list[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """Los KPI de cada fecha del rango, leídos de `kpi_valor`. Devuelve (kpis, errores).

    Mismas opciones y misma forma que `kpis.calcular_rango`. Lo que falte en `kpi_valor` se calcula, se
    devuelve y (si ya no puede cambiar) se guarda. Si la BD no responde, se calcula todo al vuelo.
    """
    siglas = [s for s in CATALOGO if siglas is None or s in siglas]
    fechas = kpis.fechas_entre(desde, hasta)
    if tecnico_id:
        tecnicos = [tecnico_id]
    elif por_tecnico:
        tecnicos = [None] + [t["id"] for t in fuente.tecnicos]
    else:
        tecnicos = [None]

    try:
        guardados = _leer(desde, hasta, tecnico_id, por_tecnico)
    except Exception:
        return kpis.calcular_rango(desde, hasta, fuente, tecnico_id, por_tecnico, siglas)

    por_clave = {}
    for sigla, fecha, tecnico, numerador, denominador in guardados:
        if sigla in CATALOGO and numerador is not None:
            dia = str(fecha)[:10]
            tecnico = str(tecnico) if tecnico is not None else None
            por_clave[(sigla, dia, tecnico)] = _kpi_guardado(sigla, dia, tecnico, numerador, denominador)

    clave = tecnico_id  # la fila que decide si una fecha ya está calculada: la global, o la del técnico pedido
    faltan = [
        (sigla, fecha) for fecha in fechas for sigla in siglas
        if not (tecnico_id and not CATALOGO[sigla]["por_tecnico"]) and (sigla, fecha, clave) not in por_clave
    ]
    errores: list[dict] = []
    if faltan:
        fechas_faltan = sorted({f for _, f in faltan})
        siglas_faltan = sorted({s for s, _ in faltan})
        # Se calcula con todos los técnicos para guardar las filas completas de esas fechas.
        calculados, errores = kpis.calcular_rango(
            fechas_faltan[0], fechas_faltan[-1], fuente,
            por_tecnico=True, siglas=siglas_faltan, fechas=fechas_faltan,
        )
        for fila in calculados:
            por_clave[(fila["sigla"], fila["fecha"], fila["tecnico_id"])] = fila
        _persistir(calculados)

    resultados = [
        por_clave[(sigla, fecha, tecnico)]
        for fecha in fechas for tecnico in tecnicos for sigla in siglas
        if (sigla, fecha, tecnico) in por_clave
    ]
    return resultados, errores
