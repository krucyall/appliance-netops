/* salud.js — vigila que el motor responda (GET /api/estado) y avisa con un banner si se cae.
   Se recupera solo cuando el motor vuelve; no consulta mientras la pantalla esta oculta. */
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
        setInterval(() => { if (!document.hidden) comprobar(); }, AXIS.config.SALUD_MS);
    }

    AXIS.salud = { iniciar, comprobar };
})();
