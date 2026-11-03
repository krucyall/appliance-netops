"""
conexion.py
Wrapper de conexion a dispositivos de red via NAPALM.

Esta version NO se puede probar en el bus (requiere GNS3 corriendo o un
equipo real), asi que el foco de este archivo es el manejo de errores:
cada excepcion de NAPALM (o de Netmiko, la libreria que NAPALM usa por
debajo) se traduce a un mensaje claro en espanol que el Frontend pueda
mostrar directamente en la pantalla tactil, sin traceback tecnico de
por medio.

`conectar()` es el primitivo de bajo nivel: abre UNA sesion SSH y la
deja disponible para ejecutar tantos get_* como se necesiten (por
ejemplo, adyacencias OSPF + running-config + startup-config en una
sola conexion, en vez de tres handshakes SSH separados).
`obtener_facts()` es un atajo de alto nivel construido sobre `conectar()`
para el caso de uso mas simple (un solo get_facts + interfaces).
"""

import socket
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator

# NAPALM (y Netmiko, que viene con NAPALM) son opcionales al importar:
# asi main.py puede arrancar en el bus sin NAPALM instalado y servir los
# endpoints mock y /api/ping. Solo `conectar()` / `obtener_facts()`
# lo necesitan, y avisan con ErrorConexion si falta.
try:
    from napalm import get_network_driver
    from napalm.base.base import NetworkDriver
    from napalm.base.exceptions import (
        ConnectAuthError,
        ConnectionException,
        ConnectTimeoutError,
    )
    from netmiko.exceptions import NetmikoAuthenticationException

    NAPALM_DISPONIBLE = True
except ImportError:
    NetworkDriver = Any  # Solo para los type hints
    NAPALM_DISPONIBLE = False


class ErrorConexion(Exception):
    """
    Excepcion propia del proyecto.

    Desacopla los errores internos de NAPALM/Netmiko (tecnicos, en
    ingles, a veces con tracebacks largos) del mensaje final que se le
    muestra al usuario en la pantalla tactil (mensaje_usuario).
    """

    def __init__(self, mensaje_usuario: str, detalle_tecnico: str = ""):
        self.mensaje_usuario = mensaje_usuario
        self.detalle_tecnico = detalle_tecnico
        super().__init__(mensaje_usuario)


def _abrir_con_manejo_de_errores(dispositivo: NetworkDriver) -> None:
    """
    Abre la sesion de un driver NAPALM ya instanciado, traduciendo
    cualquier excepcion de bajo nivel a ErrorConexion.

    Factorizado aparte para que `conectar()` y `obtener_facts()`
    compartan exactamente la misma logica de traduccion de errores
    (una sola fuente de verdad para "que le mostramos al usuario").
    """
    try:
        dispositivo.open()
    except ConnectAuthError:
        raise ErrorConexion(
            "Error: Usuario o contrasena incorrectos. Verifique las credenciales."
        )
    except NetmikoAuthenticationException as error:
        # Defensa en profundidad: en algunos drivers/versiones la
        # excepcion nativa de Netmiko se filtra sin que NAPALM la
        # traduzca a ConnectAuthError. La capturamos para no romper
        # el contrato "nunca dejar escapar una excepcion cruda".
        raise ErrorConexion(
            "Error: Usuario o contrasena incorrectos. Verifique las credenciales.",
            detalle_tecnico=str(error),
        )
    except ConnectTimeoutError:
        raise ErrorConexion(
            "Error: No se pudo conectar al equipo (tiempo de espera agotado). "
            "Verifique el cable, la IP o que el equipo este encendido."
        )
    except ConnectionException as error:
        # Cubre casos como SSH deshabilitado en el equipo o puerto cerrado/rechazado.
        # Debe ir DESPUES de ConnectAuthError/ConnectTimeoutError: ambas son
        # subclases de ConnectionException y serian capturadas aqui primero
        # si este except estuviera antes.
        raise ErrorConexion(
            "Error: No se pudo establecer conexion SSH. Verifique que el "
            "servicio SSH este habilitado en el equipo.",
            detalle_tecnico=str(error),
        )
    except Exception as error:
        # Red de seguridad: un error no anticipado no debe crashear la
        # API ni mostrar un traceback crudo en la pantalla tactil.
        raise ErrorConexion(
            "Error inesperado al intentar conectar con el equipo.",
            detalle_tecnico=str(error),
        )


@contextmanager
def conectar(
    ip: str,
    usuario: str,
    password: str,
    driver_name: str = "ios",
    timeout: int = 10,
) -> Iterator[NetworkDriver]:
    """
    Context manager de bajo nivel para una sesion NAPALM.

    Uso:
        with conectar(ip, usuario, password) as dispositivo:
            configs = dispositivo.get_config()
            ospf = dispositivo.get_bgp_neighbors()  # ejemplo

    Garantiza el cierre de la sesion al salir del bloque `with`, haya
    o no error. Lanza ErrorConexion ante cualquier falla de apertura.
    """
    if not NAPALM_DISPONIBLE:
        raise ErrorConexion(
            "Error: El appliance no tiene instalada la libreria de conexion (NAPALM).",
            detalle_tecnico="Instalar con: pip install -r requirements.txt",
        )

    driver_class = get_network_driver(driver_name)
    dispositivo = driver_class(
        hostname=ip,
        username=usuario,
        password=password,
        timeout=timeout,
    )
    _abrir_con_manejo_de_errores(dispositivo)
    try:
        yield dispositivo
    finally:
        dispositivo.close()


def obtener_facts(
    ip: str,
    usuario: str,
    password: str,
    driver_name: str = "ios",
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Se conecta a un equipo via NAPALM, extrae get_facts() + interfaces,
    y cierra la conexion. Pensado para ser llamado desde /api/auditar
    en reemplazo de la lectura de mock_facts.json.

    Implementado sobre `conectar()` para no duplicar la logica de
    apertura/cierre/traduccion de errores.
    """
    with conectar(ip, usuario, password, driver_name, timeout) as dispositivo:
        try:
            facts = dispositivo.get_facts()
            facts["interfaces"] = dispositivo.get_interfaces()
            return facts
        except Exception as error:
            raise ErrorConexion(
                "Se logro conectar, pero fallo la extraccion de datos del equipo.",
                detalle_tecnico=str(error),
            )


def verificar_conectividad_tcp(
    ip: str,
    puerto: int = 22,
    timeout: float = 2.0,
) -> Dict[str, Any]:
    """
    Verifica si un puerto TCP (por defecto SSH, 22) esta abierto y accesible.

    Pensado como prueba rapida de 1-toque antes de abrir la sesion NAPALM
    completa (que tarda varios segundos en fallar si el equipo no responde).
    No lanza excepciones: siempre retorna un dict con 'alcanzable' y un
    'mensaje' listo para mostrar en la pantalla tactil.
    """
    inicio = time.perf_counter()

    def _resultado(alcanzable: bool, mensaje: str) -> Dict[str, Any]:
        return {
            "alcanzable": alcanzable,
            "ip": ip,
            "puerto": puerto,
            "latencia_ms": round((time.perf_counter() - inicio) * 1000, 2),
            "mensaje": mensaje,
        }

    try:
        # create_connection + `with` garantiza que el socket se cierre
        # siempre, incluso si la conexion falla.
        with socket.create_connection((ip, puerto), timeout=timeout):
            pass
    except socket.timeout:
        return _resultado(
            False,
            f"Tiempo de espera agotado al conectar con {ip}:{puerto}. "
            "Verifique el cable, la IP o que el equipo este encendido.",
        )
    except ConnectionRefusedError:
        return _resultado(
            False,
            f"El equipo {ip} respondio, pero el puerto {puerto} esta cerrado.",
        )
    except OSError as error:
        # Cubre IP invalida, red inalcanzable, nombre no resuelto, etc.
        return _resultado(False, f"Error de red al sondear {ip}: {error}")

    resultado = _resultado(True, "")
    resultado["mensaje"] = (
        f"Puerto {puerto} abierto y accesible ({resultado['latencia_ms']} ms)."
    )
    return resultado


if __name__ == "__main__":
    # No ejecutar en el bus: requiere un equipo real o GNS3 corriendo.
    try:
        datos = obtener_facts("192.168.1.1", "admin", "password", driver_name="ios")
        print(datos)
    except ErrorConexion as error:
        print(f"[Lo que veria el usuario en pantalla]: {error.mensaje_usuario}")