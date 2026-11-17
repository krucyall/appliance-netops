/* estado.js — estado compartido en MEMORIA (nada se escribe en localStorage/cookies/disco):
     AXIS.sesion    IP, usuario, contrasena y plataforma del equipo objetivo.
     AXIS.auditoria ultima auditoria (la consumen Seguridad, OSPF y Recovery).
     AXIS.bus       pub/sub minimo para que los paneles reaccionen a cambios. */
(function () {
    window.AXIS = window.AXIS || {};
    const oyentes = {};
    AXIS.bus = {
        on(evento, fn) { (oyentes[evento] = oyentes[evento] || []).push(fn); },
        emit(evento, datos) {
            (oyentes[evento] || []).forEach((fn) => { try { fn(datos); } catch (e) { console.error(e); } });
        },
    };

    const sesion = { ip: "", usuario: "", password: "", driver: "ios" };
    AXIS.sesion = {
        obtener: () => Object.assign({}, sesion),
        tieneIp: () => AXIS.validarIPv4(sesion.ip),
        guardar(parcial) { Object.assign(sesion, parcial); AXIS.bus.emit("sesion", Object.assign({}, sesion)); },
        borrarCredenciales() { sesion.usuario = ""; sesion.password = ""; AXIS.bus.emit("sesion", Object.assign({}, sesion)); },
    };

    let ultima = null;
    AXIS.auditoria = {
        ultima: () => ultima,
        publicar(ip, datos, origen) {
            ultima = { ip, datos, origen };   // origen: "nueva" | "historial"
            AXIS.bus.emit("auditoria", ultima);
        },
        /** Recupera del historial la ultima auditoria guardada del equipo (sin el diff running/startup). */
        async cargarUltimaGuardada(ip) {
            const filas = await AXIS.api("/api/historial?limite=1&ip=" + encodeURIComponent(ip));
            if (!filas.length) return null;
            const r = await AXIS.api("/api/historial/" + filas[0].id);
            this.publicar(ip, Object.assign({}, r.resultado, { auditoria_id: r.auditoria_id, timestamp: r.timestamp }), "historial");
            return r;
        },
    };

    /** Estado vacio comun a Seguridad/OSPF: ir a Estado o cargar la ultima auditoria guardada del equipo. */
    AXIS.sinAuditoria = (destino) => {
        const { el, vacio, alerta } = AXIS.ui;
        const mensaje = el("div");
        destino.append(
            vacio("Aún no hay una auditoría en esta sesión. Ejecútala en la pestaña Estado o carga la última guardada del equipo.",
                { texto: "Ir a Estado", onclick: () => AXIS.tabs.activar("auditoria") }),
            el("button", { class: "btn-secundario", type: "button", text: "Cargar la última guardada",
                onclick: async () => {
                    if (!AXIS.requerirEquipo()) return;
                    try {
                        const r = await AXIS.auditoria.cargarUltimaGuardada(AXIS.sesion.obtener().ip);
                        if (!r) mensaje.textContent = "Este equipo aún no tiene auditorías guardadas.";
                    } catch (error) { alerta(mensaje, error); }
                } }),
            mensaje);
    };

    /** Si no hay IP configurada abre el modal y devuelve false. */
    AXIS.requerirEquipo = () => {
        if (AXIS.sesion.tieneIp()) return true;
        AXIS.modalCred.abrir("Primero indique la IP del equipo.");
        return false;
    };
})();
