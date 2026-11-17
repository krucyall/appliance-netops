/* ui.js — utilidades de DOM compartidas. Todo texto del servidor entra por textContent (nunca innerHTML). */
(function () {
    window.AXIS = window.AXIS || {};
    const NS = "http://www.w3.org/2000/svg";
    const ICONO = { verde: "✅", amarillo: "⚠️", rojo: "❌", desconocido: "❔" };
    const RE_IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

    function agregar(nodo, hijos) {
        for (const hijo of hijos.flat()) {
            if (hijo == null || hijo === false) continue;
            nodo.append(hijo.nodeType ? hijo : document.createTextNode(String(hijo)));
        }
        return nodo;
    }

    function el(tag, props, ...hijos) {
        const nodo = document.createElement(tag);
        for (const [clave, valor] of Object.entries(props || {})) {
            if (clave === "class") nodo.className = valor;
            else if (clave === "text") nodo.textContent = valor;
            else if (clave.startsWith("on")) nodo.addEventListener(clave.slice(2), valor);
            else if (valor !== false && valor != null) nodo.setAttribute(clave, valor);
        }
        return agregar(nodo, hijos);
    }

    /** Elemento SVG nativo (sin librerias de graficos). */
    function svg(tag, atributos, ...hijos) {
        const nodo = document.createElementNS(NS, tag);
        for (const [clave, valor] of Object.entries(atributos || {})) nodo.setAttribute(clave, valor);
        return agregar(nodo, hijos);
    }

    function limpiar(nodo) { nodo.replaceChildren(); return nodo; }

    /** Semaforo tricolor: estado = apagado | procesando | verde | amarillo | rojo. */
    function setSemaforo(nodo, estado, texto) {
        nodo.className = "semaforo " + estado;
        nodo.textContent = texto;
    }

    /** Lista de checks; el 'detalle' opcional va en un desplegable tactil bajo el mensaje. */
    function renderChecks(ul, checks) {
        limpiar(ul);
        for (const c of checks) {
            const detalle = c.detalle && c.detalle.length
                ? el("details", { class: "desplegable" },
                    el("summary", { text: "Ver detalle (" + c.detalle.length + ")" }),
                    el("ul", { class: "detalle" }, c.detalle.map((d) => el("li", { text: d }))))
                : null;
            ul.append(el("li", { class: "regla estado-" + c.estado },
                el("span", { class: "icono", text: ICONO[c.estado] || ICONO.desconocido }),
                el("div", { class: "regla-cuerpo" }, el("strong", { text: c.check + ": " }), c.mensaje, detalle)));
        }
    }

    /** Alerta amigable ante error de red (estado 0) o de HTTP, con boton de reintento opcional. */
    function alerta(destino, error, reintentar) {
        const sinRed = !error || !error.estado;
        limpiar(destino).append(el("div", { class: "alerta " + (sinRed ? "alerta-red" : "alerta-http"), role: "alert" },
            el("span", { class: "icono", text: sinRed ? "🔌" : "⚠️" }),
            el("div", { class: "alerta-texto" },
                el("strong", { text: sinRed ? "Sin conexión con el motor" : "El motor devolvió un error" }),
                el("span", { text: (error && error.message) || String(error) }),
                !sinRed ? el("small", { text: "Código HTTP " + error.estado }) : null),
            reintentar ? el("button", { class: "btn-secundario", type: "button", onclick: reintentar, text: "Reintentar" }) : null));
    }

    function vacio(texto, accion) {
        return el("div", { class: "vacio" }, el("p", { text: texto }),
            accion ? el("button", { class: "btn-secundario", type: "button", onclick: accion.onclick, text: accion.texto }) : null);
    }

    function fechaLocal(iso) {
        const f = new Date(iso);
        return isNaN(f) ? String(iso) : f.toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" });
    }

    function hora(iso) {
        const f = new Date(iso);
        return isNaN(f) ? "" : f.toLocaleTimeString("es-CL", { hour12: false });
    }

    AXIS.ui = { el, svg, anexar: (destino, ...hijos) => agregar(destino, hijos), limpiar, setSemaforo, renderChecks, alerta, vacio, fechaLocal, hora, ICONO };
    AXIS.validarIPv4 = (texto) => RE_IPV4.test(String(texto).trim());
})();
