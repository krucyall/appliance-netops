"""
extractores.py
Extraccion de datos reales de los equipos para los modulos OSPF,
Seguridad L2 y Disaster Recovery (modo real de main.py).

Cada `obtener_*()` abre una sesion con `conexion.conectar()`, saca los
datos que necesita y los entrega con EXACTAMENTE la misma forma que el
mock JSON equivalente (mock_ospf.json, mock_seguridad.json,
mock_configs.json). Asi evaluador.py no se entera de si los datos son
reales o simulados.

NAPALM no tiene getters para OSPF ni para la seguridad L2 de cada
puerto, por lo que esos dos modulos leen la salida de comandos de
Cisco IOS. Los `parsear_*()` son funciones puras (texto -> dict), sin
red, para poder probarlas offline con salidas de ejemplo.
"""

import re
from typing import Any, Callable, Dict, List

from conexion import ErrorConexion, conectar

# Drivers cuyo formato de salida saben leer los parsers de este archivo.
DRIVERS_CLI_SOPORTADOS = {"ios"}

COMANDO_OSPF = "show ip ospf neighbor detail"

# Lineas que cambian solas en la config de IOS y ensuciarian el diff
# running vs startup sin ser un cambio real del operador.
_LINEAS_RUIDO_CONFIG = re.compile(r"^ntp clock-period .*$", re.M)


def _extraer(
    ip: str,
    usuario: str,
    password: str,
    driver_name: str,
    extractor: Callable[[Any], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Abre una sesion, corre `extractor(dispositivo)` y cierra la sesion.

    Traduce cualquier error posterior a la conexion (comando rechazado,
    salida inesperada) a ErrorConexion, igual que `obtener_facts()`.
    """
    with conectar(ip, usuario, password, driver_name) as dispositivo:
        try:
            return extractor(dispositivo)
        except ErrorConexion:
            raise
        except Exception as error:
            raise ErrorConexion(
                "Se logro conectar, pero fallo la extraccion de datos del equipo.",
                detalle_tecnico=str(error),
            )


def _exigir_driver_cli(driver_name: str, modulo: str) -> None:
    """Corta antes de conectarse si el parser no sabe leer ese tipo de equipo."""
    if driver_name not in DRIVERS_CLI_SOPORTADOS:
        raise ErrorConexion(
            f"Error: El modulo {modulo} por ahora solo esta disponible para "
            f"equipos Cisco IOS (este equipo usa el driver '{driver_name}')."
        )


def _hostname_desde_config(config: str) -> str:
    match = re.search(r"^hostname (\S+)", config, re.M)
    return match.group(1) if match else "desconocido"


# --- OSPF ---------------------------------------------------------------


def parsear_ospf_detalle(salida: str) -> List[Dict[str, Any]]:
    """
    Convierte la salida de `show ip ospf neighbor detail` (Cisco IOS) en
    la lista 'vecinos_ospf' que espera evaluador.evaluar_ospf().

    Ejemplo de un bloque de la salida:
         Neighbor 10.0.0.2, interface address 10.0.0.2
            In the area 0 via interface GigabitEthernet0/1
            Neighbor priority is 1, State is FULL, 6 state changes
            ...
            Dead timer due in 00:00:38

    Lanza ValueError si el equipo rechazo el comando (lineas con '%',
    ej: OSPF no soportado en esa imagen).
    """
    if re.search(r"^\s*% ", salida, re.M):
        raise ValueError(f"El equipo rechazo '{COMANDO_OSPF}': {salida.strip()}")

    vecinos = []
    # Cada vecino empieza en una linea "Neighbor <id>, interface address <ip>"
    bloques = re.split(r"^\s*(?=Neighbor \S+, interface address)", salida, flags=re.M)

    for bloque in bloques:
        cabecera = re.match(r"Neighbor (\S+), interface address", bloque)
        if not cabecera:
            continue

        area = re.search(r"In the area (\S+) via interface (\S+)", bloque)
        estado = re.search(r"State is ([^,\s]+)", bloque)
        muerto = re.search(r"Dead timer due in (\d+):(\d+):(\d+)", bloque)

        tiempo_muerto_seg = 0
        if muerto:
            horas, minutos, segundos = (int(x) for x in muerto.groups())
            tiempo_muerto_seg = horas * 3600 + minutos * 60 + segundos

        vecinos.append({
            "vecino_id": cabecera.group(1),
            "interfaz": area.group(2) if area else "?",
            "area": area.group(1) if area else "?",
            # En algunas versiones el estado viene como "FULL/DR": solo
            # interesa la parte del estado de la adyacencia.
            "estado": estado.group(1).split("/")[0].upper() if estado else "DESCONOCIDO",
            "tiempo_muerto_seg": tiempo_muerto_seg,
        })

    return vecinos


def obtener_vecinos_ospf(
    ip: str, usuario: str, password: str, driver_name: str = "ios"
) -> Dict[str, Any]:
    """Version real de mock_ospf.json."""
    _exigir_driver_cli(driver_name, "OSPF")

    def extractor(dispositivo) -> Dict[str, Any]:
        salida = dispositivo.cli([COMANDO_OSPF])[COMANDO_OSPF]
        try:
            vecinos = parsear_ospf_detalle(salida)
        except ValueError as error:
            raise ErrorConexion(
                "Error: El equipo no acepto el comando de OSPF. Verifique que "
                "su imagen soporte OSPF (equipo de capa 3).",
                detalle_tecnico=str(error),
            )
        return {
            "hostname": dispositivo.get_facts().get("hostname", "desconocido"),
            "vecinos_ospf": vecinos,
        }

    return _extraer(ip, usuario, password, driver_name, extractor)


# --- Seguridad L2 -------------------------------------------------------


def parsear_interfaces_l2(running_config: str) -> List[Dict[str, Any]]:
    """
    Lee la running-config (Cisco IOS) y arma la lista
    'interfaces_analizadas' que espera evaluador.calcular_score_seguridad().

    Solo se analizan puertos configurados con 'switchport mode access'.
    Se respetan los defaults globales de IOS:
        - Sin 'switchport access vlan N', el puerto queda en VLAN 1.
        - 'spanning-tree portfast [edge] default' activa PortFast en
          todos los puertos de acceso (salvo 'spanning-tree portfast disable').
        - 'spanning-tree portfast [edge] bpduguard default' activa BPDU
          Guard en los puertos con PortFast (salvo 'spanning-tree bpduguard disable').
    """
    running_config = running_config.replace("\r\n", "\n")
    portfast_global = bool(
        re.search(r"^spanning-tree portfast (edge )?default\s*$", running_config, re.M)
    )
    bpduguard_global = bool(
        re.search(r"^spanning-tree portfast (edge )?bpduguard default\s*$", running_config, re.M)
    )

    interfaces = []
    # Un bloque = linea "interface X" + sus lineas indentadas
    for match in re.finditer(r"^interface (\S+)\n((?:[ \t].*\n?)*)", running_config, re.M):
        nombre = match.group(1)
        lineas = [linea.strip() for linea in match.group(2).splitlines()]

        if "switchport mode access" not in lineas:
            continue

        vlan_acceso = 1
        portfast = None  # None = no configurado en el puerto, manda el global
        bpdu_guard = None

        for linea in lineas:
            vlan = re.fullmatch(r"switchport access vlan (\d+)", linea)
            if vlan:
                vlan_acceso = int(vlan.group(1))
            elif linea in ("spanning-tree portfast", "spanning-tree portfast edge"):
                portfast = True
            elif linea == "spanning-tree portfast disable":
                portfast = False
            elif linea == "spanning-tree bpduguard enable":
                bpdu_guard = True
            elif linea == "spanning-tree bpduguard disable":
                bpdu_guard = False

        if portfast is None:
            portfast = portfast_global
        if bpdu_guard is None:
            bpdu_guard = bpduguard_global and portfast

        interfaces.append({
            "interfaz": nombre,
            "vlan_acceso": vlan_acceso,
            "bpdu_guard": bpdu_guard,
            "portfast": portfast,
        })

    return interfaces


def obtener_interfaces_l2(
    ip: str, usuario: str, password: str, driver_name: str = "ios"
) -> Dict[str, Any]:
    """Version real de mock_seguridad.json."""
    _exigir_driver_cli(driver_name, "Seguridad L2")

    def extractor(dispositivo) -> Dict[str, Any]:
        running = dispositivo.get_config(retrieve="running")["running"]
        return {
            "hostname": _hostname_desde_config(running),
            "interfaces_analizadas": parsear_interfaces_l2(running),
        }

    return _extraer(ip, usuario, password, driver_name, extractor)


# --- Disaster Recovery (running vs startup) -----------------------------


def obtener_configs(
    ip: str, usuario: str, password: str, driver_name: str = "ios"
) -> Dict[str, Any]:
    """
    Version real de mock_configs.json.

    Usa get_config(sanitized=True) para que contrasenas, secrets y
    communities SNMP lleguen como '<removed>': el diff se muestra en
    pantalla y no debe exponerlas.
    """

    def extractor(dispositivo) -> Dict[str, Any]:
        configs = dispositivo.get_config(retrieve="all", sanitized=True)
        running = _LINEAS_RUIDO_CONFIG.sub("", configs.get("running", ""))
        startup = _LINEAS_RUIDO_CONFIG.sub("", configs.get("startup", ""))

        if not startup.strip():
            raise ErrorConexion(
                "Error: El equipo no entrego una startup-config. Puede que nunca "
                "se haya guardado ('write memory') o que este tipo de equipo no "
                "la soporte."
            )

        return {
            "hostname": _hostname_desde_config(running),
            "running_config": running,
            "startup_config": startup,
        }

    return _extraer(ip, usuario, password, driver_name, extractor)
