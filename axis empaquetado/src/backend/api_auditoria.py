"""
api_auditoria.py
Endpoints del ciclo de auditoria completo y del historial local versionado.

  POST /api/auditoria/completa      ciclo completo (1 sesion SSH) + guardado + vinculo
  GET  /api/historial               auditorias guardadas (filtro opcional ?ip=)
  GET  /api/historial/{id}          una auditoria completa
  GET  /api/configs/versiones?ip=   versiones de running-config guardadas de un equipo
  GET  /api/configs/diff?base=&nueva=   diff entre dos versiones (mismo formato que el Diff Viewer)

No modifica /api/auditar, /api/ospf, /api/seguridad ni /api/configs/comparar.
"""

import threading
from ipaddress import IPv4Address
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, SecretStr

import almacenamiento
import auditoria

router = APIRouter(prefix="/api", tags=["auditoria"])
_cerrojo_ciclo = threading.Lock()  # un ciclo SSH a la vez (cuida CPU/RAM de la Raspberry Pi 4)
_ABRIR_SESION: Optional[Callable] = None  # gancho para pruebas; None = conexion.conectar / mock


class SolicitudAuditoria(BaseModel):
    """Mismos campos que CredencialesDispositivo, con IP validada y clave oculta en logs."""

    ip: IPv4Address
    usuario: str = Field("", max_length=64)
    password: SecretStr = SecretStr("")
    driver_name: Literal["ios", "vyos"] = "ios"


class CheckCompleto(BaseModel):
    check: str
    estado: str  # "verde" | "amarillo" | "rojo"
    mensaje: str
    detalle: List[str] = []


class ResultadoAuditoriaCompleta(BaseModel):
    hostname: str
    ip: str
    estado_general: str
    checks: List[CheckCompleto]
    auditoria_id: Optional[int] = None
    timestamp: Optional[str] = None
    seguridad: Optional[Dict[str, Any]] = None  # forma de ResultadoSeguridad
    ospf: Optional[Dict[str, Any]] = None  # forma de ResultadoOspf
    configs: Dict[str, Any]  # forma de ResultadoComparacion (running vs startup)
    respaldo: Dict[str, Any]  # {config_id, version, sha256, nuevo} o {error}


class ResumenAuditoria(BaseModel):
    id: int
    ip: str
    hostname: str
    timestamp: str
    estado_general: str
    score_seguridad: Optional[int] = None
    config_version: Optional[int] = None
    config_sha256: Optional[str] = None


def _error_http(error: Exception, codigo_defecto: int = 502) -> HTTPException:
    """ErrorConexion, ErrorAuditoria y ErrorAlmacenamiento exponen 'mensaje_usuario'."""
    mensaje = getattr(error, "mensaje_usuario", None)
    if mensaje:
        return HTTPException(status_code=codigo_defecto, detail=mensaje)
    return HTTPException(status_code=500, detail="Error interno inesperado.")


@router.post("/auditoria/completa", response_model=ResultadoAuditoriaCompleta)
def auditar_completo(solicitud: SolicitudAuditoria):
    if not _cerrojo_ciclo.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una auditoria en curso. Espere a que termine.")
    try:
        return auditoria.ejecutar_ciclo_auditoria(
            str(solicitud.ip), solicitud.usuario, solicitud.password.get_secret_value(),
            solicitud.driver_name, abrir_sesion=_ABRIR_SESION,
        )
    except HTTPException:
        raise
    except Exception as error:
        raise _error_http(error)
    finally:
        _cerrojo_ciclo.release()


@router.get("/historial", response_model=List[ResumenAuditoria])
def historial(ip: Optional[IPv4Address] = None, limite: int = Query(50, ge=1, le=200)):
    try:
        return almacenamiento.listar_auditorias(str(ip) if ip else None, limite)
    except Exception as error:
        raise _error_http(error, 500)


@router.get("/historial/{auditoria_id}")
def detalle_historial(auditoria_id: int):
    try:
        registro = almacenamiento.obtener_auditoria(auditoria_id)
    except Exception as error:
        raise _error_http(error, 500)
    if registro is None:
        raise HTTPException(status_code=404, detail=f"No existe la auditoria {auditoria_id}.")
    return registro


@router.get("/configs/versiones")
def versiones_config(ip: IPv4Address):
    try:
        return almacenamiento.listar_versiones(str(ip))
    except Exception as error:
        raise _error_http(error, 500)


@router.get("/configs/diff")
def diff_config(base: int, nueva: int):
    try:
        resultado = almacenamiento.diff_versiones(base, nueva)
    except Exception as error:
        raise _error_http(error, 500)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Alguna de las versiones indicadas no existe.")
    return resultado
