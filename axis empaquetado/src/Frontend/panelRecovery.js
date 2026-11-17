/* panelRecovery.js — pestana Disaster Recovery: 3 vistas (segmented control)
     Running vs Startup   diff de la ultima auditoria (cambios sin guardar)
     Historial            auditorias guardadas + detalle (+ cambios vs version anterior)
     Versiones            versiones de running-config guardadas del equipo, comparables con la anterior */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    const { el, limpiar, renderChecks, alerta, vacio, fechaLocal, ICONO } = AXIS.ui;
    let vista = "running";

    // ------------------------------------------------------------ Running vs Startup
    function pintarRunning() {
        const caja = limpiar($("rec-running"));
        const u = AXIS.auditoria.ultima();
        if (!u) return caja.append(vacio("Ejecuta una auditoría en la pestaña Estado para comparar running-config y startup-config.",
            { texto: "Ir a Estado", onclick: () => AXIS.tabs.activar("auditoria") }));
        const cfg = u.datos.configs;
        if (!cfg || !cfg.diff) return caja.append(vacio("El diff running vs startup no se guarda en el historial. Ejecuta una nueva auditoría para verlo.",
            { texto: "Ir a Estado", onclick: () => AXIS.tabs.activar("auditoria") }));
        const visor = el("div");
        caja.append(el("p", { class: "meta", text: u.datos.hostname + " · " + u.ip }), visor);
        AXIS.diffViewer.render(visor, cfg);
    }

    // ------------------------------------------------------------ Historial
    async function pintarHistorial() {
        const caja = limpiar($("rec-historial"));
        const lista = el("div"), detalle = el("div", { class: "oculto" });
        const filtro = el("input", { type: "checkbox", id: "historial-solo-equipo" });
        caja.append(el("div", { class: "fila-controles" },
            el("label", { class: "check" }, filtro, " Solo este equipo"),
            el("button", { class: "btn-secundario", type: "button", onclick: cargar, text: "↻ Actualizar" })), lista, detalle);
        filtro.addEventListener("change", cargar);
        filtro.checked = AXIS.sesion.tieneIp();

        async function cargar() {
            detalle.classList.add("oculto"); lista.classList.remove("oculto");
            limpiar(lista).append(el("p", { class: "vacio", text: "Cargando..." }));
            try {
                const ip = AXIS.sesion.obtener().ip;
                const filas = await AXIS.api("/api/historial?limite=30" + (filtro.checked && AXIS.sesion.tieneIp() ? "&ip=" + encodeURIComponent(ip) : ""));
                if (!filas.length) return limpiar(lista).append(vacio("Aún no hay auditorías guardadas."));
                limpiar(lista).append(...filas.map((f) => el("button", { class: "tarjeta estado-" + f.estado_general, type: "button", onclick: () => abrir(f.id) },
                    el("span", { class: "icono", text: ICONO[f.estado_general] || ICONO.desconocido }),
                    el("span", { class: "tarjeta-texto" },
                        el("strong", { text: f.hostname + " · " + f.ip }),
                        el("small", { text: fechaLocal(f.timestamp) + (f.score_seguridad != null ? " · Score L2 " + f.score_seguridad : "") +
                            (f.config_version != null ? " · Config v" + f.config_version : "") })))));
            } catch (error) { alerta(lista, error, cargar); }
        }

        async function abrir(id) {
            lista.classList.add("oculto"); detalle.classList.remove("oculto");
            limpiar(detalle).append(el("button", { class: "btn-secundario", type: "button", onclick: cargar, text: "← Volver al historial" }));
            try {
                const r = await AXIS.api("/api/historial/" + id);
                const ul = el("ul", { class: "reglas" });
                renderChecks(ul, r.resultado.checks || []);
                detalle.append(el("h3", { text: r.hostname + " · " + r.ip }),
                    el("p", { class: "meta", text: fechaLocal(r.timestamp) + " · Estado " + r.estado_general +
                        (r.config_version != null ? " · Config v" + r.config_version + " (" + String(r.config_sha256).slice(0, 8) + ")" : "") }), ul);
                if (r.config_id != null && r.config_version > 1) {
                    const salida = el("div", { class: "diff" });
                    detalle.append(el("button", { class: "btn-secundario", type: "button", text: "Ver cambios vs versión anterior",
                        onclick: () => compararConAnterior(r.ip, r.config_id, r.config_version, salida) }), salida);
                }
            } catch (error) { alerta(detalle, error, () => abrir(id)); }
        }

        cargar();
    }

    /** Diff de una version de config contra la inmediatamente anterior del mismo equipo. */
    async function compararConAnterior(ip, configId, version, salida) {
        limpiar(salida).append(el("p", { class: "vacio", text: "Calculando diferencias..." }));
        try {
            const versiones = await AXIS.api("/api/configs/versiones?ip=" + encodeURIComponent(ip));
            const previa = versiones.find((v) => v.version === version - 1);
            if (!previa) return limpiar(salida).append(vacio("La versión anterior ya no está guardada."));
            AXIS.diffViewer.render(salida, await AXIS.api("/api/configs/diff?base=" + previa.id + "&nueva=" + configId));
        } catch (error) { alerta(salida, error, () => compararConAnterior(ip, configId, version, salida)); }
    }

    // ------------------------------------------------------------ Versiones
    async function pintarVersiones() {
        const caja = limpiar($("rec-versiones"));
        if (!AXIS.sesion.tieneIp()) return caja.append(vacio("Indique la IP del equipo para ver sus versiones de configuración.",
            { texto: "🔑 Configurar equipo", onclick: () => AXIS.modalCred.abrir() }));
        const ip = AXIS.sesion.obtener().ip;
        caja.append(el("p", { class: "meta", text: "Versiones guardadas de " + ip }));
        const lista = el("div"), salida = el("div", { class: "diff" });
        caja.append(lista, salida);
        try {
            const versiones = await AXIS.api("/api/configs/versiones?ip=" + encodeURIComponent(ip));
            if (!versiones.length) return lista.append(vacio("Este equipo aún no tiene configuraciones guardadas. Ejecuta una auditoría."));
            lista.append(...versiones.map((v, i) => el("div", { class: "tarjeta estado-" + (i === 0 ? "verde" : "desconocido") },
                el("span", { class: "tarjeta-texto" },
                    el("strong", { text: "v" + v.version + (i === 0 ? " (actual)" : "") + " · " + String(v.sha256).slice(0, 8) }),
                    el("small", { text: fechaLocal(v.timestamp) + " · " + v.lineas + " líneas" })),
                v.version > 1
                    ? el("button", { class: "btn-secundario", type: "button", text: "Comparar con v" + (v.version - 1),
                        onclick: () => compararConAnterior(ip, v.id, v.version, salida) })
                    : el("small", { class: "meta", text: "Primera versión" }))));
        } catch (error) { alerta(lista, error, pintarVersiones); }
    }

    // ------------------------------------------------------------ vistas
    function mostrarVista(nueva) {
        vista = nueva;
        document.querySelectorAll(".seg-btn").forEach((b) => {
            const activo = b.dataset.vista === nueva;
            b.classList.toggle("activo", activo);
            b.setAttribute("aria-pressed", String(activo));
        });
        ["running", "historial", "versiones"].forEach((v) => { $("rec-" + v).hidden = v !== nueva; });
        ({ running: pintarRunning, historial: pintarHistorial, versiones: pintarVersiones })[nueva]();
    }

    function iniciar() {
        document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => mostrarVista(b.dataset.vista)));
        AXIS.tabs.registrar("recovery", { alMostrar: () => mostrarVista(vista) });
        AXIS.bus.on("auditoria", () => { if (!$("tab-recovery").hidden && vista === "running") pintarRunning(); });
    }

    AXIS.panelRecovery = { iniciar };
})();
