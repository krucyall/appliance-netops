/* panelTelemetria.js — latencia continua (GET /api/telemetria) con grafico SVG NATIVO: sin Chart.js ni CDN,
   funciona sin internet. Solo consulta mientras la pestana esta visible y la pantalla activa. */
(function () {
    window.AXIS = window.AXIS || {};
    const $ = (id) => document.getElementById(id);
    const { el, svg, limpiar, alerta, vacio, hora, ICONO } = AXIS.ui;
    let temporizador = null, datos = null, seleccion = null;

    const ms = (v) => (v == null ? "—" : v + " ms");

    function grafico(serie) {
        const W = 640, H = 210, L = 52, R = 12, T = 12, B = 28, aw = W - L - R, ah = H - T - B;
        const valores = serie.filter((m) => m.ms != null).map((m) => m.ms);
        const techo = Math.max(100, Math.ceil(Math.max(0, ...valores) / 50) * 50);
        const x = (i) => L + (serie.length > 1 ? (i * aw) / (serie.length - 1) : aw / 2);
        const y = (v) => T + ah - (v / techo) * ah;
        const g = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "grafico", role: "img", "aria-label": "Latencia en milisegundos" });

        for (let k = 0; k <= 4; k++) {   // cuadricula + eje Y
            const v = (techo * k) / 4;
            g.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }),
                svg("text", { x: L - 8, y: y(v) + 4, class: "eje", "text-anchor": "end" }, Math.round(v) + (k === 4 ? " ms" : "")));
        }
        g.append(svg("line", { x1: L, x2: W - R, y1: y(100), y2: y(100), class: "umbral" }));   // 100 ms: limite de "latencia alta"

        let d = "", cortado = true, ultimo = null;
        serie.forEach((m, i) => {
            if (m.ms == null) {   // muestra perdida: banda roja vertical
                cortado = true;
                g.append(svg("rect", { x: x(i) - 2, y: T, width: 4, height: ah, class: "perdida-banda" }));
            } else {
                d += (cortado ? "M" : "L") + x(i).toFixed(1) + " " + y(m.ms).toFixed(1) + " ";
                cortado = false; ultimo = [x(i), y(m.ms)];
            }
        });
        g.append(svg("path", { d: d.trim(), class: "traza" }));
        if (ultimo) g.append(svg("circle", { cx: ultimo[0].toFixed(1), cy: ultimo[1].toFixed(1), r: 4, class: "punto" }));
        g.append(svg("text", { x: L, y: H - 8, class: "eje" }, hora(serie[0].t)),
            svg("text", { x: W - R, y: H - 8, class: "eje", "text-anchor": "end" }, hora(serie[serie.length - 1].t)));
        return g;
    }

    function pintar() {
        const aviso = limpiar($("telemetria-estado")), caja = limpiar($("telemetria-contenido"));
        if (datos.error) aviso.append(el("p", { class: "aviso", text: "⚠️ " + datos.error }));
        if (!datos.objetivos.length) return caja.append(vacio("Aún no hay equipos monitoreados. Ejecuta una auditoría y se agregará automáticamente."));
        if (!datos.objetivos.some((o) => o.ip === seleccion)) seleccion = datos.objetivos[0].ip;
        const o = datos.objetivos.find((x) => x.ip === seleccion);

        caja.append(
            el("div", { class: "chips", role: "tablist", "aria-label": "Equipos monitoreados" }, datos.objetivos.map((t) =>
                el("button", { class: "chip" + (t.ip === seleccion ? " activo" : ""), type: "button", role: "tab", "aria-selected": String(t.ip === seleccion),
                    onclick: () => { seleccion = t.ip; pintar(); }, text: (ICONO[t.estado] || ICONO.desconocido) + " " + t.ip }))),
            el("div", { class: "kpis" },
                [["Último", ms(o.ultimo_ms)], ["Promedio", ms(o.promedio_ms)], ["Mín / Máx", (o.min_ms == null ? "—" : o.min_ms + " / " + o.max_ms + " ms")],
                 ["Pérdida", o.perdida_pct + " %"], ["Muestras", String(o.muestras)]].map(([t, v]) =>
                    el("div", { class: "kpi-mini" }, el("small", { text: t }), el("strong", { text: v })))),
            o.serie.length >= 2 ? grafico(o.serie) : el("p", { class: "vacio", text: "Recolectando muestras… (una cada pocos segundos)" }),
            el("button", { class: "btn-secundario", type: "button", text: "Dejar de monitorear " + o.ip,
                onclick: async () => {
                    try { await AXIS.api("/api/telemetria/objetivos/" + o.ip, { metodo: "DELETE" }); } catch (_) { /* ya no existe */ }
                    refrescar();
                } }));
    }

    async function refrescar() {
        try { datos = await AXIS.api("/api/telemetria"); pintar(); }
        catch (error) { limpiar($("telemetria-contenido")); alerta($("telemetria-estado"), error, refrescar); }
    }

    function iniciarSondeo() {
        detenerSondeo();
        refrescar();
        temporizador = setInterval(() => { if (!document.hidden) refrescar(); }, AXIS.config.TELEMETRIA_REFRESCO_MS);
    }
    function detenerSondeo() { if (temporizador) { clearInterval(temporizador); temporizador = null; } }

    function iniciar() { AXIS.tabs.registrar("telemetria", { alMostrar: iniciarSondeo, alOcultar: detenerSondeo }); }

    AXIS.panelTelemetria = { iniciar };
})();
