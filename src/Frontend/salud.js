/* salud.js — vigila que el motor responda (GET /api/estado) y avisa con un banner si se cae.
   Tambien muestra el modo del motor ("mock" = datos de prueba) para no confundirlos con datos reales.
   Se recupera solo cuando el motor vuelve; no consulta mientras la pantalla esta oculta. */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    let enLinea = null;
    let modoActual = null;

    function marcar(ok, modo) {
        if (ok === enLinea && modo === modoActual) return;
        const cambioConexion = ok !== enLinea;
        enLinea = ok;
        modoActual = modo;
        const chip = $("chip-motor");
        chip.className = "chip-motor " + (ok ? (modo === "mock" ? "mock" : "ok") : "caido");
        chip.textContent = !ok ? "● Sin motor" : modo === "mock" ? "● Motor · modo prueba" : "● Motor";
        chip.title = !ok ? "El motor no responde"
            : modo === "mock" ? "Modo prueba: los resultados vienen de datos simulados, no de equipos reales"
            : "Motor conectado a equipos reales";
        $("banner-offline").classList.toggle("oculto", ok);
        if (cambioConexion) AXIS.bus.emit("motor", ok);
    }

    async function comprobar() {
        try {
            const estado = await AXIS.api("/api/estado", { timeoutMs: 4000 });
            marcar(true, estado && estado.modo);
        } catch (_) { marcar(false, null); }
    }

    function iniciar() {
        $("btn-reintentar-motor").addEventListener("click", comprobar);
        comprobar();
        setInterval(() => { if (!document.hidden) comprobar(); }, AXIS.config.SALUD_MS);
    }

    AXIS.salud = { iniciar, comprobar };
})();
