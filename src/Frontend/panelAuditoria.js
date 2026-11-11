/* panelAuditoria.js — pestana "Estado": ejecuta el ciclo de auditoria y muestra el semaforo tricolor. */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    const { setSemaforo, renderChecks, limpiar } = AXIS.ui;

    const TITULO = {
        verde: (h) => "✅ " + h + " Seguro y Operativo",
        amarillo: (h) => "⚠️ " + h + " Requiere Atención",
        rojo: (h) => "❌ " + h + " Vulnerabilidades Detectadas",
    };

    function resumen(datos) {
        const partes = [];
        if (datos.seguridad && datos.seguridad.score != null) partes.push("Score L2: " + datos.seguridad.score + "/100");
        if (datos.respaldo && datos.respaldo.version != null)
            partes.push("Config v" + datos.respaldo.version + (datos.respaldo.nuevo ? " (nueva)" : " (sin cambios)"));
        if (datos.auditoria_id != null) partes.push("Auditoría #" + datos.auditoria_id);
        return partes.join(" · ");
    }

    async function ejecutar() {
        if (!AXIS.requerirEquipo()) return;
        const s = AXIS.sesion.obtener();
        const semaforo = $("semaforo"), boton = $("btn-auditar"), consola = $("consola-detalles");

        setSemaforo(semaforo, "procesando", "Conectando por SSH a " + s.ip + "...");
        $("resumen-auditoria").textContent = "";
        boton.disabled = true;
        consola.classList.add("oculto");
        limpiar($("lista-reglas"));

        try {
            const datos = await AXIS.api(AXIS.config.ENDPOINT_AUDITAR, {
                metodo: "POST", timeoutMs: AXIS.config.TIMEOUT_AUDITORIA_MS,
                cuerpo: { ip: s.ip, usuario: s.usuario, password: s.password, driver_name: s.driver },
            });
            const estado = TITULO[datos.estado_general] ? datos.estado_general : "rojo";
            setSemaforo(semaforo, estado, TITULO[estado](datos.hostname));
            $("resumen-auditoria").textContent = resumen(datos);
            if (datos.checks && datos.checks.length) {
                renderChecks($("lista-reglas"), datos.checks);
                consola.classList.remove("oculto");
            }
            AXIS.auditoria.publicar(s.ip, datos, "nueva");   // alimenta Seguridad, OSPF y Recovery
        } catch (error) {
            setSemaforo(semaforo, "rojo", "⚠️ " + error.message + (error.estado ? " (HTTP " + error.estado + ")" : ""));
            console.error("Error al auditar:", error);
        } finally {
            boton.disabled = false;
        }
    }

    function iniciar() { $("btn-auditar").addEventListener("click", ejecutar); }

    AXIS.panelAuditoria = { iniciar };
})();
