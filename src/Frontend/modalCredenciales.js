/* modalCredenciales.js — modal para ingresar IP, usuario y contrasena EN TIEMPO DE EJECUCION.
   Los valores se guardan solo en memoria (AXIS.sesion): se pierden al recargar la pagina.
   El selector de equipos se llena con el inventario del laboratorio (GET /api/dispositivos);
   si el motor no responde se usan los atajos de AXIS.config.EQUIPOS_RAPIDOS. */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    const { el, limpiar } = AXIS.ui;
    let equipos = AXIS.config.EQUIPOS_RAPIDOS;   // [{nombre, ip, driver?}]

    const buscar = (ip) => equipos.find((e) => e.ip === ip);

    function pintarEquipos() {
        const rapidos = $("mc-equipo");
        const elegido = rapidos.value;
        limpiar(rapidos);
        rapidos.append(el("option", { value: "", text: "Otra IP (escribirla abajo)" }),
            ...equipos.map((e) => el("option", { value: e.ip, text: e.nombre + " (" + e.ip + ")" })));
        rapidos.value = buscar(elegido) ? elegido : "";
    }

    async function cargarInventario() {
        try {
            const inventario = await AXIS.api("/api/dispositivos");
            if (Array.isArray(inventario) && inventario.length) {
                equipos = inventario.map((d) => ({ nombre: d.nombre, ip: d.ip, driver: d.driver }));
                pintarEquipos();
            }
        } catch (_) { /* sin motor: se mantienen los atajos de config.js */ }
    }

    function abrir(motivo) {
        const s = AXIS.sesion.obtener();
        $("mc-ip").value = s.ip; $("mc-usuario").value = s.usuario;
        $("mc-password").value = s.password; $("mc-driver").value = s.driver;
        $("mc-password").type = "password";
        $("mc-equipo").value = (buscar(s.ip) || {}).ip || "";
        $("mc-error").textContent = motivo || "";
        $("modal-cred").classList.remove("oculto");
        $("btn-sesion").setAttribute("aria-expanded", "true");
        (s.ip ? $("mc-usuario") : $("mc-ip")).focus();
    }

    function cerrar() {
        $("modal-cred").classList.add("oculto");
        $("btn-sesion").setAttribute("aria-expanded", "false");
    }

    function guardar(evento) {
        evento.preventDefault();
        const ip = $("mc-ip").value.trim();
        if (!AXIS.validarIPv4(ip)) { $("mc-error").textContent = "⚠️ Ingrese una IPv4 válida (ej: 192.168.1.20)."; return; }
        AXIS.sesion.guardar({ ip, usuario: $("mc-usuario").value, password: $("mc-password").value, driver: $("mc-driver").value });
        cerrar();
    }

    function pintarChip() {
        const s = AXIS.sesion.obtener();
        $("btn-sesion").querySelector(".chip-texto").textContent = s.ip ? s.ip + (s.usuario ? " · " + s.usuario : "") : "Configurar equipo";
    }

    function iniciar() {
        const rapidos = $("mc-equipo");
        pintarEquipos();
        cargarInventario();
        rapidos.addEventListener("change", () => {
            const equipo = buscar(rapidos.value);
            if (!equipo) return;
            $("mc-ip").value = equipo.ip;
            if (equipo.driver && $("mc-driver").querySelector('option[value="' + equipo.driver + '"]')) {
                $("mc-driver").value = equipo.driver;
            }
        });
        $("mc-ip").addEventListener("input", () => {
            rapidos.value = (buscar($("mc-ip").value.trim()) || {}).ip || "";
        });
        $("btn-sesion").addEventListener("click", () => abrir());
        $("form-cred").addEventListener("submit", guardar);
        $("mc-cancelar").addEventListener("click", cerrar);
        $("mc-borrar").addEventListener("click", () => {
            AXIS.sesion.borrarCredenciales();
            $("mc-usuario").value = ""; $("mc-password").value = "";
            $("mc-error").textContent = "Credenciales borradas de la memoria.";
        });
        $("mc-ver").addEventListener("click", () => {
            $("mc-password").type = $("mc-password").type === "password" ? "text" : "password";
        });
        $("modal-cred").addEventListener("click", (e) => { if (e.target === $("modal-cred")) cerrar(); });
        document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal-cred").classList.contains("oculto")) cerrar(); });
        AXIS.bus.on("sesion", pintarChip);
        pintarChip();
    }

    AXIS.modalCred = { iniciar, abrir, cerrar };
})();
