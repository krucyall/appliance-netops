/* panelOspf.js — pestana OSPF: adyacencias del equipo (FULL = sana; cualquier otro estado = atencion). */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    const { el, anexar, limpiar, ICONO } = AXIS.ui;

    function pintar() {
        const caja = limpiar($("ospf-contenido"));
        const u = AXIS.auditoria.ultima();
        if (!u) return AXIS.sinAuditoria(caja);
        const o = u.datos.ospf;
        const chk = (u.datos.checks || []).find((c) => c.check === "ospf");

        if (!o) {   // el equipo no corre OSPF (o no se pudo consultar): se explica con el mensaje del check
            const estado = chk ? chk.estado : "desconocido";
            return caja.append(el("div", { class: "kpi estado-" + estado },
                el("h3", { text: "Adyacencias OSPF" }),
                el("p", { class: "kpi-estado", text: ICONO[estado] + " " + (chk ? chk.mensaje : "Sin datos OSPF.") }),
                chk && chk.detalle && chk.detalle.length ? el("ul", { class: "detalle" }, chk.detalle.map((d) => el("li", { text: d }))) : null));
        }

        const sanos = o.vecinos.filter((v) => v.es_sano).length;
        anexar(caja,
            u.origen === "historial" ? el("p", { class: "meta", text: "Datos de la última auditoría guardada de " + u.ip }) : null,
            el("div", { class: "kpi estado-" + o.estado },
                el("h3", { text: "Adyacencias OSPF · " + u.datos.hostname }),
                el("p", { class: "kpi-estado", text: ICONO[o.estado] + " " + o.mensaje }),
                el("p", { class: "meta", text: sanos + " de " + o.vecinos.length + " vecino(s) en FULL" })),
            o.vecinos.map((v) => el("div", { class: "tarjeta estado-" + (v.es_sano ? "verde" : "rojo") },
                el("span", { class: "tarjeta-texto" },
                    el("strong", { text: "Vecino " + v.vecino_id + " · " + v.interfaz }),
                    el("small", { text: "Área " + v.area + " · Dead time " + v.tiempo_muerto_seg + " s" }),
                    v.es_sano ? null : el("small", { class: "texto-alerta", text: v.mensaje })),
                el("span", { class: "badge " + (v.es_sano ? "verde" : "rojo"), text: v.estado }))));
    }

    function iniciar() {
        AXIS.tabs.registrar("ospf", { alMostrar: pintar });
        AXIS.bus.on("auditoria", () => { if (!$("tab-ospf").hidden) pintar(); });
    }

    AXIS.panelOspf = { iniciar };
})();
