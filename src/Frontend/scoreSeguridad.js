/* scoreSeguridad.js — pestana Seguridad L2: score (0-100) con medidor SVG, hallazgos por severidad
   y puertos "mareados" (habilitados sin enlace). Usa la ultima auditoria de la sesion. */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    const { el, svg, anexar, limpiar, vacio, ICONO } = AXIS.ui;
    const SEVERIDAD = { alta: { orden: 0, color: "rojo" }, media: { orden: 1, color: "amarillo" }, baja: { orden: 2, color: "verde" } };

    function medidor(score, estado) {
        const R = 46, C = 2 * Math.PI * R;
        return svg("svg", { viewBox: "0 0 120 120", class: "gauge " + estado, role: "img", "aria-label": "Score de seguridad " + score + " de 100" },
            svg("circle", { cx: 60, cy: 60, r: R, class: "gauge-fondo" }),
            svg("circle", { cx: 60, cy: 60, r: R, class: "gauge-arco", transform: "rotate(-90 60 60)",
                "stroke-dasharray": (C * score / 100).toFixed(1) + " " + C.toFixed(1) }),
            svg("text", { x: 60, y: 64, class: "gauge-valor", "text-anchor": "middle" }, String(score)),
            svg("text", { x: 60, y: 82, class: "gauge-sub", "text-anchor": "middle" }, "/ 100"));
    }

    function tarjetaScore(seg, checks) {
        if (!seg) {
            const c = checks.find((x) => x.check === "seguridad_l2");
            return el("div", { class: "kpi" }, el("h3", { text: "Score de seguridad L2" }),
                el("p", { class: "meta", text: c ? c.mensaje : "Sin datos de seguridad L2." }));
        }
        return el("div", { class: "kpi" }, el("h3", { text: "Score de seguridad L2" }), medidor(seg.score, seg.estado_color),
            el("p", { class: "meta", text: seg.hallazgos.length + " hallazgo(s) · " + ICONO[seg.estado_color] }));
    }

    function tarjetaPuertos(c) {
        if (!c) return el("div", { class: "kpi" }, el("h3", { text: "Puertos mareados" }), el("p", { class: "meta", text: "Sin datos de interfaces." }));
        return el("div", { class: "kpi estado-" + c.estado }, el("h3", { text: "Puertos mareados" }),
            el("p", { class: "kpi-estado", text: ICONO[c.estado] + " " + c.mensaje }),
            c.detalle && c.detalle.length ? el("ul", { class: "detalle" }, c.detalle.map((d) => el("li", { text: d }))) : null);
    }

    function hallazgos(seg) {
        if (!seg) return null;
        if (!seg.hallazgos.length) return el("p", { class: "vacio", text: "✅ Sin hallazgos: VLAN 1 y PortFast/BPDU Guard en orden." });
        const ordenados = seg.hallazgos.slice().sort((a, b) => (SEVERIDAD[a.severidad] || { orden: 9 }).orden - (SEVERIDAD[b.severidad] || { orden: 9 }).orden);
        return el("div", {}, el("h3", { text: "Hallazgos" }), ordenados.map((h) => {
            const sev = SEVERIDAD[h.severidad] || { color: "amarillo" };
            return el("div", { class: "tarjeta estado-" + sev.color },
                el("span", { class: "tarjeta-texto" }, el("strong", { text: h.interfaz }), el("small", { text: h.mensaje })),
                el("span", { class: "badge " + sev.color, text: h.severidad }));
        }));
    }

    function pintar() {
        const caja = limpiar($("seg-contenido"));
        const u = AXIS.auditoria.ultima();
        if (!u) return AXIS.sinAuditoria(caja);
        const checks = u.datos.checks || [];
        anexar(caja,
            u.origen === "historial" ? el("p", { class: "meta", text: "Datos de la última auditoría guardada de " + u.ip }) : null,
            el("div", { class: "grid-2" }, tarjetaScore(u.datos.seguridad, checks), tarjetaPuertos(checks.find((c) => c.check === "puertos_mareados"))),
            hallazgos(u.datos.seguridad));
    }

    function iniciar() {
        AXIS.tabs.registrar("seguridad", { alMostrar: pintar });
        AXIS.bus.on("auditoria", () => { if (!$("tab-seguridad").hidden) pintar(); });
    }

    AXIS.scoreSeguridad = { iniciar };
})();
