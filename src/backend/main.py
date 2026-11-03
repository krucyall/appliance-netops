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
from pathlib import Path
from typing import List

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from conexion import verificar_conectividad_tcp
from evaluador import evaluar_dispositivo

from evaluador import (
    evaluar_dispositivo,
    evaluar_ospf,
    calcular_score_seguridad,
    comparar_configs,
)

RUTA_MOCK_OSPF = Path(__file__).parent / "mock_ospf.json"
RUTA_MOCK_SEGURIDAD = Path(__file__).parent / "mock_seguridad.json"
RUTA_MOCK_CONFIGS = Path(__file__).parent / "mock_configs.json"
# Inventario de equipos del laboratorio. Solo datos fijos (IP, driver,
# ubicacion): version, uptime, etc. se obtienen en vivo via NAPALM.
RUTA_DISPOSITIVOS = Path(__file__).parent / "dispositivos_lab.json"

def _cargar_json_o_404(ruta: Path) -> dict:
    """Helper compartido: carga un mock JSON o lanza 404 con mensaje claro."""
    if not ruta.exists():
        raise HTTPException(status_code=404, detail=f"No se encontro el archivo mock en {ruta}.")
    with open(ruta, "r", encoding="utf-8") as archivo:
        return json.load(archivo)

app = FastAPI(
    title="M.E.O.W. - Motor NetOps",
    description="API local del appliance NetOps portatil (proyecto de titulo).",
    version="0.1.0-esqueleto",
)

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
    estado: str  # "verde" | "rojo"
    mensaje: str


class ResultadoAuditoria(BaseModel):
    """
    Contrato de respuesta de /api/auditar.

    Definir esto temprano (aunque el body de la peticion aun sea vacio)
    permite que el companero de Frontend/UX empiece a maquetar el
    dashboard de semaforos sin esperar a que el backend este 100% listo.
    """

    hostname: str
    estado_general: str  # "verde" | "rojo"
    checks: List[ResultadoCheck]


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


@app.get("/api/estado")
def estado_motor():
    """Endpoint de salud: confirma que el motor esta corriendo."""
    return {"estado": "ok", "mensaje": "Motor M.E.O.W. operativo."}


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