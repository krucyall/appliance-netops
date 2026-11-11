"""
main.py
Esqueleto de la API REST del proyecto M.E.O.W. (FastAPI).

En esta fase (offline / bus) el endpoint /api/auditar NO se conecta a
ningun equipo real: lee un archivo mock_facts.json local y lo pasa por
el motor de evaluacion (evaluador.py). Esto permite desarrollar y
probar toda la logica de la API sin depender de GNS3 ni de internet.

Correr localmente:
    uvicorn main:app --reload
Luego abrir en el navegador: http://localhost:8000/docs
(la interfaz de pruebas de FastAPI se genera sola, sin internet)
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

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
from mtu_stress import router as router_mtu

RUTA_MOCK_OSPF = Path(__file__).parent / "mock_ospf.json"
RUTA_MOCK_SEGURIDAD = Path(__file__).parent / "mock_seguridad.json"
RUTA_MOCK_CONFIGS = Path(__file__).parent / "mock_configs.json"

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


class LineaDiff(BaseModel):
    tipo: str
    linea: str


class ResultadoComparacion(BaseModel):
    hay_cambios_sin_guardar: bool
    cantidad_diferencias: int
    mensaje: str
    diff: List[LineaDiff]


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


@app.get("/api/estado")
def estado_motor():
    """Endpoint de salud: confirma que el motor esta corriendo."""
    return {"estado": "ok", "mensaje": "Motor M.E.O.W. operativo."}


@app.post("/api/auditar", response_model=ResultadoAuditoria)
def auditar_dispositivo(credenciales: CredencialesDispositivo):
    """
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)