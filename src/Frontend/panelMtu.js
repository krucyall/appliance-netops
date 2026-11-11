/* panelMtu.js — prueba de estres MTU (POST /api/mtu-stress): pings con DF desde el equipo hacia un destino.
   Los campos del equipo (IP, usuario, contrasena, driver) comparten la sesion en memoria con el modal 🔑. */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    const { el, limpiar, setSemaforo, alerta, ICONO } = AXIS.ui;
    const RESULTADO = { ok: "✅ OK", parcial: "⚠️ Parcial", fallo: "❌ Fallo" };
    const ESTADO_SONDA = { ok: "verde", parcial: "amarillo", fallo: "rojo" };
    const TITULO = { verde: "Camino OK", amarillo: "Resultado con advertencias", rojo: "MTU no soportado" };
    let escribiendo = false;   // evita que el eco de la sesion pise lo que el usuario esta tecleando

    function desdeSesion() {
        const s = AXIS.sesion.obtener();
        $("mtu-ip").value = s.ip; $("mtu-usuario").value = s.usuario;
        $("mtu-password").value = s.password; $("mtu-driver").value = s.driver;
    }

    function haciaSesion() {
        escribiendo = true;
        AXIS.sesion.guardar({ ip: $("mtu-ip").value.trim(), usuario: $("mtu-usuario").value, password: $("mtu-password").value, driver: $("mtu-driver").value });
        escribiendo = false;
    }

    function pintarResultado(r) {
        setSemaforo($("semaforo-mtu"), r.estado, ICONO[r.estado] + " " + (TITULO[r.estado] || r.estado));
        $("mensaje-mtu").textContent = r.mensaje;
        limpiar($("kpi-mtu")).append(
            el("div", { class: "kpi-mini" }, el("small", { text: "MTU máximo exitoso" }),
                el("strong", { text: r.mtu_maximo_exitoso != null ? r.mtu_maximo_exitoso + " B" : "Ninguno" })),
            el("div", { class: "kpi-mini" }, el("small", { text: "MTU esperado" }), el("strong", { text: r.mtu_esperado + " B" })),
            el("div", { class: "kpi-mini" }, el("small", { text: "Destino" }), el("strong", { text: r.destino })));
        const tabla = limpiar($("tabla-mtu"));
        tabla.append(el("thead", {}, el("tr", {}, ["Tamaño IP", "Enviados", "Recibidos", "Pérdida", "Resultado"].map((t) => el("th", { text: t })))),
            el("tbody", {}, r.sondas.map((s) => el("tr", { class: "estado-" + ESTADO_SONDA[s.resultado] },
                el("td", { text: s.tamano_ip + " B" }), el("td", { text: String(s.enviados) }), el("td", { text: String(s.recibidos) }),
                el("td", { text: s.perdida_pct + " %" }), el("td", { text: RESULTADO[s.resultado] || s.resultado })))));
    }

    async function ejecutar() {
        const sem = $("semaforo-mtu"), boton = $("btn-mtu"), error = $("error-mtu");
        const ip = $("mtu-ip").value.trim(), destino = $("mtu-destino").value.trim(), mtu = Number($("mtu-esperado").value);
        limpiar(error);
        if (!AXIS.validarIPv4(ip)) return setSemaforo(sem, "rojo", "⚠️ Ingrese la IP del equipo");
        if (!AXIS.validarIPv4(destino)) return setSemaforo(sem, "rojo", "⚠️ Ingrese una IP de destino válida");
        if (!Number.isInteger(mtu) || mtu < 576 || mtu > 9000) return setSemaforo(sem, "rojo", "⚠️ El MTU esperado debe estar entre 576 y 9000");
        haciaSesion();

        setSemaforo(sem, "procesando", "Ejecutando pruebas de fragmentación...");
        boton.disabled = true;
        boton.textContent = "Ejecutando pruebas de fragmentación...";
        limpiar($("kpi-mtu")); limpiar($("tabla-mtu")); $("mensaje-mtu").textContent = "";
        try {
            pintarResultado(await AXIS.api("/api/mtu-stress", {
                metodo: "POST", timeoutMs: AXIS.config.TIMEOUT_MTU_MS,
                cuerpo: { ip_equipo: ip, usuario: $("mtu-usuario").value, password: $("mtu-password").value, driver: $("mtu-driver").value,
                          destino, mtu_esperado: mtu, cantidad: Number($("mtu-cantidad").value) },
            }));
        } catch (e) {
            setSemaforo(sem, "rojo", "⚠️ La prueba no se pudo completar");
            alerta(error, e, ejecutar);
        } finally {
            boton.disabled = false;
            boton.textContent = "EJECUTAR PRUEBA MTU";
        }
    }

    function iniciar() {
        document.querySelectorAll("[data-mtu]").forEach((chip) => chip.addEventListener("click", () => { $("mtu-esperado").value = chip.dataset.mtu; }));
        ["mtu-ip", "mtu-usuario", "mtu-password", "mtu-driver"].forEach((id) => $(id).addEventListener("input", haciaSesion));
        $("mtu-driver").addEventListener("change", haciaSesion);
        $("btn-mtu").addEventListener("click", ejecutar);
        AXIS.bus.on("sesion", () => { if (!escribiendo) desdeSesion(); });
        desdeSesion();
    }

    AXIS.panelMtu = { iniciar };
})();
