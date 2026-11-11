/* app.js — arranque del dashboard. Cada pestana vive en su propio modulo; aqui solo se enciende todo en orden.
   El guardia evita doble inicializacion (listeners duplicados) si el evento llegara a dispararse dos veces. */
document.addEventListener("DOMContentLoaded", () => {
    if (AXIS.iniciado) return;
    AXIS.iniciado = true;
    AXIS.modalCred.iniciar();
    AXIS.panelAuditoria.iniciar();
    AXIS.panelRecovery.iniciar();
    AXIS.scoreSeguridad.iniciar();
    AXIS.panelOspf.iniciar();
    AXIS.panelMtu.iniciar();
    AXIS.panelTelemetria.iniciar();
    AXIS.salud.iniciar();
    AXIS.tabs.iniciar();
});
