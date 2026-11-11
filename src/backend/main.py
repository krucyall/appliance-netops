"""
main.py
Esqueleto de la API REST del proyecto M.E.O.W. (FastAPI).

<<<<<<< HEAD
En esta fase (offline / bus) el endpoint /api/auditar NO se conecta a
ningun equipo real: lee un archivo mock_facts.json local y lo pasa por
el motor de evaluacion (evaluador.py). Esto permite desarrollar y
probar toda la logica de la API sin depender de GNS3 ni de internet.

Correr localmente:
    uvicorn main:app --reload
=======
El modo de /api/auditar se elige con la variable de entorno MEOW_MODO:
    - "mock" (por defecto): lee mock_facts.json, sin conectarse a nada.
      Sirve para desarrollar offline (bus) sin GNS3 ni internet.
    - "real": se conecta por SSH al equipo via NAPALM (conexion.py)
      con las credenciales que envia el Frontend.
No hay respaldo automatico: en modo real, si el equipo no responde se
muestra el error, nunca datos mock haciendose pasar por reales.

Correr localmente (desde src/backend):
    uvicorn main:app --reload                  # modo mock
    MEOW_MODO=real uvicorn main:app --reload   # modo real (bash/Linux)
    $env:MEOW_MODO="real"; uvicorn main:app --reload   # PowerShell
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1
Luego abrir en el navegador: http://localhost:8000/docs
(la interfaz de pruebas de FastAPI se genera sola, sin internet)
"""

import json
<<<<<<< HEAD
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import telemetria
from api_auditoria import router as router_auditoria
from api_telemetria import router as router_telemetria
=======
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from conexion import ErrorConexion, obtener_facts, verificar_conectividad_tcp
from extractores import obtener_configs, obtener_interfaces_l2, obtener_vecinos_ospf

MODOS_VALIDOS = ("mock", "real")
MODO = os.environ.get("MEOW_MODO", "mock").strip().lower()
if MODO not in MODOS_VALIDOS:
    raise RuntimeError(
        f"MEOW_MODO='{MODO}' no es valido. Use uno de: {', '.join(MODOS_VALIDOS)}."
    )

logger = logging.getLogger("meow")

>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1
from evaluador import (
    evaluar_dispositivo,
    evaluar_ospf,
    calcular_score_seguridad,
    comparar_configs,
)
<<<<<<< HEAD
from mtu_stress import router as router_mtu
=======
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1

RUTA_MOCK_OSPF = Path(__file__).parent / "mock_ospf.json"
RUTA_MOCK_SEGURIDAD = Path(__file__).parent / "mock_seguridad.json"
RUTA_MOCK_CONFIGS = Path(__file__).parent / "mock_configs.json"
<<<<<<< HEAD
=======
# Inventario de equipos del laboratorio. Solo datos fijos (IP, driver,
# ubicacion): version, uptime, etc. se obtienen en vivo via NAPALM.
RUTA_DISPOSITIVOS = Path(__file__).parent / "dispositivos_lab.json"
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1

def _cargar_json_o_404(ruta: Path) -> dict:
    """Helper compartido: carga un mock JSON o lanza 404 con mensaje claro."""
    if not ruta.exists():
        raise HTTPException(status_code=404, detail=f"No se encontro el archivo mock en {ruta}.")
    with open(ruta, "r", encoding="utf-8") as archivo:
        return json.load(archivo)

<<<<<<< HEAD
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

=======
app = FastAPI(
    title="M.E.O.W. - Motor NetOps",
    description="API local del appliance NetOps portatil (proyecto de titulo).",
    version="0.1.0-esqueleto",
)

>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1
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
<<<<<<< HEAD
=======
    modo: str  # "mock" | "real"
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1


class HallazgoSeguridad(BaseModel):
    interfaz: str
    tipo: str
    severidad: str
    mensaje: str


class ResultadoSeguridad(BaseModel):
    hostname: str
    score: int
    estado_color: str
<<<<<<< HEAD
    hallazgos: List[HallazgoSeguridad]
=======
    cantidad_interfaces: int  # Puertos de acceso analizados (0 = nada que evaluar)
    hallazgos: List[HallazgoSeguridad]
    modo: str  # "mock" | "real"
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1


class LineaDiff(BaseModel):
    tipo: str
    linea: str


class ResultadoComparacion(BaseModel):
    hay_cambios_sin_guardar: bool
    cantidad_diferencias: int
    mensaje: str
    diff: List[LineaDiff]
<<<<<<< HEAD


@app.get("/api/ospf", response_model=ResultadoOspf)
def auditar_ospf():
    """[Modulo L3] Adyacencias OSPF. Hoy lee mock_ospf.json; a futuro, conexion.conectar()."""
    datos = _cargar_json_o_404(RUTA_MOCK_OSPF)
    return evaluar_ospf(datos)


@app.get("/api/seguridad", response_model=ResultadoSeguridad)
def auditar_seguridad():
    """[Modulo L2] Score de Seguridad. Hoy lee mock_seguridad.json."""
    datos = _cargar_json_o_404(RUTA_MOCK_SEGURIDAD)
    return calcular_score_seguridad(datos)


@app.get("/api/configs/comparar", response_model=ResultadoComparacion)
def comparar_configuraciones():
    """[Modulo Disaster Recovery] Diff running vs startup. Hoy lee mock_configs.json."""
    datos = _cargar_json_o_404(RUTA_MOCK_CONFIGS)
    return comparar_configs(datos["running_config"], datos["startup_config"])

class CredencialesDispositivo(BaseModel):
    """Contrato de los datos que el Frontend debe enviar para auditar un equipo."""
    ip: str
    usuario: str
    password: str
    driver_name: str = "ios"  # Valor por defecto para equipos Cisco

=======
    modo: str  # "mock" | "real"


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


def _datos_del_equipo(
    credenciales: CredencialesDispositivo,
    ruta_mock: Path,
    obtener: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Punto unico de decision mock/real para todos los modulos de auditoria.

    - Modo mock: carga `ruta_mock`.
    - Modo real: llama a `obtener(ip, usuario, password, driver_name=...)`
      (obtener_facts, obtener_vecinos_ospf, ...). Responde 400 si faltan
      credenciales y 502 con un 'detail' en espanol si falla la conexion.
    """
    if MODO == "mock":
        return _cargar_json_o_404(ruta_mock)

    if not credenciales.usuario or not credenciales.password:
        raise HTTPException(
            status_code=400,
            detail="Ingrese usuario y contrasena SSH del equipo.",
        )
    try:
        return obtener(
            credenciales.ip,
            credenciales.usuario,
            credenciales.password,
            driver_name=credenciales.driver_name,
        )
    except ErrorConexion as error:
        # El detalle tecnico va al log del servidor, no a la pantalla.
        logger.warning(
            "Fallo %s en %s: %s | %s",
            obtener.__name__,
            credenciales.ip,
            error.mensaje_usuario,
            error.detalle_tecnico,
        )
        raise HTTPException(status_code=502, detail=error.mensaje_usuario)


@app.post("/api/ospf", response_model=ResultadoOspf)
def auditar_ospf(credenciales: CredencialesDispositivo):
    """[Modulo L3] Adyacencias OSPF (`show ip ospf neighbor detail`, solo Cisco IOS)."""
    datos = _datos_del_equipo(credenciales, RUTA_MOCK_OSPF, obtener_vecinos_ospf)
    return {**evaluar_ospf(datos), "modo": MODO}


@app.post("/api/seguridad", response_model=ResultadoSeguridad)
def auditar_seguridad(credenciales: CredencialesDispositivo):
    """[Modulo L2] Score de Seguridad de los puertos de acceso (running-config, solo Cisco IOS)."""
    datos = _datos_del_equipo(credenciales, RUTA_MOCK_SEGURIDAD, obtener_interfaces_l2)
    return {**calcular_score_seguridad(datos), "modo": MODO}


@app.post("/api/configs/comparar", response_model=ResultadoComparacion)
def comparar_configuraciones(credenciales: CredencialesDispositivo):
    """[Modulo Disaster Recovery] Diff running vs startup (contrasenas ocultas como '<removed>')."""
    datos = _datos_del_equipo(credenciales, RUTA_MOCK_CONFIGS, obtener_configs)
    return {**comparar_configs(datos["running_config"], datos["startup_config"]), "modo": MODO}

>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1
class ResultadoCheck(BaseModel):
    """Contrato de un check individual, para que Frontend sepa que esperar."""

    check: str
<<<<<<< HEAD
    estado: str  # "verde" | "amarillo" | "rojo"
    mensaje: str
    detalle: List[str] = []  # opcional: una linea por puerto/hallazgo (p. ej. puertos mareados)
=======
    estado: str  # "verde" | "rojo"
    mensaje: str
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1


class ResultadoAuditoria(BaseModel):
    """
    Contrato de respuesta de /api/auditar.

    Definir esto temprano (aunque el body de la peticion aun sea vacio)
    permite que el companero de Frontend/UX empiece a maquetar el
    dashboard de semaforos sin esperar a que el backend este 100% listo.
    """

    hostname: str
<<<<<<< HEAD
    estado_general: str  # "verde" | "amarillo" | "rojo"
    checks: List[ResultadoCheck]
=======
    estado_general: str  # "verde" | "rojo"
    checks: List[ResultadoCheck]
    modo: str  # "mock" | "real": el Frontend avisa si son datos de prueba


class Dispositivo(BaseModel):
    """Contrato de un equipo del inventario (dispositivos_lab.json)."""

    id: str
    nombre: str
    ip: str
    rol: str
    driver: str  # Nombre del driver NAPALM: "ios", "eos", "junos", "vyos"...
    modelo: str
    ubicacion: str
    puerto_ssh: int = 22


class ResultadoPing(BaseModel):
    """Contrato de respuesta de /api/ping (prueba TCP de 1-toque)."""

    alcanzable: bool
    ip: str
    puerto: int
    latencia_ms: float
    mensaje: str
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1


@app.get("/api/estado")
def estado_motor():
<<<<<<< HEAD
    """Endpoint de salud: confirma que el motor esta corriendo."""
    return {"estado": "ok", "mensaje": "Motor M.E.O.W. operativo."}
=======
    """Endpoint de salud: confirma que el motor esta corriendo y en que modo."""
    return {"estado": "ok", "mensaje": "Motor M.E.O.W. operativo.", "modo": MODO}


@app.get("/api/dispositivos", response_model=List[Dispositivo])
def listar_dispositivos():
    """Lista los equipos del laboratorio para el selector del Frontend."""
    return _cargar_json_o_404(RUTA_DISPOSITIVOS)


@app.get("/api/ping", response_model=ResultadoPing)
def ping_dispositivo(
    ip: str,
    puerto: int = Query(22, ge=1, le=65535),
    timeout: float = Query(2.0, gt=0, le=10),
):
    """
    Prueba rapida de conectividad TCP (por defecto SSH, puerto 22).

    Sirve para que la pantalla tactil confirme que el equipo responde
    antes de lanzar una auditoria completa. Siempre responde 200: si el
    equipo no es alcanzable, se indica con alcanzable=False y un mensaje.
    Ejemplo: /api/ping?ip=192.168.1.1
    """
    return verificar_conectividad_tcp(ip, puerto, timeout)
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1


@app.post("/api/auditar", response_model=ResultadoAuditoria)
def auditar_dispositivo(credenciales: CredencialesDispositivo):
    """
<<<<<<< HEAD
    Ejecuta una auditoria.
    En Fase 1: Ignora las credenciales y usa datos mock.
    En Fase 2: Usará credenciales.ip y credenciales.usuario con conexion.py.
    """
    if not RUTA_MOCK.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No se encontro el archivo mock en {RUTA_MOCK}. "
            "Crea mock_facts.json en esta misma carpeta.",
        )

    with open(RUTA_MOCK, "r", encoding="utf-8") as archivo:
        datos_dispositivo = json.load(archivo)

    resultado = evaluar_dispositivo(datos_dispositivo)
    return resultado
=======
    Ejecuta una auditoria de firmware + uptime.

    - Modo mock: ignora las credenciales y usa mock_facts.json.
    - Modo real: se conecta por SSH (NAPALM) con las credenciales
      recibidas. Errores: ver `_datos_del_equipo()`.
    """
    datos_dispositivo = _datos_del_equipo(credenciales, RUTA_MOCK, obtener_facts)
    return {**evaluar_dispositivo(datos_dispositivo), "modo": MODO}
>>>>>>> 8370e2233e48bb6eab451f6923399fc8785509c1

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)