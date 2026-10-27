import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

import db
from datos import fuente
from modelos import (
    ESTADOS_OT,
    MOTIVOS_REPROGRAMACION,
    TIPOS_MODIFICACION_PATCH,
    AsignarTecnicoRequest,
    AsignarTecnicosRequest,
)

router = APIRouter()

ESTADOS_ASIGNABLES = ("por_revisar", "por_asignar")
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


def _fecha_hora_ot(orden: dict) -> Optional[str]:
    fecha, hora = orden.get("fecha_programada"), orden.get("hora_programada")
    if not (fecha and hora):
        return None
    if hora.count(":") == 1:
        hora += ":00"
    return f"{fecha}T{hora}"


def _asignar_en_memoria(orden: dict, tecnico_id: Optional[str]) -> None:
    orden["tecnico_id"] = tecnico_id
    if tecnico_id is None:
        orden["estado"] = "por_asignar"
    elif orden["estado"] in ESTADOS_ASIGNABLES:
        orden["estado"] = "asignacion_por_confirmar"


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
            "GET  /api/tecnicos",
            "GET  /api/ordenes?estado=&fecha=",
            "GET  /api/reset",
            "GET  /api/disponibilidad",
            "PATCH /api/ordenes/{id}/tecnico",
            "POST /api/asignar-tecnicos",
        ],
    }


@router.get("/api/reset", tags=["Admin"], summary="Reiniciar y regenerar todos los datos")
def reset():
    """Regenera en memoria técnicos, OTs y disponibilidades (vuelve al estado inicial)."""
    fuente.reiniciar()
    return {
        "mensaje": "Reset completado. Todos los datos han sido regenerados.",
        "tecnicos": len(fuente.tecnicos),
        "ordenes": len(fuente.ordenes),
        "disponibilidades": len(fuente.disponibilidades),
    }


# --- Lecturas ---

@router.get("/api/tecnicos", tags=["Tecnicos"], summary="Obtener lista de tecnicos")
def get_tecnicos():
    return fuente.tecnicos


@router.get("/api/ordenes", tags=["Ordenes de Trabajo"], summary="Obtener lista de ordenes de trabajo")
def get_ordenes(
    estado: Optional[str] = Query(
        default=None,
        description=f"Filtra por estado: {' | '.join(ESTADOS_OT)}",
        examples=["por_asignar"],
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
        ordenes = [o for o in ordenes if o["fecha_programada"] == fecha]
    return ordenes


@router.get("/api/disponibilidad", tags=["Disponibilidad"], summary="Obtener disponibilidades de tecnicos")
def get_disponibilidad(
    fecha: Optional[str] = Query(
        default=None,
        description="Filtra por fecha (YYYY-MM-DD)",
        examples=["2026-10-05"],
    ),
):
    if fecha is None:
        return fuente.disponibilidades
    _validar_fecha(fecha)
    return [d for d in fuente.disponibilidades if d["fecha"] == fecha]


# --- Asignación ---

@router.patch(
    "/api/ordenes/{orden_id}/tecnico",
    tags=["Ordenes de Trabajo"],
    summary="Asignar o reprogramar tecnico de una orden de trabajo",
)
def asignar_tecnico(orden_id: str, body: AsignarTecnicoRequest):
    """Registra un evento de asignación para una OT (siempre inserta una fila nueva).

    - **Asignar**: `tecnico_id` sin `motivo_reprogramacion`.
    - **Reprogramar**: `tecnico_id` nuevo con `motivo_reprogramacion`.
    - **Descartar**: `tecnico_id` null; la OT vuelve a `por_asignar`.
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

    tecnico_anterior = orden["tecnico_id"]
    _asignar_en_memoria(orden, body.tecnico_id)

    error = _guardar([{
        "id_orden_trabajo": orden_id,
        "id_propuesta": tecnico_anterior,
        "id_aceptada": body.tecnico_id,
        "fecha_asignacion": None,
        "fecha_orden_de_trabajo": _fecha_hora_ot(orden),
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

            _asignar_en_memoria(orden, ruta.tecnico_id)
            actualizadas.append({"ot_id": ot_id, "tecnico_id": ruta.tecnico_id, "estado": orden["estado"]})
            registros.append({
                "id_orden_trabajo": ot_id,
                "id_propuesta": propuesta_por_ot.get(ot_id),
                "id_aceptada": ruta.tecnico_id,
                "fecha_asignacion": body.revisado_en,
                "fecha_orden_de_trabajo": _fecha_hora_ot(orden),
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
