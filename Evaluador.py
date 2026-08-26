"""
conexion.py
Wrapper de conexion a dispositivos de red via NAPALM.

Esta version NO se puede probar en el bus (requiere GNS3 corriendo o un
equipo real), asi que el foco de este archivo es el manejo de errores:
cada excepcion de NAPALM se traduce a un mensaje claro en espanol que
el Frontend pueda mostrar directamente en la pantalla tactil, sin
traceback tecnico de por medio.
"""

from typing import Any, Dict

from napalm import get_network_driver
from napalm.base.exceptions import (
    ConnectAuthError,
    ConnectionException,
    ConnectTimeoutError,
)


class ErrorConexion(Exception):
    """
    Excepcion propia del proyecto.

    Desacopla los errores internos de NAPALM (tecnicos, en ingles, a
    veces con tracebacks largos de Paramiko/Netmiko) del mensaje final
    que se le muestra al usuario en la pantalla tactil (mensaje_usuario).
    """

    def __init__(self, mensaje_usuario: str, detalle_tecnico: str = ""):
        self.mensaje_usuario = mensaje_usuario
        self.detalle_tecnico = detalle_tecnico
        super().__init__(mensaje_usuario)


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
    en Fase 2, en reemplazo de la lectura de mock_facts.json.

    Lanza ErrorConexion con un mensaje apto para pantalla ante cualquier
    falla: timeout, credenciales incorrectas, SSH deshabilitado, etc.
    Nunca deja escapar una excepcion cruda de NAPALM/Paramiko hacia la API.
    """
    driver = get_network_driver(driver_name)
    dispositivo = driver(
        hostname=ip,
        username=usuario,
        password=password,
        timeout=timeout,
    )

    try:
        dispositivo.open()
    except ConnectAuthError:
        raise ErrorConexion(
            "Error: Usuario o contrasena incorrectos. Verifique las credenciales."
        )
    except ConnectTimeoutError:
        raise ErrorConexion(
            "Error: No se pudo conectar al equipo (tiempo de espera agotado). "
            "Verifique el cable, la IP o que el equipo este encendido."
        )
    except ConnectionException as error:
        # Cubre casos como SSH deshabilitado en el equipo o puerto cerrado/rechazado
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

    try:
        facts = dispositivo.get_facts()
        facts["interfaces"] = dispositivo.get_interfaces()
        return facts
    except Exception as error:
        raise ErrorConexion(
            "Se logro conectar, pero fallo la extraccion de datos del equipo.",
            detalle_tecnico=str(error),
        )
    finally:
        # Cerrar siempre la sesion SSH, haya fallado o no la extraccion de datos.
        dispositivo.close()


if __name__ == "__main__":
    # No ejecutar en el bus: requiere un equipo real o GNS3 corriendo.
    # Sirve como referencia de uso cuando ya tengas red disponible.
    try:
        datos = obtener_facts("192.168.1.1", "admin", "password", driver_name="ios")
        print(datos)
    except ErrorConexion as error:
        print(f"[Lo que veria el usuario en pantalla]: {error.mensaje_usuario}")
