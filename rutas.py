import json
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

import db
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
            "GET  /api/reset",
        ],
    }


@router.get("/api/reset", tags=["Admin"], summary="Reiniciar y regenerar todos los datos")
def reset():
    """Regenera en memoria técnicos, clientes y OTs (vuelve al estado inicial)."""
    fuente.reiniciar()
    return {
        "mensaje": "Reset completado. Todos los datos han sido regenerados.",
        "tecnicos": len(fuente.tecnicos),
        "ordenes": len(fuente.ordenes),
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

    tecnico_anterior = orden["tecnicoId"]
    _asignar_en_memoria(orden, body.tecnico_id)

    error = _guardar([{
        "id_orden_trabajo": orden_id,
        "id_propuesta": tecnico_anterior,
        "id_aceptada": body.tecnico_id,
        "fecha_asignacion": None,
        "fecha_orden_de_trabajo": fecha_ot,
        "motivo_reprogramacion": body.motivo_reprogramacion,
        "usuario_dispatcher": body.revisado_por.nombre if body.revisado_por else None,
        "fecha_generacion": None,
        "propuesta_modificada": body.motivo_reprogramacion is not None,
        "tipo_modificacion": body.tipo_modificacion,
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

    Estrategia parcial: lo válido se aplica y los errores se reportan sin detener el resto.
    """
    propuesta_por_ot = {
        ot_id: ruta.tecnico_id for ruta in body.rutas for ot_id in ruta.ruta_propuesta
    }
    actualizadas, errores, registros = [], [], []

    for ruta in body.rutas:
        if fuente.buscar_tecnico(ruta.tecnico_id) is None:
            errores += [
                {"ot_id": ot_id, "error": f"Tecnico con id '{ruta.tecnico_id}' no encontrado en el sistema."}
                for ot_id in ruta.ruta_final
            ]
            continue

        tipo_modificacion = (
            json.dumps(ruta.modificaciones, ensure_ascii=False)[:LARGO_MAX_TIPO_MODIFICACION]
            if ruta.modificaciones
            else None
        )

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

            _asignar_en_memoria(orden, ruta.tecnico_id)
            actualizadas.append({"ot_id": ot_id, "tecnico_id": ruta.tecnico_id, "estado": orden["estado"]})
            registros.append({
                "id_orden_trabajo": ot_id,
                "id_propuesta": propuesta_por_ot.get(ot_id),
                "id_aceptada": ruta.tecnico_id,
                "fecha_asignacion": body.revisado_en,
                "fecha_orden_de_trabajo": fecha_ot,
                "motivo_reprogramacion": None,
                "usuario_dispatcher": body.revisado_por.nombre,
                "fecha_generacion": body.generado_en,
                "propuesta_modificada": not ruta.aceptada_sin_modificacion,
                "tipo_modificacion": tipo_modificacion,
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
