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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from evaluador import evaluar_dispositivo

app = FastAPI(
    title="M.E.O.W. - Motor NetOps",
    description="API local del appliance NetOps portatil (proyecto de titulo).",
    version="0.1.0-esqueleto",
)

# Ruta al mock. En Fase 2 esto se reemplaza por una conexion real via
# NAPALM (ver conexion.py), pero el contrato de datos (el diccionario
# de facts) se mantiene identico para no tener que tocar evaluador.py.
RUTA_MOCK = Path(__file__).parent / "mock_facts.json"

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
