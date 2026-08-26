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
from typing import Any, Dict

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

    El estado general es 'rojo' si CUALQUIER check individual fallo
    (logica conservadora: basta una vulnerabilidad para que la mascota
    se ponga triste).
    """
    checks = [
        evaluar_firmware(datos),
        evaluar_uptime(datos),
    ]

    estado_general = "rojo" if any(c["estado"] == "rojo" for c in checks) else "verde"

    return {
        "hostname": datos.get("hostname", "desconocido"),
        "estado_general": estado_general,
        "checks": checks,
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
