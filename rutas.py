import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

import db
import kpis
from datos import fuente
from generadores import ahora_utc
from modelos import (
    ESTADO_ASIGNACION_POR_CONFIRMAR,
    ESTADO_POR_ASIGNAR,
    ESTADOS_ASIGNABLES,
    ESTADOS_OT,
    MOTIVOS_REPROGRAMACION,
    TIPOS_MODIFICACION_PATCH,
    AsignarTecnicoRequest,
    AsignarTecnicosRequest,
)

router = APIRouter()  # endpoints propios de GoodRoute
router_v1 = APIRouter(prefix="/api/v1")  # simula el backoffice (modelo de datos v3.0)

FORMATO_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
FORMATO_HORA = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
LARGO_MAX_TIPO_MODIFICACION = 100  # columna varchar(100)


# --- Helpers ---

def _validar_valor(etiqueta: str, valor: str, permitidos: list[str]) -> None:
    if valor not in permitidos:
        raise HTTPException(
            status_code=422,
            detail=f"El {etiqueta} '{valor}' no es valido. Valores permitidos: {', '.join(permitidos)}",
        )


def _validar_fecha(fecha: str) -> None:
    try:
        date.fromisoformat(fecha)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"El parametro 'fecha' tiene un formato invalido: '{fecha}'. "
                "Use el formato YYYY-MM-DD (ej: 2026-10-05)."
            ),
        )


def _validar_tecnico(tecnico_id: Optional[str]) -> None:
    if tecnico_id is not None and fuente.buscar_tecnico(tecnico_id) is None:
        raise HTTPException(404, f"Tecnico con id '{tecnico_id}' no encontrado.")


def _fecha_hora_ot(orden: dict) -> str:
    """Timestamp de la OT para la BD. Lanza ValueError si la fecha u hora no son válidas.

    En el sistema externo `fechaProgramada` es texto libre, así que no se asume el formato.
    """
    fecha, hora = orden.get("fechaProgramada"), orden.get("horaProgramada")
    valida = (
        isinstance(fecha, str) and FORMATO_FECHA.fullmatch(fecha)
        and isinstance(hora, str) and FORMATO_HORA.fullmatch(hora)
    )
    if valida:
        try:
            date.fromisoformat(fecha)
        except ValueError:
            valida = False
    if not valida:
        raise ValueError(
            f"La OT '{orden['id']}' tiene fechaProgramada '{fecha}' y horaProgramada '{hora}': "
            "se esperaba YYYY-MM-DD y HH:MM."
        )
    return f"{fecha}T{hora}:00"


def _asignar_en_memoria(orden: dict, tecnico_id: Optional[str]) -> None:
    orden["tecnicoId"] = tecnico_id
    if tecnico_id is None:
        orden["estado"] = ESTADO_POR_ASIGNAR
    elif orden["estado"] in ESTADOS_ASIGNABLES:
        orden["estado"] = ESTADO_ASIGNACION_POR_CONFIRMAR
    orden["updatedAt"] = ahora_utc()


def _tipo_modificacion_de_ot(modificaciones: list[dict], ot_id: str) -> Optional[str]:
    """Resume, como texto corto, las modificaciones de la dispatcher que afectan a una OT.

    El optimizador manda las modificaciones por ruta (cada una dice a qué OT se refiere). A cada OT
    le corresponden las suyas y las que no nombran ninguna OT (cambios de la ruta completa).
    Devuelve los tipos sin repetir, separados por coma (p. ej. "ot_agregada"), o None si no hay.
    """
    tipos: list[str] = []
    for mod in modificaciones:
        if not isinstance(mod, dict):
            continue
        ots_mencionadas = {
            v for v in mod.values() if isinstance(v, str) and v.startswith("OT-")
        } | {x for v in mod.values() if isinstance(v, list) for x in v if isinstance(x, str)}
        if ots_mencionadas and ot_id not in ots_mencionadas:
            continue
        tipo = mod.get("tipo")
        if isinstance(tipo, str) and tipo not in tipos:
            tipos.append(tipo)
    if not tipos:
        return None
    return ", ".join(tipos)[:LARGO_MAX_TIPO_MODIFICACION]


def _guardar(registros: list[dict]) -> Optional[str]:
    """Inserta en BD. Devuelve el mensaje de error, o None si salió bien.

    Un fallo de BD no tumba el request: el cambio en memoria ya se aplicó.
    """
    try:
        db.insertar_asignaciones(registros)
    except Exception as e:
        return str(e)
    return None


# --- Raíz y admin ---

@router.get("/", tags=["Root"])
def root():
    return {
        "mensaje": "GoodRoute Backend",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints_disponibles": [
            "GET  /api/v1/tecnicos",
            "GET  /api/v1/ordenes?estado=&fecha=",
            "GET  /api/v1/ordenes/{id}",
            "PATCH /api/v1/ordenes/{id}/asignar-tecnico",
            "POST /api/asignar-tecnicos",
            "GET  /api/kpis?fecha=&tecnico_id=",
            "GET  /api/kpis/{sigla}?fecha=&tecnico_id=",
            "GET  /api/asignaciones?fecha=",
            "POST /api/asignaciones/cargar-historial",
            "GET  /api/reset?semilla=",
        ],
    }


@router.get("/api/reset", tags=["Admin"], summary="Reiniciar y regenerar todos los datos")
def reset(
    semilla: Optional[int] = Query(
        default=None,
        description="Semilla de las OTs y el historial. Sin ella se sortea una nueva; la respuesta la informa.",
    ),
):
    """Regenera en memoria técnicos, clientes, OTs e historial.

    Los técnicos y clientes salen siempre iguales; las OTs cambian en cada reset
    (con la misma `semilla` y el mismo día se repiten). Las filas ya guardadas en la BD
    no se tocan, y sus ids de OT pasan a corresponder a las OTs nuevas.
    """
    fuente.reiniciar(semilla)
    return {
        "mensaje": "Reset completado. Todos los datos han sido regenerados.",
        "semilla": fuente.semilla,
        "tecnicos": len(fuente.tecnicos),
        "ordenes": len(fuente.ordenes),
        "asignaciones_historial": len(fuente.asignaciones),
    }


# --- Lecturas ---

@router_v1.get("/tecnicos", tags=["Tecnicos"], summary="Obtener lista de tecnicos")
def get_tecnicos():
    return fuente.tecnicos


@router_v1.get("/ordenes", tags=["Ordenes de Trabajo"], summary="Obtener lista de ordenes de trabajo")
def get_ordenes(
    estado: Optional[str] = Query(
        default=None,
        description=f"Filtra por estado: {' | '.join(ESTADOS_OT)}",
        examples=["porAsignar"],
    ),
    fecha: Optional[str] = Query(
        default=None,
        description="Filtra por fecha programada (YYYY-MM-DD)",
        examples=["2026-10-05"],
    ),
):
    """Los filtros `estado` y `fecha` son opcionales y se pueden combinar."""
    ordenes = fuente.ordenes
    if estado is not None:
        _validar_valor("estado", estado, ESTADOS_OT)
        ordenes = [o for o in ordenes if o["estado"] == estado]
    if fecha is not None:
        _validar_fecha(fecha)
        ordenes = [o for o in ordenes if o["fechaProgramada"] == fecha]
    return ordenes


@router_v1.get("/ordenes/{orden_id}", tags=["Ordenes de Trabajo"], summary="Obtener el detalle de una orden de trabajo")
def get_orden(orden_id: str):
    """La OT con su `documentoInicio` (null si el técnico no marcó llegada) y sus `comprobantes`.

    Los nombres de esas dos propiedades están por confirmar con el equipo del sistema externo.
    El detalle omite dispositivos, costos e historial de eventos.
    """
    orden = fuente.buscar_orden(orden_id)
    if orden is None:
        raise HTTPException(404, f"Orden de trabajo con id '{orden_id}' no encontrada.")
    return {
        **orden,
        "documentoInicio": fuente.documento_inicio_de(orden_id),
        "comprobantes": fuente.comprobantes_de(orden_id),
    }


@router.get("/api/kpis", tags=["KPIs"], summary="Todos los KPI del catálogo para una fecha")
def get_kpis(
    fecha: str = Query(description="Fecha programada de las OTs (YYYY-MM-DD)", examples=["2026-10-20"]),
    tecnico_id: Optional[str] = Query(default=None, description="Calcula solo sobre este técnico"),
):
    """Devuelve OTA, OTR, OTX, OTP, TTR, TPS, TAR y TPL.

    Cada KPI trae `numerador`, `denominador` y `valor`. Si falla el cálculo de alguno (por ejemplo, sin
    conexión a la BD), aparece en `errores` y los demás se devuelven igual. Con `tecnico_id`, TPL y OTP
    no se calculan (no existen por técnico) y se listan en `no_calculados_por_tecnico`.
    """
    _validar_fecha(fecha)
    _validar_tecnico(tecnico_id)
    resultados, errores, omitidos = [], [], []
    for sigla, datos in kpis.CATALOGO.items():
        if tecnico_id and not datos["por_tecnico"]:
            omitidos.append(sigla)
            continue
        try:
            resultados.append(kpis.calcular(sigla, fecha, fuente, tecnico_id))
        except Exception as e:
            errores.append({"sigla": sigla, "error": str(e)})
    return {
        "fecha": fecha,
        "tecnico_id": tecnico_id,
        "kpis": resultados,
        "errores": errores,
        "no_calculados_por_tecnico": omitidos,
    }


@router.get("/api/kpis/{sigla}", tags=["KPIs"], summary="Un KPI del catálogo para una fecha")
def get_kpi(
    sigla: str,
    fecha: str = Query(description="Fecha programada de las OTs (YYYY-MM-DD)", examples=["2026-10-20"]),
    tecnico_id: Optional[str] = Query(default=None, description="Calcula solo sobre este técnico"),
):
    """`sigla` es una de OTA, OTR, OTX, OTP, TTR, TPS, TAR o TPL. Devuelve `numerador`, `denominador` y `valor`."""
    _validar_valor("KPI", sigla, list(kpis.CATALOGO))
    _validar_fecha(fecha)
    _validar_tecnico(tecnico_id)
    if tecnico_id and not kpis.CATALOGO[sigla]["por_tecnico"]:
        raise HTTPException(422, f"{sigla} no se calcula por técnico.")
    try:
        return kpis.calcular(sigla, fecha, fuente, tecnico_id)
    except Exception as e:
        raise HTTPException(503, f"No se pudo calcular {sigla}: {e}")


@router.get("/api/asignaciones", tags=["Asignaciones"], summary="Historia de la tabla de asignaciones (HU-11)")
def get_asignaciones(
    fecha: Optional[str] = Query(
        default=None,
        description="Filtra por fecha de la OT (YYYY-MM-DD)",
        examples=["2026-10-05"],
    ),
):
    """Filas del historial en el formato de la tabla `asignacion` (snake_case).

    Incluye la confirmación de cada OT y, cuando hubo, la reprogramación. No incluye lo que se
    registra en vivo con el PATCH o el POST de asignación: eso va directo a la BD.
    """
    filas = fuente.asignaciones
    if fecha is not None:
        _validar_fecha(fecha)
        filas = [f for f in filas if f["fecha_orden_de_trabajo"].startswith(fecha)]
    return filas


@router.post(
    "/api/asignaciones/cargar-historial",
    tags=["Asignaciones"],
    summary="Insertar el historial de asignaciones en la BD",
)
def cargar_historial_asignaciones(
    forzar: bool = Query(default=False, description="Insertar de nuevo aunque este historial ya se haya cargado"),
):
    """Inserta en `asignacion` las filas de `GET /api/asignaciones`.

    La tabla solo crece: cada carga agrega filas. Por eso, sin `forzar`, se rechaza una segunda
    carga del mismo historial (el reset genera uno nuevo).
    """
    if fuente.historial_cargado_en_bd and not forzar:
        raise HTTPException(409, "Este historial ya se cargó en la BD. Use forzar=true para insertarlo de nuevo.")
    error = _guardar(fuente.asignaciones)
    if error is None:
        fuente.historial_cargado_en_bd = True
    return {"insertados": 0 if error else len(fuente.asignaciones), "error": error}


# --- Asignación ---

@router_v1.patch(
    "/ordenes/{orden_id}/asignar-tecnico",
    tags=["Ordenes de Trabajo"],
    summary="Asignar o reprogramar tecnico de una orden de trabajo",
)
def asignar_tecnico(orden_id: str, body: AsignarTecnicoRequest):
    """Registra un evento de asignación para una OT (siempre inserta una fila nueva).

    - **Asignar**: `tecnico_id` sin `motivo_reprogramacion`.
    - **Reprogramar**: `tecnico_id` nuevo con `motivo_reprogramacion`.
    - **Descartar**: `tecnico_id` null; la OT vuelve a `porAsignar`.

    Toda edición por este endpoint es manual, no una propuesta del optimizador: la fila se guarda con
    `id_propuesta` null y `propuesta_modificada` false, y por eso no cuenta para TAR.
    """
    orden = fuente.buscar_orden(orden_id)
    if orden is None:
        raise HTTPException(404, f"Orden de trabajo con id '{orden_id}' no encontrada.")
    if body.tecnico_id is not None and fuente.buscar_tecnico(body.tecnico_id) is None:
        raise HTTPException(404, f"Tecnico con id '{body.tecnico_id}' no encontrado.")
    if body.motivo_reprogramacion is not None:
        _validar_valor("motivo", body.motivo_reprogramacion, MOTIVOS_REPROGRAMACION)
    if body.tipo_modificacion is not None:
        _validar_valor("tipo_modificacion", body.tipo_modificacion, TIPOS_MODIFICACION_PATCH)

    try:
        fecha_ot = _fecha_hora_ot(orden)
    except ValueError as e:
        raise HTTPException(422, str(e))

    _asignar_en_memoria(orden, body.tecnico_id)

    error = _guardar([{
        "id_orden_trabajo": orden_id,
        "id_propuesta": None,
        "id_aceptada": body.tecnico_id,
        "fecha_asignacion": None,
        "fecha_orden_de_trabajo": fecha_ot,
        "motivo_reprogramacion": body.motivo_reprogramacion,
        "usuario_dispatcher": body.revisado_por.nombre if body.revisado_por else None,
        "fecha_generacion": None,
        "propuesta_modificada": False,
        "tipo_modificacion": body.tipo_modificacion,
        "inicio_planificacion": body.inicio_planificacion,
        "kilometraje_estimado": body.kilometraje_estimado,
    }])

    respuesta = {**orden, "guardado_en_bd": error is None}
    if error:
        respuesta["db_error"] = error
    return respuesta


@router.post(
    "/api/asignar-tecnicos",
    tags=["Ordenes de Trabajo"],
    summary="Asignar tecnicos de forma masiva desde una corrida de optimizacion",
)
def asignar_tecnicos_masivo(body: AsignarTecnicosRequest):
    """Aplica la `ruta_final` de cada técnico y guarda un registro por OT.

    `propuesta_modificada` se decide por OT: es true si su técnico final difiere del que propuso el
    optimizador (o si el optimizador no la había propuesto). Cambiar el orden dentro de una ruta no
    cuenta. `tipo_modificacion` guarda, por OT, el tipo de las modificaciones que la afectan.

    Estrategia parcial: lo válido se aplica y los errores se reportan sin detener el resto.
    """
    propuesta_por_ot = {
        ot_id: ruta.tecnico_id for ruta in body.rutas for ot_id in ruta.ruta_propuesta
    }
    actualizadas, errores, registros = [], [], []

    for ruta in body.rutas:
        km_por_ot = {parada.ot_id: parada.kilometraje_estimado for parada in ruta.paradas}
        if fuente.buscar_tecnico(ruta.tecnico_id) is None:
            errores += [
                {"ot_id": ot_id, "error": f"Tecnico con id '{ruta.tecnico_id}' no encontrado en el sistema."}
                for ot_id in ruta.ruta_final
            ]
            continue

        for ot_id in ruta.ruta_final:
            orden = fuente.buscar_orden(ot_id)
            if orden is None:
                errores.append({"ot_id": ot_id, "error": f"OT '{ot_id}' no encontrada en el sistema."})
                continue

            try:
                fecha_ot = _fecha_hora_ot(orden)
            except ValueError as e:
                errores.append({"ot_id": ot_id, "error": str(e)})
                continue

            propuesto = propuesta_por_ot.get(ot_id)
            _asignar_en_memoria(orden, ruta.tecnico_id)
            actualizadas.append({"ot_id": ot_id, "tecnico_id": ruta.tecnico_id, "estado": orden["estado"]})
            registros.append({
                "id_orden_trabajo": ot_id,
                "id_propuesta": propuesto,
                "id_aceptada": ruta.tecnico_id,
                "fecha_asignacion": body.revisado_en,
                "fecha_orden_de_trabajo": fecha_ot,
                "motivo_reprogramacion": None,
                "usuario_dispatcher": body.revisado_por.nombre,
                "fecha_generacion": body.generado_en,
                "propuesta_modificada": propuesto != ruta.tecnico_id,
                "tipo_modificacion": _tipo_modificacion_de_ot(ruta.modificaciones, ot_id),
                "inicio_planificacion": body.inicio_planificacion,
                "kilometraje_estimado": km_por_ot.get(ot_id),
            })

    error_bd = _guardar(registros) if registros else None

    return {
        "mensaje": "Asignacion masiva completada.",
        "fecha_planificacion": body.fecha_planificacion,
        "revisado_por": body.revisado_por.nombre,
        "total_recibidas": sum(len(r.ruta_final) for r in body.rutas),
        "total_actualizadas": len(actualizadas),
        "actualizadas": actualizadas,
        "errores": errores,
        "base_de_datos": {"insertados": 0 if error_bd else len(registros), "error": error_bd},
    }
