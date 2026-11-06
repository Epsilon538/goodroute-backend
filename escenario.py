"""Escenario fabricado a mano para la prueba con usuarios (documento "Datos de la prueba 6-nov").

La semilla genera jornadas plausibles pero al azar. El escenario fija, sobre los datos de la semilla:
- la disponibilidad y el estado de algunos técnicos (inactivos o no disponibles un día de la semana);
- todas las OTs de un día planificado, con casos que el optimizador debe asignar o rechazar con una causa;
- reprogramaciones extra en el historial, para que las tareas del supervisor tengan respuestas claras.

Se aplica al final de cada `FuenteDatos.reiniciar`, es decir, al arrancar la API y en cada `/api/reset`, solo si
la semilla es la del escenario y su fecha no pasó. Si el archivo no existe, no hace nada.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from generadores import DIRECCIONES, azar_de, iso_utc, nueva_orden, numero_ot, programada_utc
from modelos import MOTIVOS_REPROGRAMACION, TIPOS_OT

ARCHIVO = Path(__file__).parent / "escenario_prueba.json"
CAMPOS_TECNICO = {"activo", "disponibilidad"}
CASOS = {"asignable", "d1", "misma_hora", "sin_georreferencia", "fuera_de_jornada"}
MARGEN_ANTES_DE_LA_VISITA_MIN = 60


def aplicar(fuente, archivo: Path = ARCHIVO) -> Optional[str]:
    """Aplica el escenario sobre los datos ya generados. Devuelve un resumen, o None si no hay archivo.

    Lanza ValueError si el escenario no es coherente con los datos (técnico u OT inexistente, dirección que no
    está en `direcciones_reales_chile.json`, tipo desconocido...), para que el error se vea al arrancar.
    """
    if not archivo.exists():
        return None
    escenario = json.loads(archivo.read_text(encoding="utf-8"))
    if fuente.semilla != escenario["semilla"]:
        return f"no aplicado: la semilla es {fuente.semilla} y el escenario es para la {escenario['semilla']}"
    fecha = date.fromisoformat(escenario["fecha"])
    if fecha < date.today():
        return f"no aplicado: su fecha ({fecha.isoformat()}) ya pasó"

    _ajustar_tecnicos(fuente, escenario["tecnicos"])
    ordenes = _reemplazar_ordenes(fuente, fecha, escenario["ordenes"])
    reprogramaciones = _agregar_reprogramaciones(fuente, escenario.get("reprogramaciones_historial", []))
    return (f"aplicado: {len(escenario['tecnicos'])} técnicos ajustados, {ordenes} OTs del {fecha.isoformat()}, "
            f"{reprogramaciones} reprogramaciones agregadas al historial")


def _ajustar_tecnicos(fuente, cambios: list[dict]) -> None:
    for cambio in cambios:
        tecnico = fuente.buscar_tecnico(cambio["id"])
        if tecnico is None:
            raise ValueError(f"Escenario: el técnico {cambio['id']} no existe.")
        if "disponibilidad" in cambio and (len(cambio["disponibilidad"]) != 7
                                            or not all(isinstance(d, bool) for d in cambio["disponibilidad"])):
            raise ValueError(f"Escenario: la disponibilidad de {cambio['id']} debe ser una lista de 7 booleanos.")
        tecnico.update({campo: valor for campo, valor in cambio.items() if campo in CAMPOS_TECNICO})


def _reemplazar_ordenes(fuente, fecha: date, ordenes: list[dict]) -> int:
    """Quita las OTs generadas para `fecha` y pone las del escenario, numeradas con `numero_ot` para esa fecha."""
    fuente.ordenes = [o for o in fuente.ordenes if o["fechaProgramada"] != fecha.isoformat()]
    for indice, ot in enumerate(ordenes):
        if ot["tipo"] not in TIPOS_OT:
            raise ValueError(f"Escenario: tipo de OT desconocido '{ot['tipo']}'.")
        if ot["caso"] not in CASOS:
            raise ValueError(f"Escenario: caso desconocido '{ot['caso']}'.")
        conocidas = {(d["direccion"], d["comuna"]) for d in DIRECCIONES.get(ot["region"], [])}
        real = (ot["direccion"], ot["comuna"]) in conocidas
        if real == (ot["caso"] == "sin_georreferencia"):
            raise ValueError(f"Escenario: la dirección '{ot['direccion']}, {ot['comuna']}' "
                             + ("es real y debería ser inventada." if real else "no está en direcciones_reales_chile.json."))
        hora, minuto = (int(x) for x in ot["hora"].split(":"))
        numero = numero_ot(fecha, indice)
        azar = azar_de(fuente.semilla, "ot", numero)
        direccion = {"direccion": ot["direccion"], "comuna": ot["comuna"]}
        fuente.ordenes.append(nueva_orden(
            numero, ot["tipo"], direccion, ot["region"], programada_utc(fecha, hora, minuto),
            azar.choice(fuente.clientes), azar,
        ))
    return len(ordenes)


def _momento(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def _agregar_reprogramaciones(fuente, reprogramaciones: list[dict]) -> int:
    """Agrega al historial reprogramaciones que mantienen al mismo técnico (cambio de hora acordado), así que
    no alteran la ejecución simulada de la OT. Cada una suma 1 en OTR para ese técnico."""
    for extra in reprogramaciones:
        if extra["motivo"] not in MOTIVOS_REPROGRAMACION:
            raise ValueError(f"Escenario: motivo de reprogramación desconocido '{extra['motivo']}'.")
        filas = [i for i, a in enumerate(fuente.asignaciones) if a["id_orden_trabajo"] == extra["ot"]]
        orden = fuente.buscar_orden(extra["ot"])
        if orden is None or len(filas) != 1:
            raise ValueError(f"Escenario: {extra['ot']} no está en el historial o ya fue reprogramada.")
        confirmacion = fuente.asignaciones[filas[0]]
        momento = _momento(confirmacion["fecha_asignacion"]) + timedelta(minutes=extra["minutos_despues_confirmacion"])
        visita = _momento(f"{orden['fechaProgramada']}T{orden['horaProgramada']}:00Z")  # ambas en UTC
        if momento > visita - timedelta(minutes=MARGEN_ANTES_DE_LA_VISITA_MIN):
            raise ValueError(f"Escenario: la reprogramación de {extra['ot']} queda muy cerca de la visita.")
        fuente.asignaciones.insert(filas[0] + 1, {
            **confirmacion,
            "id_propuesta": None,  # edición manual, igual que las reprogramaciones del historial
            "fecha_asignacion": iso_utc(momento),
            "fecha_generacion": iso_utc(momento),
            "motivo_reprogramacion": extra["motivo"],
            "inicio_planificacion": None,
            "propuesta_modificada": False,
            "tipo_modificacion": "Reprogramada con el mismo técnico",
        })
    return len(reprogramaciones)
