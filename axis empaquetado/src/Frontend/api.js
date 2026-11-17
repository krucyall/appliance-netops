/* api.js — cliente HTTP unico: timeout, errores en lenguaje de usuario y sin excepciones crudas en pantalla.
   ErrorApi.estado: 0 = sin red / timeout; >0 = codigo HTTP devuelto por el motor. */
(function () {
    window.AXIS = window.AXIS || {};

    class ErrorApi extends Error {
        constructor(mensaje, estado) { super(mensaje); this.estado = estado; }
    }

    function mensajeDe(datos, estado) {
        const d = datos && datos.detail;
        if (typeof d === "string") return d;
        if (Array.isArray(d) && d.length) {   // 422 de validacion de FastAPI
            return "Revise los datos ingresados: " + d.map((e) => {
                const campo = (e.loc || []).slice(-1)[0];
                return campo + ": " + String(e.msg).replace(/^Value error, /, "");
            }).join("; ");
        }
        return "El motor respondió con un error inesperado.";
    }

    async function api(ruta, opciones) {
        const { metodo = "GET", cuerpo, timeoutMs = AXIS.config.TIMEOUT_MS } = opciones || {};
        const control = new AbortController();
        const reloj = setTimeout(() => control.abort(), timeoutMs);
        try {
            const respuesta = await fetch(AXIS.config.API_BASE + ruta, {
                method: metodo,
                headers: cuerpo ? { "Content-Type": "application/json" } : undefined,
                body: cuerpo ? JSON.stringify(cuerpo) : undefined,
                signal: control.signal,
            });
            if (respuesta.status === 204) return null;
            let datos = null;
            try { datos = await respuesta.json(); } catch (_) { /* cuerpo no JSON */ }
            if (!respuesta.ok) throw new ErrorApi(mensajeDe(datos, respuesta.status), respuesta.status);
            return datos;
        } catch (error) {
            if (error instanceof ErrorApi) throw error;
            if (error.name === "AbortError") throw new ErrorApi("El motor tardó demasiado en responder.", 0);
            throw new ErrorApi("No hay comunicación con el motor A.X.I.S. Verifique que esté encendido.", 0);
        } finally {
            clearTimeout(reloj);
        }
    }

    AXIS.api = api;
    AXIS.ErrorApi = ErrorApi;
})();
