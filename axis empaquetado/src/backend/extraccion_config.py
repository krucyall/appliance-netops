"""
extraccion_config.py
Extraccion de la running-config completa via NAPALM para A.X.I.S.

Pensado para usarse DENTRO de la sesion SSH unica que ya abre tu context manager
de conexion.py: recibe el objeto 'dispositivo' ya abierto (no abre ni cierra nada),
asi no se hacen handshakes extra en la Raspberry Pi.

Seguridad (Competencia 1): la config puede traer contrasenas, secretos y comunidades
SNMP. Por eso:
  1) se pide a NAPALM sanitized=True (get_config(retrieve, full, sanitized));
  2) si el driver no acepta ese parametro, se reintenta sin el;
  3) SIEMPRE se aplica ademas una redaccion propia (defensa en profundidad), de modo
     que ningun secreto llegue al JSON de la API, a los logs ni al disco.

Modo Read-Only: solo usa get_config(retrieve="running"); no toca el candidate ni
hace commit.
"""

import hashlib
import re
from typing import Any, Dict

# Cada patron conserva el prefijo (grupo 1) y reemplaza solo el secreto.
_PATRONES_SECRETOS = [
    # Cisco IOS: enable secret/password, username ... secret/password, line/vty password
    (re.compile(r"(?im)^(\s*enable\s+(?:secret|password)(?:\s+\d)?\s+)\S+"), r"\1<removed>"),
    (re.compile(r"(?im)^(\s*username\s+\S+\s+.*?(?:secret|password)(?:\s+\d)?\s+)\S+"), r"\1<removed>"),
    (re.compile(r"(?im)^(\s*password(?:\s+\d)?\s+)\S+"), r"\1<removed>"),
    # SNMP communities y claves de protocolos (OSPF, TACACS/RADIUS, NTP)
    (re.compile(r"(?im)^(\s*snmp-server\s+community\s+)\S+"), r"\1<removed>"),
    (re.compile(r"(?im)^(\s*ip\s+ospf\s+authentication-key\s+(?:\d\s+)?)\S+"), r"\1<removed>"),
    (re.compile(r"(?im)^(\s*ip\s+ospf\s+message-digest-key\s+\d+\s+md5\s+(?:\d\s+)?)\S+"), r"\1<removed>"),
    (re.compile(r"(?im)^(\s*(?:tacacs|radius)-server\s+key\s+(?:\d\s+)?)\S+"), r"\1<removed>"),
    (re.compile(r"(?im)^(\s*key-string\s+(?:\d\s+)?)\S+"), r"\1<removed>"),
    # VyOS: encrypted-password / plaintext-password / community 'xxx'
    (re.compile(r"(?i)((?:encrypted|plaintext)-password\s+)'[^']*'"), r"\1'<removed>'"),
    (re.compile(r"(?im)^(\s*community\s+)\S+"), r"\1<removed>"),
]


class ErrorExtraccionConfig(Exception):
    """Falla al extraer la config. 'mensaje_usuario' es apto para la pantalla tactil."""

    def __init__(self, mensaje_usuario: str, detalle_tecnico: str = ""):
        self.mensaje_usuario = mensaje_usuario
        self.detalle_tecnico = detalle_tecnico
        super().__init__(mensaje_usuario)


def redactar_secretos(texto: str) -> str:
    """Reemplaza contrasenas/secretos/comunidades por <removed>. Idempotente."""
    for patron, reemplazo in _PATRONES_SECRETOS:
        texto = patron.sub(reemplazo, texto)
    return texto


def _normalizar(texto: str) -> str:
    """Quita ruido que cambia en cada lectura sin que cambie la config real."""
    lineas = []
    for linea in texto.splitlines():
        l = linea.rstrip()
        if not l:
            continue
        # Cabeceras tipicas de IOS que traen fecha/tamano y rompen el hash
        if l.startswith(("Building configuration", "Current configuration", "! Last configuration change",
                         "! NVRAM config last updated", "! No configuration change since")):
            continue
        lineas.append(l)
    return "\n".join(lineas)


def obtener_running_config(dispositivo: Any, sanitizar: bool = True) -> Dict[str, Any]:
    """
    Extrae la running-config completa del equipo (ya conectado).

    Retorna:
        {
          "running":    str   config con secretos redactados,
          "lineas":     int   cantidad de lineas utiles,
          "sanitizada": bool  si se aplico sanitizado,
          "sha256":     str   huella de la config normalizada (sirve para detectar
                              cambios entre auditorias y como evidencia de integridad),
        }
    Lanza ErrorExtraccionConfig con mensaje apto para pantalla ante cualquier falla.
    """
    try:
        try:
            bruto = dispositivo.get_config(retrieve="running", sanitized=sanitizar)
        except TypeError:
            # Driver comunitario cuyo get_config no acepta 'sanitized': se reintenta
            # y la redaccion propia (mas abajo) cubre el hueco.
            bruto = dispositivo.get_config(retrieve="running")
    except ErrorExtraccionConfig:
        raise
    except Exception as error:
        raise ErrorExtraccionConfig(
            "Error: No se pudo extraer la configuracion del equipo. "
            "Verifique que la sesion SSH siga activa y que el usuario tenga privilegios de lectura.",
            detalle_tecnico=str(error),
        )

    running = bruto.get("running", "") if isinstance(bruto, dict) else ""
    if not isinstance(running, str) or not running.strip():
        raise ErrorExtraccionConfig(
            "Error: El equipo respondio, pero la configuracion en ejecucion llego vacia.",
            detalle_tecnico=f"get_config devolvio claves: {list(bruto) if isinstance(bruto, dict) else type(bruto)}",
        )

    running = redactar_secretos(running)
    normalizada = _normalizar(running)

    return {
        "running": running,
        "lineas": len(normalizada.splitlines()),
        "sanitizada": bool(sanitizar),
        "sha256": hashlib.sha256(normalizada.encode("utf-8")).hexdigest(),
    }


def obtener_configs(dispositivo: Any, sanitizar: bool = True) -> Dict[str, Any]:
    """
    Extrae running-config Y startup-config en UNA sola llamada a get_config(retrieve="all"),
    para alimentar el modulo Disaster Recovery (comparar_configs) sin un segundo viaje SSH.

    Ambas salen con secretos redactados. La startup puede venir vacia (equipo sin
    'write memory'): eso NO es un error, solo significa que no hay nada guardado.
    La running vacia SI es un error.

    Retorna lo mismo que obtener_running_config() mas la clave "startup".
    """
    try:
        try:
            bruto = dispositivo.get_config(retrieve="all", sanitized=sanitizar)
        except TypeError:
            bruto = dispositivo.get_config(retrieve="all")
    except Exception as error:
        raise ErrorExtraccionConfig(
            "Error: No se pudo extraer la configuracion del equipo. "
            "Verifique que la sesion SSH siga activa y que el usuario tenga privilegios de lectura.",
            detalle_tecnico=str(error),
        )

    running = bruto.get("running", "") if isinstance(bruto, dict) else ""
    startup = bruto.get("startup", "") if isinstance(bruto, dict) else ""
    if not isinstance(running, str) or not running.strip():
        raise ErrorExtraccionConfig(
            "Error: El equipo respondio, pero la configuracion en ejecucion llego vacia.",
            detalle_tecnico=f"get_config devolvio claves: {list(bruto) if isinstance(bruto, dict) else type(bruto)}",
        )

    running = redactar_secretos(running)
    startup = redactar_secretos(startup) if isinstance(startup, str) else ""
    normalizada = _normalizar(running)

    return {
        "running": running,
        "startup": startup,
        "lineas": len(normalizada.splitlines()),
        "sanitizada": bool(sanitizar),
        "sha256": hashlib.sha256(normalizada.encode("utf-8")).hexdigest(),
    }
