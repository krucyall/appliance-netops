/* config.js — configuracion central del frontend (si ya tienes uno, fusiona estas claves). */
window.AXIS = window.AXIS || {};
AXIS.config = {
    API_BASE: "http://127.0.0.1:8000",
    // "/api/auditoria/completa": ciclo real (1 sesion SSH) + historial. "/api/auditar" = modo Fase 1.
    ENDPOINT_AUDITAR: "/api/auditoria/completa",
    TIMEOUT_MS: 15000,
    TIMEOUT_AUDITORIA_MS: 90000,   // el ciclo SSH completo puede tardar
    TIMEOUT_MTU_MS: 120000,        // varios tamanos x repeticiones
    SALUD_MS: 10000,               // cada cuanto se comprueba que el motor responde
    TELEMETRIA_REFRESCO_MS: 5000,
    // Atajos del selector de equipo en el modal de credenciales (la IP siempre se puede escribir a mano).
    EQUIPOS_RAPIDOS: [
        { nombre: "Router Core", ip: "192.168.1.10" },
        { nombre: "Switch Distribución", ip: "192.168.1.20" },
        { nombre: "Router Vulnerable GNS3", ip: "192.168.1.30" },
    ],
};
