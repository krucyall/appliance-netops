/* tabs.js — navegacion por pestanas tactil (evita el scroll vertical largo). */
(function () {
    window.AXIS = window.AXIS || {};
    const paneles = {};

    function activar(id) {
        document.querySelectorAll(".panel").forEach((p) => {
            const activo = p.id === "tab-" + id;
            p.hidden = !activo;
            p.classList.toggle("activo", activo);
        });
        document.querySelectorAll(".tab-btn").forEach((b) => {
            const activo = b.dataset.tab === id;
            b.classList.toggle("activo", activo);
            b.setAttribute("aria-selected", String(activo));
        });
        for (const [clave, gancho] of Object.entries(paneles)) {
            if (clave !== id && gancho.alOcultar) gancho.alOcultar();
        }
        if (paneles[id] && paneles[id].alMostrar) paneles[id].alMostrar();
        const main = document.querySelector("main");
        if (main) main.scrollTop = 0;
        try { sessionStorage.setItem("axis_tab", id); } catch (_) { /* modo privado/kiosco */ }
    }

    function iniciar() {
        document.querySelectorAll(".tab-btn").forEach((b) => b.addEventListener("click", () => activar(b.dataset.tab)));
        let guardada = null;
        try { guardada = sessionStorage.getItem("axis_tab"); } catch (_) { /* ignorar */ }
        activar(guardada && document.getElementById("tab-" + guardada) ? guardada : "auditoria");
    }

    AXIS.tabs = { registrar: (id, ganchos) => { paneles[id] = ganchos; }, activar, iniciar };
})();
