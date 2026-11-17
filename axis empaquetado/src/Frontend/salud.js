/* salud.js — vigila que el motor responda (GET /api/estado) y avisa con un banner si se cae.
   Se recupera solo cuando el motor vuelve. */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    let enLinea = null;

    function marcar(ok) {
        if (ok === enLinea) return;
        enLinea = ok;
        $("chip-motor").className = "chip-motor " + (ok ? "ok" : "caido");
        $("chip-motor").textContent = ok ? "● Motor" : "● Sin motor";
        $("banner-offline").classList.toggle("oculto", ok);
        AXIS.bus.emit("motor", ok);
    }

    async function comprobar() {
        try { await AXIS.api("/api/estado", { timeoutMs: 4000 }); marcar(true); }
        catch (_) { marcar(false); }
    }

    function iniciar() {
        $("btn-reintentar-motor").addEventListener("click", comprobar);
        comprobar();
        // Sin 'document.hidden': tambien es el latido que le dice al motor que el navegador sigue abierto
        // (con AXIS_AUTOCIERRE_MIN el motor se apaga solo si deja de recibir peticiones). Es 1 peticion
        // minima cada 10 s; el navegador la espacia solo cuando la pestana esta en segundo plano.
        setInterval(comprobar, AXIS.config.SALUD_MS);
    }

    AXIS.salud = { iniciar, comprobar };
})();
