"""
mtu_stress.py
Ping de estres MTU para A.X.I.S.: envia pings con el bit DF (Don't Fragment) activado
desde el equipo auditado hacia un destino, a varios tamanos, para detectar si el camino
realmente soporta el MTU esperado (tuneles, VLAN/QinQ, jumbo frames mal configurados).

POR QUE NO SE USA device.ping() DE NAPALM
  ping(destination, source, ttl, timeout, size, count, vrf) no permite activar DF.
  Sin DF el equipo fragmenta y TODOS los tamanos "pasan", dejando la prueba sin sentido.
  Por eso se ejecuta el ping nativo de cada plataforma via device.cli().

CONVENCION DE TAMANOS
  La API siempre habla en tamano de paquete IP (L3, cabecera incluida): 1500 = MTU 1500.
  - Cisco IOS: 'size' ya es el datagrama IP completo -> se usa tal cual.
  - VyOS/Linux: 'size' es solo payload ICMP -> se resta 28 (20 IP + 8 ICMP).

SEGURIDAD (esto inyecta trafico, por eso esta acotado)
  - Nunca se concatena texto del usuario en la CLI: destino es IPv4Address validado y
    todo lo demas son enteros con rango; el driver sale de una lista cerrada.
  - Destino solo en redes privadas (el appliance no debe disparar trafico a Internet).
  - Maximo 6 tamanos x 10 repeticiones por solicitud; una sola prueba a la vez.
  - Sigue siendo Read-Only: el ping es un comando de ejecucion, no cambia la config.
"""

import re
import threading
from contextlib import AbstractContextManager
from ipaddress import IPv4Address
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator

MTU_MIN = 576          # minimo IPv4 que todo host debe aceptar (RFC 791)
MTU_MAX = 9000         # jumbo frames tipicos
MAX_TAMANOS = 6
MAX_REPETICIONES = 10
TIMEOUT_SONDA_SEG = 2
SOLO_REDES_PRIVADAS = True
_SOBRECARGA_ICMP_IPV4 = 28  # 20 bytes IP + 8 bytes ICMP


class ErrorPruebaMTU(Exception):
    """Falla de la prueba MTU. 'mensaje_usuario' es apto para la pantalla tactil."""

    def __init__(self, mensaje_usuario: str, detalle_tecnico: str = ""):
        self.mensaje_usuario = mensaje_usuario
        self.detalle_tecnico = detalle_tecnico
        super().__init__(mensaje_usuario)


# --------------------------------------------------------------------------------------
# Construccion de comandos y lectura de resultados (por plataforma)
# --------------------------------------------------------------------------------------
# Si un comando no te funciona en tu IOL/VyOS, este es el UNICO lugar que hay que tocar.
_CONSTRUCTORES: Dict[str, Callable[[str, int, int, int], str]] = {
    "ios": lambda destino, l3, n, t: f"ping {destino} df-bit repeat {n} size {l3} timeout {t}",
    "vyos": lambda destino, l3, n, t: (
        f"ping {destino} count {n} size {l3 - _SOBRECARGA_ICMP_IPV4} do-not-fragment"
    ),
}

_RE_IOS = re.compile(r"Success rate is (\d+) percent \((\d+)/(\d+)\)")
_RE_LINUX = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
_RE_TOO_BIG = re.compile(r"message too long|frag(?:mentation)? needed", re.IGNORECASE)


def construir_comando(driver: str, destino: IPv4Address, tamano_ip: int, cantidad: int,
                      timeout: int = TIMEOUT_SONDA_SEG) -> str:
    """Arma el comando de ping con DF. Solo acepta tipos ya validados (sin texto libre)."""
    if driver not in _CONSTRUCTORES:
        raise ErrorPruebaMTU(f"Error: la plataforma '{driver}' no esta soportada para la prueba MTU.")
    if not isinstance(destino, IPv4Address):
        raise ErrorPruebaMTU("Error: el destino debe ser una IPv4 valida.")
    return _CONSTRUCTORES[driver](str(destino), int(tamano_ip), int(cantidad), int(timeout))


def interpretar_salida(driver: str, salida: str, cantidad: int) -> Dict[str, int]:
    """Convierte la salida cruda del ping en {enviados, recibidos}."""
    if driver == "ios":
        m = _RE_IOS.search(salida)
        if m:
            return {"enviados": int(m.group(3)), "recibidos": int(m.group(2))}
    else:
        m = _RE_LINUX.search(salida)
        if m:
            return {"enviados": int(m.group(1)), "recibidos": int(m.group(2))}
        if _RE_TOO_BIG.search(salida):
            # Linux a veces aborta antes de imprimir el resumen cuando DF no cabe localmente
            return {"enviados": cantidad, "recibidos": 0}

    raise ErrorPruebaMTU(
        "Error: no se pudo interpretar la respuesta del ping del equipo.",
        detalle_tecnico=salida[:300],
    )


# --------------------------------------------------------------------------------------
# Ejecucion y evaluacion
# --------------------------------------------------------------------------------------
def _clasificar(enviados: int, recibidos: int) -> str:
    if enviados > 0 and recibidos == enviados:
        return "ok"
    if recibidos == 0:
        return "fallo"
    return "parcial"


def ejecutar_prueba_mtu(
    dispositivo: Any,
    driver: str,
    destino: IPv4Address,
    tamanos: List[int],
    mtu_esperado: int = 1500,
    cantidad: int = 3,
) -> Dict[str, Any]:
    """
    Corre la prueba sobre un 'dispositivo' NAPALM ya abierto y evalua el resultado.

    Estado global:
      rojo     -> el tamano del MTU esperado fallo (el camino NO lo soporta)
      amarillo -> el MTU esperado paso, pero hubo perdida parcial o fallos en otros tamanos
      verde    -> todos los tamanos pasaron sin perdida
    """
    tamanos = sorted(set(tamanos) | {mtu_esperado})
    sondas: List[Dict[str, Any]] = []

    for tamano in tamanos:
        comando = construir_comando(driver, destino, tamano, cantidad)
        try:
            salida = dispositivo.cli([comando])[comando]
        except Exception as error:
            raise ErrorPruebaMTU(
                "Error: el equipo no pudo ejecutar el ping. Verifique la sesion SSH y los privilegios.",
                detalle_tecnico=str(error),
            )

        conteo = interpretar_salida(driver, salida, cantidad)
        enviados, recibidos = conteo["enviados"], conteo["recibidos"]
        perdida = round(100 * (enviados - recibidos) / enviados, 1) if enviados else 100.0
        sondas.append({
            "tamano_ip": tamano,
            "enviados": enviados,
            "recibidos": recibidos,
            "perdida_pct": perdida,
            "resultado": _clasificar(enviados, recibidos),
        })

    exitosos = [s["tamano_ip"] for s in sondas if s["resultado"] == "ok"]
    mtu_maximo = max(exitosos) if exitosos else None
    sonda_esperada = next(s for s in sondas if s["tamano_ip"] == mtu_esperado)

    if sonda_esperada["resultado"] == "fallo":
        estado = "rojo"
        hasta = f"El mayor tamano que paso fue {mtu_maximo}." if mtu_maximo else "Ningun tamano probado paso."
        mensaje = (
            f"El camino hacia {destino} NO soporta MTU {mtu_esperado} sin fragmentar. {hasta} "
            "Revisar MTU de interfaces, tuneles o VLAN en el trayecto."
        )
    elif all(s["resultado"] == "ok" for s in sondas):
        estado = "verde"
        mensaje = f"El camino hacia {destino} soporta MTU {mtu_esperado} sin fragmentar."
    else:
        estado = "amarillo"
        mensaje = (
            f"MTU {mtu_esperado} paso hacia {destino}, pero hubo perdida o fallos en otros tamanos. "
            "Puede indicar congestion, duplex inconsistente o filtrado de ICMP."
        )

    return {
        "check": "mtu_stress",
        "estado": estado,
        "mensaje": mensaje,
        "destino": str(destino),
        "mtu_esperado": mtu_esperado,
        "mtu_maximo_exitoso": mtu_maximo,
        "sondas": sondas,
    }


# --------------------------------------------------------------------------------------
# API (FastAPI)
# --------------------------------------------------------------------------------------
class SolicitudMTU(BaseModel):
    ip_equipo: IPv4Address
    usuario: str = Field("", max_length=64)  # vacio permitido (modo mock); en modo real, conectar() informa "credenciales incorrectas"
    password: SecretStr  # SecretStr evita que la clave salga en logs o repr
    driver: Literal["ios", "vyos"] = "ios"
    destino: IPv4Address
    mtu_esperado: int = Field(1500, ge=MTU_MIN, le=MTU_MAX)
    tamanos: Optional[List[int]] = None  # por defecto: [1400, mtu_esperado]
    cantidad: int = Field(3, ge=1, le=MAX_REPETICIONES)

    @field_validator("destino")
    @classmethod
    def _destino_permitido(cls, v: IPv4Address) -> IPv4Address:
        if v.is_multicast or v.is_unspecified or v.is_loopback or v.is_link_local or v.is_reserved:
            raise ValueError("El destino debe ser una IPv4 unicast valida.")
        if SOLO_REDES_PRIVADAS and not v.is_private:
            raise ValueError("Por seguridad, la prueba solo se permite hacia redes privadas.")
        return v

    @field_validator("tamanos")
    @classmethod
    def _tamanos_validos(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is None:
            return v
        if not 1 <= len(v) <= MAX_TAMANOS:
            raise ValueError(f"Indique entre 1 y {MAX_TAMANOS} tamanos.")
        if any(not (MTU_MIN <= t <= MTU_MAX) for t in v):
            raise ValueError(f"Cada tamano debe estar entre {MTU_MIN} y {MTU_MAX} bytes.")
        return v


class ResultadoSonda(BaseModel):
    tamano_ip: int
    enviados: int
    recibidos: int
    perdida_pct: float
    resultado: str  # "ok" | "parcial" | "fallo"


class ResultadoMTU(BaseModel):
    check: str
    estado: str  # "verde" | "amarillo" | "rojo"
    mensaje: str
    destino: str
    mtu_esperado: int
    mtu_maximo_exitoso: Optional[int] = None
    sondas: List[ResultadoSonda]


router = APIRouter(prefix="/api", tags=["mtu"])
_cerrojo_prueba = threading.Lock()  # una sola prueba de trafico a la vez


def _abrir_sesion(solicitud: SolicitudMTU) -> AbstractContextManager:
    """
    Abre la sesion con el context manager `conectar()` de conexion.py (una sola sesion SSH,
    cierre garantizado y errores traducidos a ErrorConexion). Con AXIS_MODO=mock usa el
    equipo simulado, para probar la pantalla sin laboratorio.
    """
    from mock_dispositivo import modo_mock, sesion_mock

    if modo_mock():
        return sesion_mock()

    from conexion import conectar  # import diferido: no exige NAPALM para importar este modulo

    return conectar(
        str(solicitud.ip_equipo),
        solicitud.usuario,
        solicitud.password.get_secret_value(),
        solicitud.driver,
    )


@router.post("/mtu-stress", response_model=ResultadoMTU)
def mtu_stress(solicitud: SolicitudMTU):
    """Ejecuta el ping de estres MTU (con DF) desde el equipo hacia 'destino'."""
    if not _cerrojo_prueba.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una prueba de trafico en curso. Espere a que termine.")
    try:
        tamanos = solicitud.tamanos or [1400, solicitud.mtu_esperado]
        with _abrir_sesion(solicitud) as dispositivo:
            return ejecutar_prueba_mtu(
                dispositivo,
                solicitud.driver,
                solicitud.destino,
                tamanos,
                mtu_esperado=solicitud.mtu_esperado,
                cantidad=solicitud.cantidad,
            )
    except HTTPException:
        raise
    except Exception as error:
        # ErrorPruebaMTU y el ErrorConexion de conexion.py exponen 'mensaje_usuario'.
        mensaje = getattr(error, "mensaje_usuario", None)
        if mensaje:
            raise HTTPException(status_code=502, detail=mensaje)
        raise HTTPException(status_code=500, detail="Error interno inesperado durante la prueba MTU.")
    finally:
        _cerrojo_prueba.release()
