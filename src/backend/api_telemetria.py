"""
api_telemetria.py
Endpoints de consulta y administracion de la telemetria continua (ver telemetria.py).

  GET    /api/telemetria                  resumen de todos los equipos monitoreados
  GET    /api/telemetria/{ip}             resumen + serie de un equipo
  POST   /api/telemetria/objetivos        agregar un equipo  {"ip": "10.0.0.2"}
  DELETE /api/telemetria/objetivos/{ip}   dejar de monitorear un equipo

Los equipos tambien se agregan solos tras cada auditoria completa.
"""

from ipaddress import IPv4Address
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

import telemetria

router = APIRouter(prefix="/api/telemetria", tags=["telemetria"])


class Muestra(BaseModel):
    t: str
    ms: Optional[float] = None  # None = sin respuesta


class ResumenEquipo(BaseModel):
    ip: str
    estado: str  # "verde" | "amarillo" | "rojo" | "desconocido"
    ultimo_ms: Optional[float] = None
    promedio_ms: Optional[float] = None
    min_ms: Optional[float] = None
    max_ms: Optional[float] = None
    perdida_pct: float
    muestras: int
    serie: List[Muestra]


class ResumenTelemetria(BaseModel):
    activo: bool
    intervalo_seg: float
    error: Optional[str] = None
    objetivos: List[ResumenEquipo]


class SolicitudObjetivo(BaseModel):
    ip: IPv4Address

    @field_validator("ip")
    @classmethod
    def _ip_permitida(cls, v: IPv4Address) -> IPv4Address:
        if v.is_multicast or v.is_unspecified or v.is_loopback or v.is_link_local or v.is_reserved or not v.is_private:
            raise ValueError("Solo se pueden monitorear IPv4 unicast de redes privadas.")
        return v


@router.get("", response_model=ResumenTelemetria)
def consultar_telemetria() -> Dict[str, Any]:
    return telemetria.monitor.resumen_todos()


@router.get("/{ip}", response_model=ResumenEquipo)
def consultar_equipo(ip: IPv4Address) -> Dict[str, Any]:
    resumen = telemetria.monitor.resumen(str(ip))
    if resumen is None:
        raise HTTPException(status_code=404, detail=f"El equipo {ip} no esta siendo monitoreado.")
    return resumen


@router.post("/objetivos", status_code=201)
def agregar_objetivo(solicitud: SolicitudObjetivo) -> Dict[str, Any]:
    try:
        nuevo = telemetria.monitor.agregar_objetivo(str(solicitud.ip))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))
    return {"ip": str(solicitud.ip), "nuevo": nuevo}


@router.delete("/objetivos/{ip}", status_code=204)
def quitar_objetivo(ip: IPv4Address) -> None:
    if not telemetria.monitor.quitar_objetivo(str(ip)):
        raise HTTPException(status_code=404, detail=f"El equipo {ip} no esta siendo monitoreado.")
