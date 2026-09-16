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

from contextlib import contextmanager
from typing import Any, Dict, Iterator

from napalm import get_network_driver
from napalm.base.base import NetworkDriver
from napalm.base.exceptions import (
    ConnectAuthError,
    ConnectionException,
    ConnectTimeoutError,
)
from netmiko.exceptions import NetmikoAuthenticationException


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


if __name__ == "__main__":
    # No ejecutar en el bus: requiere un equipo real o GNS3 corriendo.
    try:
        datos = obtener_facts("192.168.1.1", "admin", "password", driver_name="ios")
        print(datos)
    except ErrorConexion as error:
        print(f"[Lo que veria el usuario en pantalla]: {error.mensaje_usuario}")