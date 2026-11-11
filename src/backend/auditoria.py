"""
auditoria.py
Orquestador del CICLO DE AUDITORIA de A.X.I.S. (multi-evaluacion en UNA sola sesion SSH).

Flujo:
  1. Abre UNA sesion (conexion.conectar) y extrae todo lo necesario de una vez:
     facts, interfaces, running+startup sanitizadas y, si hay OSPF, 2 'show'.
  2. Cierra la sesion (el equipo ya no se vuelve a tocar).
  3. Evalua OFFLINE con tus evaluadores: firmware, uptime, puertos mareados,
     score L2 (VLAN 1 / PortFast sin BPDU Guard), adyacencias OSPF y running vs startup.
  4. Guarda la config sanitizada (versionada, con sha256) y el resultado, vinculados por IP.
  5. Registra la IP en la telemetria continua.

Contrato de salida: incluye 'hostname', 'estado_general' y 'checks' (igual que /api/auditar),
asi el dashboard actual funciona sin cambios; el resto son bloques adicionales.

Resiliencia: si el almacenamiento falla, la auditoria igual se devuelve (con un check
amarillo que lo avisa). Si el equipo no responde, se propaga el error de conexion.py.
Modo mock: AXIS_MODO=mock usa mock_equipo.json en lugar de un equipo real.
"""

from contextlib import AbstractContextManager
from typing import Any, Callable, Dict, List, Optional

import almacenamiento
import telemetria
from evaluador import calcular_score_seguridad, comparar_configs, evaluar_dispositivo, evaluar_ospf
from extraccion_config import ErrorExtraccionConfig, obtener_configs
from extractores import extraer_interfaces_l2, parsear_vecinos_ospf_ios
from mock_dispositivo import modo_mock

AbrirSesion = Callable[[str, str, str, str], AbstractContextManager]
_PESO = {"verde": 0, "amarillo": 1, "rojo": 2}
_COMANDOS_OSPF = ["show ip ospf neighbor", "show ip ospf interface brief"]


class ErrorAuditoria(Exception):
    """Falla del ciclo. 'mensaje_usuario' es apto para la pantalla tactil."""

    def __init__(self, mensaje_usuario: str, detalle_tecnico: str = ""):
        self.mensaje_usuario = mensaje_usuario
        self.detalle_tecnico = detalle_tecnico
        super().__init__(mensaje_usuario)


def _sesion_por_defecto(ip: str, usuario: str, password: str, driver_name: str) -> AbstractContextManager:
    if modo_mock():
        from mock_dispositivo import sesion_mock
        return sesion_mock()
    from conexion import conectar  # import diferido: permite probar sin NAPALM instalado

    return conectar(ip, usuario, password, driver_name)


def _peor_estado(checks: List[Dict[str, Any]]) -> str:
    peor = max((_PESO.get(c["estado"], 2) for c in checks), default=0)
    return next(e for e, p in _PESO.items() if p == peor)


def _check(nombre: str, estado: str, mensaje: str, detalle: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"check": nombre, "estado": estado, "mensaje": mensaje, "detalle": detalle or []}


def ejecutar_ciclo_auditoria(
    ip: str,
    usuario: str,
    password: str,
    driver_name: str = "ios",
    abrir_sesion: Optional[AbrirSesion] = None,
    guardar: bool = True,
) -> Dict[str, Any]:
    abrir = abrir_sesion or _sesion_por_defecto
    es_ios = driver_name == "ios"

    # ------------------------------------------------ 1) UNA sesion, toda la extraccion
    with abrir(ip, usuario, password, driver_name) as dispositivo:
        try:
            facts = dispositivo.get_facts()
            interfaces = dispositivo.get_interfaces()
        except Exception as error:
            raise ErrorAuditoria(
                "Se logro conectar, pero fallo la extraccion de datos del equipo.", detalle_tecnico=str(error)
            )
        try:
            from conexion import extraer_configuraciones  # envuelve extraccion_config con ErrorConexion
            configs = extraer_configuraciones(dispositivo)
        except ImportError:  # sin NAPALM (pruebas offline): se usa el extractor directo
            try:
                configs = obtener_configs(dispositivo)
            except ErrorExtraccionConfig as error:
                raise ErrorAuditoria(error.mensaje_usuario, error.detalle_tecnico)

        salida_ospf: Optional[Dict[str, str]] = None
        error_ospf: Optional[str] = None
        if es_ios and "router ospf" in configs["running"].lower():
            try:
                salida_ospf = dispositivo.cli(_COMANDOS_OSPF)
            except Exception as error:
                error_ospf = str(error)

    # ------------------------------------------------ 2) evaluacion OFFLINE
    hostname = facts.get("hostname") or "desconocido"
    base = evaluar_dispositivo({**facts, "interfaces": interfaces})
    checks: List[Dict[str, Any]] = [dict(c) for c in base["checks"]]

    # Seguridad L2 (VLAN 1 alta / PortFast sin BPDU Guard media): datos derivados de la running
    seguridad: Optional[Dict[str, Any]] = None
    l2 = extraer_interfaces_l2(configs["running"]) if es_ios else {"interfaces_analizadas": []}
    if l2["interfaces_analizadas"]:
        seguridad = calcular_score_seguridad({"hostname": hostname, **l2})
        n = len(seguridad["hallazgos"])
        checks.append(_check(
            "seguridad_l2", seguridad["estado_color"],
            f"Score de seguridad L2: {seguridad['score']}/100 ({n} hallazgo(s)).",
            [h["mensaje"] for h in seguridad["hallazgos"]],
        ))
    else:
        checks.append(_check("seguridad_l2", "verde", "No aplica: el equipo no tiene puertos de acceso L2 activos."))

    # OSPF (FULL = sano)
    ospf: Optional[Dict[str, Any]] = None
    if error_ospf is not None:
        checks.append(_check("ospf", "amarillo", "No se pudo consultar el estado OSPF del equipo.", [error_ospf]))
    elif salida_ospf is not None:
        vecinos = parsear_vecinos_ospf_ios(
            salida_ospf.get(_COMANDOS_OSPF[0], ""), salida_ospf.get(_COMANDOS_OSPF[1], "")
        )
        ospf = evaluar_ospf({"hostname": hostname, **vecinos})
        checks.append(_check(
            "ospf", ospf["estado"], ospf["mensaje"],
            [v["mensaje"] for v in ospf["vecinos"] if not v["es_sano"]],
        ))
    else:
        checks.append(_check("ospf", "verde", "No aplica: OSPF no esta configurado en este equipo."))

    # Disaster Recovery: running vs startup
    comparacion = comparar_configs(configs["running"], configs["startup"])
    if not configs["startup"].strip():
        checks.append(_check("config_sin_guardar", "amarillo",
                             "El equipo no tiene startup-config guardada (falta 'write memory')."))
    elif comparacion["hay_cambios_sin_guardar"]:
        cambios = [f"{'+' if l['tipo'] == 'agregada' else '-'} {l['linea'].strip()}"
                   for l in comparacion["diff"] if l["tipo"] != "igual"][:10]
        checks.append(_check("config_sin_guardar", "amarillo", comparacion["mensaje"], cambios))
    else:
        checks.append(_check("config_sin_guardar", "verde", comparacion["mensaje"]))

    resultado: Dict[str, Any] = {
        "hostname": hostname,
        "ip": ip,
        "estado_general": _peor_estado(checks),
        "checks": checks,
        "seguridad": seguridad,
        "ospf": ospf,
        "configs": comparacion,
        "respaldo": {},
        "auditoria_id": None,
        "timestamp": None,
    }

    # ------------------------------------------------ 3) almacenamiento versionado + vinculo
    if guardar:
        try:
            cfg = almacenamiento.guardar_config(ip, hostname, configs["running"], configs["sha256"], configs["lineas"])
            resultado["respaldo"] = cfg
            # El diff completo no se persiste en el historial (la config ya esta versionada aparte)
            persistible = {**resultado, "configs": {k: v for k, v in comparacion.items() if k != "diff"}}
            aud = almacenamiento.guardar_auditoria(ip, hostname, persistible, cfg["config_id"], cfg["sha256"])
            resultado["auditoria_id"], resultado["timestamp"] = aud["auditoria_id"], aud["timestamp"]
        except almacenamiento.ErrorAlmacenamiento as error:
            resultado["respaldo"] = {"error": error.mensaje_usuario}
            checks.append(_check("almacenamiento", "amarillo",
                                 "La auditoria se completo, pero no se pudo guardar en el historial local.",
                                 [error.mensaje_usuario]))
            resultado["estado_general"] = _peor_estado(checks)

    # ------------------------------------------------ 4) telemetria continua
    try:
        telemetria.monitor.agregar_objetivo(ip)
    except ValueError:
        pass  # tope de equipos monitoreados: no es motivo para fallar la auditoria

    return resultado
