"""
reglas_puertos.py
Regla de auditoria de puertos "mareados" para A.X.I.S.

Cruza dos banderas que NAPALM entrega en get_interfaces():
  - is_enabled: estado ADMINISTRATIVO (lo que dice la config: shutdown / no shutdown)
  - is_up:      estado OPERATIVO (si hay enlace fisico/logico)

Combinaciones:
  enabled=True  / up=True   -> sano
  enabled=False / up=False  -> apagado a proposito (buena practica en puertos sin uso)
  enabled=True  / up=False  -> MAREADO: habilitado pero sin enlace (cable caido, extremo
                               apagado, err-disable, o puerto sin uso que quedo abierto)
  enabled=False / up=True   -> INCONSISTENTE: apagado por config pero con enlace; no
                               deberia pasar, indica dato corrupto o driver mal interpretado

Opcionalmente detecta puertos INESTABLES con last_flapped (segundos desde el ultimo
cambio de estado; NAPALM usa -1 si el driver no lo informa).

Solo trabaja con diccionarios: no depende de FastAPI ni de NAPALM.
"""

import re
from typing import Any, Dict, List

# Interfaces virtuales que siempre estan "up" o no representan un puerto fisico.
_VIRTUALES = re.compile(
    r"^(lo(opback)?\d*|null\d*|vlan\d+|tunnel\d+|bvi\d+|vi\d+)$", re.IGNORECASE
)

# Un puerto habilitado y sin enlace es un hallazgo de higiene, no una falla critica.
# Si el semaforo del frontend solo maneja verde/rojo, cambia esta constante a "rojo".
ESTADO_ADVERTENCIA = "amarillo"

UMBRAL_FLAP_SEGUNDOS = 5 * 60  # up pero cambio de estado hace menos de 5 minutos


def _es_fisica(nombre: str) -> bool:
    return not _VIRTUALES.match(nombre.strip())


def evaluar_puertos_mareados(
    datos: Dict[str, Any],
    umbral_flap_segundos: int = UMBRAL_FLAP_SEGUNDOS,
) -> Dict[str, Any]:
    """
    Evalua la coherencia is_enabled / is_up de las interfaces fisicas.

    Espera datos["interfaces"] con la forma de NAPALM get_interfaces():
        {"Gi0/0": {"is_enabled": True, "is_up": False, "last_flapped": 120.0, ...}, ...}
    """
    interfaces = datos.get("interfaces")

    if not isinstance(interfaces, dict) or not interfaces:
        return {
            "check": "puertos_mareados",
            "estado": "rojo",
            "mensaje": "No se pudo leer el estado de las interfaces (campo 'interfaces' vacio o ausente).",
            "detalle": [],
        }

    habilitados_sin_enlace: List[str] = []
    inconsistentes: List[str] = []
    inestables: List[str] = []
    fisicas = 0

    for nombre, info in sorted(interfaces.items()):
        if not _es_fisica(nombre) or not isinstance(info, dict):
            continue
        fisicas += 1

        enabled = bool(info.get("is_enabled"))
        up = bool(info.get("is_up"))
        last_flapped = info.get("last_flapped", -1)

        if enabled and not up:
            habilitados_sin_enlace.append(nombre)
        elif up and not enabled:
            inconsistentes.append(nombre)
        elif enabled and up:
            # -1 (o ausente) significa "el driver no lo informa": no se puede afirmar nada.
            if isinstance(last_flapped, (int, float)) and 0 <= last_flapped < umbral_flap_segundos:
                inestables.append(f"{nombre} (cambio de estado hace {int(last_flapped)} s)")

    detalle: List[str] = []
    detalle += [f"{p}: habilitado (no shutdown) pero sin enlace" for p in habilitados_sin_enlace]
    detalle += [f"{p}: apagado por configuracion pero reporta enlace activo" for p in inconsistentes]
    detalle += [f"{p}: enlace inestable" for p in inestables]

    if fisicas == 0:
        return {
            "check": "puertos_mareados",
            "estado": "rojo",
            "mensaje": "El equipo no reporto interfaces fisicas evaluables.",
            "detalle": [],
        }

    if inconsistentes:
        estado = "rojo"
        mensaje = (
            f"{len(inconsistentes)} puerto(s) con estado inconsistente (apagados pero con enlace). "
            "Revisar manualmente: el dato podria estar corrupto."
        )
    elif habilitados_sin_enlace or inestables:
        estado = ESTADO_ADVERTENCIA
        partes = []
        if habilitados_sin_enlace:
            partes.append(
                f"{len(habilitados_sin_enlace)} puerto(s) habilitado(s) sin enlace "
                "(cierra con 'shutdown' los que no uses)"
            )
        if inestables:
            partes.append(f"{len(inestables)} puerto(s) con enlace inestable")
        mensaje = "; ".join(partes) + "."
    else:
        estado = "verde"
        mensaje = f"{fisicas} puerto(s) fisico(s) con estado coherente."

    return {
        "check": "puertos_mareados",
        "estado": estado,
        "mensaje": mensaje,
        "detalle": detalle,
    }
