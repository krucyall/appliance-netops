"""
evaluador.py
Motor de evaluacion ("el cerebro") del proyecto M.E.O.W.

Contiene la logica de negocio pura: lee los datos extraidos via NAPALM
(o un mock JSON equivalente) y decide el estado de salud del dispositivo
segun las reglas de buenas practicas definidas para el MVP.

No depende de FastAPI ni de NAPALM directamente: solo trabaja con
diccionarios de Python, por lo que es 100% testeable offline (en el bus,
sin internet ni GNS3).
"""

import re
import difflib
from typing import Any, Dict

from reglas_puertos import evaluar_puertos_mareados

# --- Configuracion de umbrales (constantes de negocio) ---
# En una fase posterior estos valores deberian venir de un JSON de
# "buenas practicas" externo (Competencia 1: Auditoria), no hardcodeados
# aqui. Por ahora sirven para dejar la logica funcionando de punta a punta.
VERSION_MINIMA_ACEPTABLE = 15.0
UPTIME_MINIMO_SEGUNDOS = 10 * 60  # 10 minutos



def _extraer_version_numerica(os_version: str) -> float:
    """
    Extrae la porcion numerica principal de un string de version de IOS.

    Los equipos Cisco suelen reportar cadenas como "15.4(3)M2" o
    "12.4(24)T". Para poder comparar, se toma solo el primer numero
    con su decimal (ej: 15.4).

    Retorna 0.0 si no se pudo interpretar el formato, para que quien
    llama decida como manejar un dato no confiable.
    """
    match = re.match(r"(\d+)\.(\d+)", os_version.strip())
    if not match:
        return 0.0
    mayor, menor = match.groups()
    return float(f"{mayor}.{menor}")


def evaluar_firmware(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evalua si la version de OS del equipo esta desactualizada.

    Espera que 'datos' tenga la forma que retorna NAPALM get_facts(),
    es decir, al menos la clave 'os_version' (str).
    """
    os_version_raw = datos.get("os_version", "")

    if not os_version_raw:
        return {
            "check": "firmware",
            "estado": "rojo",
            "mensaje": "No se pudo leer la version de firmware (campo 'os_version' vacio o ausente).",
        }

    version_numerica = _extraer_version_numerica(os_version_raw)

    if version_numerica == 0.0:
        return {
            "check": "firmware",
            "estado": "rojo",
            "mensaje": f"Formato de version no reconocido: '{os_version_raw}'. Revisar manualmente.",
        }

    if version_numerica < VERSION_MINIMA_ACEPTABLE:
        return {
            "check": "firmware",
            "estado": "rojo",
            "mensaje": (
                f"Firmware desactualizado: {os_version_raw} "
                f"(minimo aceptable: {VERSION_MINIMA_ACEPTABLE}). "
                "Se recomienda planificar actualizacion."
            ),
        }

    return {
        "check": "firmware",
        "estado": "verde",
        "mensaje": f"Firmware dentro de norma: {os_version_raw}.",
    }


def evaluar_uptime(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evalua si el equipo se reinicio recientemente.

    NAPALM entrega 'uptime' en segundos. Un uptime bajo puede indicar
    una caida no planificada, un crash o una reconfiguracion reciente
    que vale la pena revisar.
    """
    uptime_segundos = datos.get("uptime")

    if uptime_segundos is None:
        return {
            "check": "uptime",
            "estado": "rojo",
            "mensaje": "No se pudo leer el uptime del equipo (campo 'uptime' ausente).",
        }

    # NAPALM retorna -1 en algunos drivers cuando el uptime no esta disponible
    if uptime_segundos < 0:
        return {
            "check": "uptime",
            "estado": "rojo",
            "mensaje": "El equipo no reporto un uptime valido.",
        }

    if uptime_segundos < UPTIME_MINIMO_SEGUNDOS:
        minutos = uptime_segundos / 60
        return {
            "check": "uptime",
            "estado": "rojo",
            "mensaje": (
                f"El equipo se reinicio hace solo {minutos:.1f} minutos. "
                "Podria indicar una caida reciente o un cambio no documentado."
            ),
        }

    return {
        "check": "uptime",
        "estado": "verde",
        "mensaje": "El equipo tiene un tiempo de actividad estable.",
    }


def evaluar_dispositivo(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Corre todas las validaciones disponibles sobre un dispositivo y arma
    el resultado agregado que consumira el endpoint /api/auditar.

    Checks: firmware, uptime y, cuando 'datos' trae 'interfaces' (get_interfaces() de
    NAPALM), puertos mareados (is_enabled vs is_up).

    Semaforo general: 'rojo' si algun check es rojo; si no, 'amarillo' si alguno es
    amarillo (advertencia, p. ej. puertos habilitados sin enlace); si no, 'verde'.
    """
    checks = [
        evaluar_firmware(datos),
        evaluar_uptime(datos),
    ]

    if "interfaces" in datos:
        checks.append(evaluar_puertos_mareados(datos))

    if any(c["estado"] == "rojo" for c in checks):
        estado_general = "rojo"
    elif any(c["estado"] == "amarillo" for c in checks):
        estado_general = "amarillo"
    else:
        estado_general = "verde"

    return {
        "hostname": datos.get("hostname", "desconocido"),
        "estado_general": estado_general,
        "checks": checks,
    }


def evaluar_ospf(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Audita las adyacencias OSPF de un dispositivo.

    Un vecino se considera sano solo en estado 'FULL'. Cualquier otro
    estado (INIT, 2-WAY, EXSTART, EXCHANGE, LOADING) indica una
    adyacencia que no termino de converger.

    Args:
        datos: Diccionario con la forma
            {"hostname": str, "vecinos_ospf": [{"vecino_id": str,
            "interfaz": str, "area": str, "estado": str,
            "tiempo_muerto_seg": int}, ...]}.

    Returns:
        Diccionario con el estado general del check y el detalle
        enriquecido de cada vecino (agrega 'es_sano' y 'mensaje').
    """
    vecinos = datos.get("vecinos_ospf", [])

    if not vecinos:
        return {
            "check": "ospf",
            "estado": "rojo",
            "mensaje": "No se reportaron vecinos OSPF. Verifique que el proceso OSPF este activo.",
            "vecinos": [],
        }

    ESTADOS_SANOS = {"FULL"}
    detalle_vecinos = []
    hay_problemas = False

    for vecino in vecinos:
        estado_vecino = vecino.get("estado", "DESCONOCIDO")
        es_sano = estado_vecino in ESTADOS_SANOS
        hay_problemas = hay_problemas or not es_sano

        detalle_vecinos.append({
            **vecino,
            "es_sano": es_sano,
            "mensaje": (
                f"Adyacencia estable con {vecino.get('vecino_id', '?')}."
                if es_sano
                else f"Vecino {vecino.get('vecino_id', '?')} atascado en estado "
                     f"'{estado_vecino}'. Revise MTU, tipo de red o autenticacion OSPF."
            ),
        })

    return {
        "check": "ospf",
        "estado": "rojo" if hay_problemas else "verde",
        "mensaje": (
            "Una o mas adyacencias OSPF no llegaron a FULL."
            if hay_problemas
            else "Todas las adyacencias OSPF estan en estado FULL."
        ),
        "vecinos": detalle_vecinos,
    }


def calcular_score_seguridad(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcula un Score de Seguridad L2 (0-100) sobre un set de interfaces.

    Reglas actuales (ponderacion provisional, ajustar con el estandar
    CIS/NSA que esta documentando Bastian):
        - VLAN de acceso == 1               -> -15 pts (severidad alta)
        - PortFast activo sin BPDU Guard    -> -10 pts (severidad media)

    Args:
        datos: Diccionario con la forma
            {"hostname": str, "interfaces_analizadas": [{"interfaz": str,
            "vlan_acceso": int, "bpdu_guard": bool, "portfast": bool}, ...]}.

    Returns:
        Diccionario con el score final, un color de estado derivado de
        umbrales, y el detalle de cada hallazgo.
    """
    interfaces = datos.get("interfaces_analizadas", [])
    score = 100
    hallazgos = []

    for interfaz in interfaces:
        nombre = interfaz.get("interfaz", "?")

        if interfaz.get("vlan_acceso") == 1:
            score -= 15
            hallazgos.append({
                "interfaz": nombre,
                "tipo": "vlan1_en_acceso",
                "severidad": "alta",
                "mensaje": f"{nombre} usa VLAN 1 como VLAN de acceso. Reasignar a una VLAN dedicada.",
            })

        if interfaz.get("portfast") and not interfaz.get("bpdu_guard"):
            score -= 10
            hallazgos.append({
                "interfaz": nombre,
                "tipo": "sin_bpdu_guard",
                "severidad": "media",
                "mensaje": f"{nombre} tiene PortFast sin BPDU Guard. Riesgo de loop por switch no autorizado.",
            })

    score = max(score, 0)
    estado_color = "verde" if score >= 90 else "amarillo" if score >= 70 else "rojo"

    return {
        "hostname": datos.get("hostname", "desconocido"),
        "score": score,
        "estado_color": estado_color,
        "hallazgos": hallazgos,
    }


def comparar_configs(running: str, startup: str) -> Dict[str, Any]:
    """
    Compara running-config vs startup-config linea por linea y arma un
    diff estructurado listo para el Diff Viewer del Frontend.

    Args:
        running: Texto completo de la running-config.
        startup: Texto completo de la startup-config.

    Returns:
        Diccionario con un resumen (si hay cambios sin guardar) y la
        lista de lineas etiquetadas como 'igual', 'agregada' (solo en
        running: cambio hecho pero no guardado) o 'eliminada' (solo en
        startup: se perderia si alguien la reintrodujera).
    """
    lineas_startup = startup.strip().splitlines()
    lineas_running = running.strip().splitlines()

    diff = difflib.ndiff(lineas_startup, lineas_running)
    resultado_lineas = []
    cantidad_diferencias = 0

    for linea in diff:
        marca, contenido = linea[:2], linea[2:]
        if marca == "  ":
            resultado_lineas.append({"tipo": "igual", "linea": contenido})
        elif marca == "+ ":
            resultado_lineas.append({"tipo": "agregada", "linea": contenido})
            cantidad_diferencias += 1
        elif marca == "- ":
            resultado_lineas.append({"tipo": "eliminada", "linea": contenido})
            cantidad_diferencias += 1
                    # Las lineas '? ' de ndiff (marcadores intra-linea) se ignoran:
        # no aportan valor a un diff linea-por-linea.

    hay_cambios_sin_guardar = cantidad_diferencias > 0

    return {
        "hay_cambios_sin_guardar": hay_cambios_sin_guardar,
        "cantidad_diferencias": cantidad_diferencias,
        "mensaje": (
            f"Hay {cantidad_diferencias} linea(s) que difieren entre running-config y "
            "startup-config. Si el equipo se reinicia, estos cambios se perderan."
            if hay_cambios_sin_guardar
            else "La running-config coincide con la startup-config. No hay cambios sin guardar."
        ),
        "diff": resultado_lineas,
    }


if __name__ == "__main__":
    # Prueba rapida 100% offline, sin FastAPI ni NAPALM: python evaluador.py
    import json

    datos_ejemplo = {
        "hostname": "R1-LAB",
        "os_version": "12.4(24)T",
        "uptime": 300,
    }
    print(json.dumps(evaluar_dispositivo(datos_ejemplo), indent=2, ensure_ascii=False))
