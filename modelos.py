from typing import Optional

from pydantic import BaseModel

TIPOS_OT = [
    "instalacionSimple",
    "instalacionConCorte",
    "mantencion",
    "retiro",
]

ESTADOS_OT = [
    "porRevisar",
    "porAsignar",
    "enviadaPlanificacion",
    "asignada",
    "enTerreno",
    "parcial",
    "finalizada",
    "enviadaCobranza",
]

ESTADO_POR_ASIGNAR = "porAsignar"
ESTADO_ASIGNADA = "asignada"
# Estados desde los que asignar un técnico deja la OT en asignada (ya no se usa asignacionPorConfirmar).
ESTADOS_ASIGNABLES = ("porRevisar", "porAsignar", "enviadaPlanificacion")

# Deben coincidir con el CHECK de asignacion.motivo_reprogramacion en la BD.
MOTIVOS_REPROGRAMACION = [
    "Cliente No Disponible",
    "Falta de Coordinación",
    "Técnico No Disponible",
    "Falta de Tiempo",
    "Problema de Ruta",
    "Problema Técnico",
    "Dirección Incorrecta",
    "Cliente Solicitó Cambio",
    "Otro",
]

TIPOS_MODIFICACION_PATCH = [
    "OT asignada a otro técnico",
    "OT reordenada",
    "OT descartada",
]


class RevisadoPor(BaseModel):
    usuario_id: str
    nombre: str
    rol: str


class AsignarTecnicoRequest(BaseModel):
    """Body del PATCH. `tecnico_id` null significa OT descartada."""

    tecnico_id: Optional[str] = None
    motivo_reprogramacion: Optional[str] = None
    revisado_por: Optional[RevisadoPor] = None
    tipo_modificacion: Optional[str] = None
    inicio_planificacion: Optional[str] = None
    kilometraje_estimado: Optional[float] = None


class ResumenCorrida(BaseModel):
    total_ots: int
    ots_asignadas: int
    ots_pendientes: int
    total_tecnicos: int
    tecnicos_utilizados: int
    distancia_total_km: Optional[float] = None
    fuente_matriz: str


class ParadaRuta(BaseModel):
    ot_id: str
    secuencia: int
    tipo: str
    direccion: str
    hora_estimada_llegada: str
    espera_min: int
    # Km desde donde sale el técnico (base o OT anterior) hasta esta OT; las rutas no vuelven a la base.
    kilometraje_estimado: Optional[float] = None


class RutaTecnico(BaseModel):
    tecnico_id: str
    tecnico_nombre: str
    zona_base: str
    distancia_total_km: Optional[float] = None
    capacidad_uso: str
    hora_salida_base: str
    hora_retorno_base: Optional[str] = None
    ruta_propuesta: list[str]
    ruta_final: list[str]
    aceptada_sin_modificacion: bool
    modificaciones: list[dict] = []
    paradas: list[ParadaRuta]


class AsignarTecnicosRequest(BaseModel):
    """Corrida del optimizador ya revisada y aprobada por la dispatcher."""

    fecha_planificacion: str
    inicio_planificacion: Optional[str] = None
    generado_en: str
    revisado_en: str
    revisado_por: RevisadoPor
    resumen_corrida: ResumenCorrida
    rutas: list[RutaTecnico]
    pendientes: list[dict] = []
