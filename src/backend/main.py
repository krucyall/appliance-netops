"""
main.py
Esqueleto de la API REST del proyecto M.E.O.W. (FastAPI).

El modo se elige con la variable de entorno AXIS_MODO (mock_dispositivo.py):
    - "mock": /api/auditar, /api/ospf, /api/seguridad y /api/configs/comparar
      leen los Super Mocks (mock_*.json), sin conectarse a nada. Sirve para
      desarrollar, capacitar y probar offline sin GNS3 ni internet.
    - "real" (por defecto): se conectan por SSH al equipo via NAPALM
      (conexion.py) con las credenciales que envia el Frontend.
No hay respaldo automatico: en modo real, si el equipo no responde se
muestra el error, nunca datos mock haciendose pasar por reales.
GET /api/estado informa el modo activo para que la UI lo muestre.

Correr localmente (desde src/backend):
    AXIS_MODO=mock uvicorn main:app --reload             # bash/Linux
    $env:AXIS_MODO="mock"; uvicorn main:app --reload     # PowerShell
Luego abrir en el navegador: http://localhost:8000/docs
(la interfaz de pruebas de FastAPI se genera sola, sin internet)
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import telemetria
from api_auditoria import router as router_auditoria
from api_telemetria import router as router_telemetria
from evaluador import (
    evaluar_dispositivo,
    evaluar_ospf,
    calcular_score_seguridad,
    comparar_configs,
)
from extractores import extraer_interfaces_l2, parsear_vecinos_ospf_ios
from mock_dispositivo import modo_mock
from mtu_stress import router as router_mtu

logger = logging.getLogger("axis")

RUTA_MOCK_OSPF = Path(__file__).parent / "mock_ospf.json"
RUTA_MOCK_SEGURIDAD = Path(__file__).parent / "mock_seguridad.json"
RUTA_MOCK_CONFIGS = Path(__file__).parent / "mock_configs.json"
# Inventario de equipos del laboratorio. Solo datos fijos (IP, driver,
# ubicacion): version, uptime, etc. se obtienen en vivo via NAPALM.
RUTA_DISPOSITIVOS = Path(__file__).parent / "dispositivos_lab.json"
_COMANDOS_OSPF = ["show ip ospf neighbor", "show ip ospf interface brief"]


def _modo() -> str:
    """Modo activo ("mock" | "real"). Se lee en cada peticion (AXIS_MODO)."""
    return "mock" if modo_mock() else "real"

def _cargar_json_o_404(ruta: Path) -> dict:
    """Helper compartido: carga un mock JSON o lanza 404 con mensaje claro."""
    if not ruta.exists():
        raise HTTPException(status_code=404, detail=f"No se encontro el archivo mock en {ruta}.")
    with open(ruta, "r", encoding="utf-8") as archivo:
        return json.load(archivo)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Arranca la telemetria continua (tarea de fondo) con la API y la detiene al apagar."""
    telemetria.monitor.iniciar()
    try:
        yield
    finally:
        await telemetria.monitor.detener()


app = FastAPI(
    title="A.X.I.S. - Motor NetOps",
    description="API local del appliance NetOps portatil (proyecto de titulo).",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(router_mtu)         # POST /api/mtu-stress
app.include_router(router_auditoria)   # POST /api/auditoria/completa, GET /api/historial, /api/configs/versiones|diff
app.include_router(router_telemetria)  # GET/POST/DELETE /api/telemetria...

# Ruta al mock. En Fase 2 esto se reemplaza por una conexion real via
# NAPALM (ver conexion.py), pero el contrato de datos (el diccionario
# de facts) se mantiene identico para no tener que tocar evaluador.py.
RUTA_MOCK = Path(__file__).parent / "mock_facts.json"

class VecinoOspf(BaseModel):
    vecino_id: str
    interfaz: str
    area: str
    estado: str
    es_sano: bool
    mensaje: str
    tiempo_muerto_seg: int


class ResultadoOspf(BaseModel):
    check: str
    estado: str
    mensaje: str
    vecinos: List[VecinoOspf]
    modo: str  # "mock" | "real"


class HallazgoSeguridad(BaseModel):
    interfaz: str
    tipo: str
    severidad: str
    mensaje: str


class ResultadoSeguridad(BaseModel):
    hostname: str
    score: int
    estado_color: str
    hallazgos: List[HallazgoSeguridad]
    modo: str  # "mock" | "real"


class LineaDiff(BaseModel):
    tipo: str
    linea: str


class ResultadoComparacion(BaseModel):
    hay_cambios_sin_guardar: bool
    cantidad_diferencias: int
    mensaje: str
    diff: List[LineaDiff]
    modo: str  # "mock" | "real"


class ResultadoCheck(BaseModel):
    """Contrato de un check individual, para que Frontend sepa que esperar."""

    check: str
    estado: str  # "verde" | "amarillo" | "rojo"
    mensaje: str
    detalle: List[str] = []  # opcional: una linea por puerto/hallazgo (p. ej. puertos mareados)


class ResultadoAuditoria(BaseModel):
    """
    Contrato de respuesta de /api/auditar.

    Definir esto temprano (aunque el body de la peticion aun sea vacio)
    permite que el companero de Frontend/UX empiece a maquetar el
    dashboard de semaforos sin esperar a que el backend este 100% listo.
    """

    hostname: str
    estado_general: str  # "verde" | "amarillo" | "rojo"
    checks: List[ResultadoCheck]
    modo: str  # "mock" | "real": el Frontend avisa si son datos de prueba


class CredencialesDispositivo(BaseModel):
    """
    Contrato de los datos que el Frontend debe enviar para auditar un equipo.

    usuario/password los escribe el tecnico en la pantalla tactil. Solo
    son obligatorios en modo real (en modo mock se ignoran). Nunca se
    guardan ni se escriben en el log.
    """
    ip: str
    usuario: str = ""
    password: str = ""
    driver_name: str = "ios"  # Valor por defecto para equipos Cisco


class Dispositivo(BaseModel):
    """Contrato de un equipo del inventario (dispositivos_lab.json)."""

    id: str
    nombre: str
    ip: str
    rol: str
    driver: str  # Nombre del driver NAPALM: "ios", "vyos"...
    modelo: str
    ubicacion: str
    puerto_ssh: int = 22


def _datos_del_equipo(
    credenciales: CredencialesDispositivo,
    ruta_mock: Path,
    extraer: Callable[[Any], Dict[str, Any]],
    solo_ios: bool = False,
) -> Dict[str, Any]:
    """
    Punto unico de decision mock/real para los modulos de auditoria individuales.

    - Modo mock: carga `ruta_mock`.
    - Modo real: abre UNA sesion (conexion.conectar) y llama a `extraer(dispositivo)`.
      Responde 400 si faltan credenciales o el driver no aplica, y 502 con un
      'detail' en espanol si falla la conexion o la extraccion.
    """
    if modo_mock():
        return _cargar_json_o_404(ruta_mock)

    if not credenciales.usuario or not credenciales.password:
        raise HTTPException(status_code=400, detail="Ingrese usuario y contrasena SSH del equipo.")
    if solo_ios and credenciales.driver_name != "ios":
        raise HTTPException(status_code=400, detail="Este modulo solo esta disponible para equipos Cisco IOS.")

    # Import diferido: permite importar la API (y correr las pruebas) sin NAPALM instalado.
    from conexion import ErrorConexion, conectar

    try:
        with conectar(credenciales.ip, credenciales.usuario, credenciales.password,
                      credenciales.driver_name) as dispositivo:
            try:
                return extraer(dispositivo)
            except ErrorConexion:
                raise
            except Exception as error:
                raise ErrorConexion(
                    "Se logro conectar, pero fallo la extraccion de datos del equipo.",
                    detalle_tecnico=str(error),
                )
    except ErrorConexion as error:
        # El detalle tecnico va al log del servidor, no a la pantalla.
        logger.warning("Fallo en %s: %s | %s", credenciales.ip, error.mensaje_usuario, error.detalle_tecnico)
        raise HTTPException(status_code=502, detail=error.mensaje_usuario)


def _hostname(dispositivo: Any) -> str:
    return dispositivo.get_facts().get("hostname") or "desconocido"


def _extraer_facts(dispositivo: Any) -> Dict[str, Any]:
    facts = dispositivo.get_facts()
    facts["interfaces"] = dispositivo.get_interfaces()
    return facts


def _extraer_ospf(dispositivo: Any) -> Dict[str, Any]:
    salida = dispositivo.cli(_COMANDOS_OSPF)
    vecinos = parsear_vecinos_ospf_ios(salida.get(_COMANDOS_OSPF[0], ""), salida.get(_COMANDOS_OSPF[1], ""))
    return {"hostname": _hostname(dispositivo), **vecinos}


def _extraer_l2(dispositivo: Any) -> Dict[str, Any]:
    from conexion import extraer_configuraciones

    configs = extraer_configuraciones(dispositivo)
    return {"hostname": _hostname(dispositivo), **extraer_interfaces_l2(configs["running"])}


def _extraer_configs(dispositivo: Any) -> Dict[str, Any]:
    from conexion import extraer_configuraciones

    configs = extraer_configuraciones(dispositivo)  # sanitizadas: secretos como '<removed>'
    return {"running_config": configs["running"], "startup_config": configs["startup"]}


@app.get("/api/estado")
def estado_motor():
    """Endpoint de salud: confirma que el motor esta corriendo y en que modo."""
    return {"estado": "ok", "mensaje": "Motor M.E.O.W. operativo.", "modo": _modo()}


@app.get("/api/dispositivos", response_model=List[Dispositivo])
def listar_dispositivos():
    """Lista los equipos del laboratorio para el selector del Frontend."""
    return _cargar_json_o_404(RUTA_DISPOSITIVOS)


@app.post("/api/auditar", response_model=ResultadoAuditoria)
def auditar_dispositivo(credenciales: CredencialesDispositivo):
    """
    Auditoria rapida de firmware + uptime.

    - Modo mock: ignora las credenciales y usa mock_facts.json.
    - Modo real: se conecta por SSH (NAPALM) con las credenciales recibidas.
    """
    datos = _datos_del_equipo(credenciales, RUTA_MOCK, _extraer_facts)
    return {**evaluar_dispositivo(datos), "modo": _modo()}


@app.post("/api/ospf", response_model=ResultadoOspf)
def auditar_ospf(credenciales: CredencialesDispositivo):
    """[Modulo L3] Adyacencias OSPF (solo Cisco IOS)."""
    datos = _datos_del_equipo(credenciales, RUTA_MOCK_OSPF, _extraer_ospf, solo_ios=True)
    return {**evaluar_ospf(datos), "modo": _modo()}


@app.post("/api/seguridad", response_model=ResultadoSeguridad)
def auditar_seguridad(credenciales: CredencialesDispositivo):
    """[Modulo L2] Score de Seguridad de los puertos de acceso (running-config, solo Cisco IOS)."""
    datos = _datos_del_equipo(credenciales, RUTA_MOCK_SEGURIDAD, _extraer_l2, solo_ios=True)
    return {**calcular_score_seguridad(datos), "modo": _modo()}


@app.post("/api/configs/comparar", response_model=ResultadoComparacion)
def comparar_configuraciones(credenciales: CredencialesDispositivo):
    """[Modulo Disaster Recovery] Diff running vs startup (contrasenas ocultas como '<removed>')."""
    datos = _datos_del_equipo(credenciales, RUTA_MOCK_CONFIGS, _extraer_configs)
    return {**comparar_configs(datos["running_config"], datos["startup_config"]), "modo": _modo()}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)