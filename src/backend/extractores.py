"""
extractores.py
Convierte la salida cruda de un equipo Cisco IOS (running-config y comandos show) en los
diccionarios que ya esperan tus evaluadores. NAPALM no tiene getters para VLAN de acceso,
PortFast/BPDU Guard ni vecinos OSPF, asi que se derivan de texto.

  extraer_interfaces_l2(running)            -> {"interfaces_analizadas": [...]}  (calcular_score_seguridad)
  parsear_vecinos_ospf_ios(neigh, brief)    -> {"vecinos_ospf": [...]}           (evaluar_ospf)

Son funciones puras (texto -> dict): se prueban offline y no abren ninguna sesion.
Soporte actual: Cisco IOS / IOL / IOSv. VyOS queda fuera (otra sintaxis).
"""

import re
from typing import Any, Dict, List, Optional

_RE_INTERFAZ = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_RE_VIRTUAL = re.compile(r"^(loopback|null|vlan|tunnel|port-channel|bvi|vi)\d*", re.IGNORECASE)


def _bloques_interfaz(running: str) -> Dict[str, List[str]]:
    """Divide la config en {nombre_interfaz: [lineas_indentadas]} (se corta con '!' o linea sin sangria)."""
    bloques: Dict[str, List[str]] = {}
    actual: Optional[str] = None
    for crudo in running.splitlines():
        m = _RE_INTERFAZ.match(crudo.strip()) if not crudo.startswith(" ") else None
        if m:
            actual = m.group(1)
            bloques[actual] = []
        elif actual is not None and crudo.startswith(" "):
            bloques[actual].append(crudo.strip().lower())
        else:
            actual = None
    return bloques


def extraer_interfaces_l2(running: str) -> Dict[str, Any]:
    """
    Deriva, por cada puerto de ACCESO activo, la VLAN, PortFast y BPDU Guard efectivos.

    Reglas (como IOS las aplica):
      - Es switch si hay 'switchport' o 'spanning-tree mode' en la config; si no, retorna [].
      - Se omiten: interfaces virtuales, troncales, puertos 'no switchport' o con 'ip address'
        (enrutados) y puertos en 'shutdown' (los apagados no son superficie de ataque).
      - VLAN de acceso: 'switchport access vlan N'; si no existe, IOS usa la VLAN 1.
      - PortFast: por interfaz ('spanning-tree portfast' / 'portfast edge') o global
        ('spanning-tree portfast default'), salvo 'spanning-tree portfast disable'.
      - BPDU Guard: por interfaz ('spanning-tree bpduguard enable') o global
        ('spanning-tree portfast bpduguard default', que solo aplica a puertos PortFast),
        salvo 'spanning-tree bpduguard disable'.
    """
    texto = running or ""
    bajo = texto.lower()
    es_switch = "switchport" in bajo or "spanning-tree mode" in bajo
    if not es_switch:
        return {"interfaces_analizadas": []}

    portfast_global = re.search(r"^spanning-tree portfast (?:edge )?default\s*$", bajo, re.MULTILINE) is not None
    bpduguard_global = re.search(r"^spanning-tree portfast (?:edge )?bpduguard default\s*$", bajo, re.MULTILINE) is not None

    resultado: List[Dict[str, Any]] = []
    for nombre, lineas in _bloques_interfaz(texto).items():
        if _RE_VIRTUAL.match(nombre):
            continue
        if "shutdown" in lineas and "no shutdown" not in lineas:
            continue
        if "no switchport" in lineas or any(l.startswith("ip address") for l in lineas):
            continue
        if "switchport mode trunk" in lineas:
            continue

        vlan = 1
        for l in lineas:
            m = re.match(r"switchport access vlan (\d+)$", l)
            if m:
                vlan = int(m.group(1))

        portfast = (
            "spanning-tree portfast disable" not in lineas
            and (any(re.match(r"spanning-tree portfast( edge)?$", l) for l in lineas) or portfast_global)
        )
        bpdu_guard = "spanning-tree bpduguard disable" not in lineas and (
            "spanning-tree bpduguard enable" in lineas or (bpduguard_global and portfast)
        )

        resultado.append({
            "interfaz": nombre,
            "vlan_acceso": vlan,
            "bpdu_guard": bool(bpdu_guard),
            "portfast": bool(portfast),
        })
    return {"interfaces_analizadas": resultado}


# ----------------------------------------------------------------------------- OSPF (IOS)
_RE_VECINO = re.compile(
    r"^(\d+\.\d+\.\d+\.\d+)\s+\d+\s+([A-Za-z0-9]+)(?:/\s*\S+)?\s+(\d{2}:\d{2}:\d{2}|-)\s+\d+\.\d+\.\d+\.\d+\s+(\S+)\s*$"
)
_RE_IF_BRIEF = re.compile(r"^(\S+)\s+\d+\s+(\S+)\s+\d+\.\d+\.\d+\.\d+/\d+\s+")


_PREFIJOS_IF = {
    "gi": "gigabitethernet", "fa": "fastethernet", "et": "ethernet", "te": "tengigabitethernet",
    "vl": "vlan", "lo": "loopback", "po": "port-channel", "se": "serial", "tu": "tunnel",
}


def _canonica(nombre: str) -> str:
    """'Gi0/1' y 'GigabitEthernet0/1' -> 'gigabitethernet0/1' (para cruzar tablas de distintos 'show')."""
    m = re.match(r"^([A-Za-z\-]+)(.*)$", nombre.strip())
    if not m:
        return nombre.strip().lower()
    prefijo, resto = m.group(1).lower(), m.group(2)
    return _PREFIJOS_IF.get(prefijo[:2], prefijo) + resto


def _segundos(hhmmss: str) -> int:
    if hhmmss == "-":
        return 0
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return h * 3600 + m * 60 + s


def parsear_vecinos_ospf_ios(salida_neighbor: str, salida_if_brief: str = "") -> Dict[str, Any]:
    """
    Convierte 'show ip ospf neighbor' (y, para el area, 'show ip ospf interface brief')
    en {"vecinos_ospf": [{vecino_id, interfaz, area, estado, tiempo_muerto_seg}]}.

    El estado se normaliza quitando el rol: 'FULL/DR' -> 'FULL', 'EXSTART/BDR' -> 'EXSTART'.
    Si el area no se puede determinar queda como '?'. Lineas que no son de vecino se ignoran.
    """
    areas: Dict[str, str] = {}
    for linea in (salida_if_brief or "").splitlines():
        m = _RE_IF_BRIEF.match(linea.strip())
        if m:
            areas[_canonica(m.group(1))] = m.group(2)

    vecinos: List[Dict[str, Any]] = []
    for linea in (salida_neighbor or "").splitlines():
        m = _RE_VECINO.match(linea.strip())
        if not m:
            continue
        vecino_id, estado, dead, interfaz = m.groups()
        vecinos.append({
            "vecino_id": vecino_id,
            "interfaz": interfaz,
            "area": areas.get(_canonica(interfaz), "?"),
            "estado": estado.upper(),
            "tiempo_muerto_seg": _segundos(dead),
        })
    return {"vecinos_ospf": vecinos}
