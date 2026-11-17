/* diffViewer.js — visor de diferencias: agregadas (verde sutil) y eliminadas (rojo sutil).
   Acepta el formato de comparar_configs() / GET /api/configs/diff:
     { mensaje, cantidad_diferencias, diff: [{tipo: "igual"|"agregada"|"eliminada", linea}] }
   Por defecto muestra solo los cambios, con separadores "n lineas sin cambios". */
(function () {
    window.AXIS = window.AXIS || {};
    const { el, limpiar } = AXIS.ui;
    const LIMITE = 300;
    const PREFIJO = { agregada: "+ ", eliminada: "- ", igual: "  " };

    function lineas(diff, soloCambios) {
        if (!soloCambios) return diff.map((l) => ({ tipo: l.tipo, texto: l.linea }));
        const salida = [];
        let iguales = 0, hayCambios = false;
        for (const l of diff) {
            if (l.tipo === "igual") { iguales++; continue; }
            if (iguales && hayCambios) salida.push({ tipo: "separador", texto: "⋯ " + iguales + " líneas sin cambios" });
            iguales = 0; hayCambios = true;
            salida.push({ tipo: l.tipo, texto: l.linea });
        }
        if (iguales && hayCambios) salida.push({ tipo: "separador", texto: "⋯ " + iguales + " líneas sin cambios" });
        return salida;
    }

    function render(destino, datos) {
        const diff = datos.diff || [];
        const agregadas = diff.filter((l) => l.tipo === "agregada").length;
        const eliminadas = diff.filter((l) => l.tipo === "eliminada").length;
        let soloCambios = true, limite = LIMITE;
        const cuerpo = el("div", { class: "diff-cuerpo" });
        const alternar = el("button", { class: "btn-secundario", type: "button" });

        function pintar() {
            const todas = lineas(diff, soloCambios);
            limpiar(cuerpo).append(...todas.slice(0, limite).map((l) =>
                el("pre", { class: "linea-diff " + l.tipo, text: (PREFIJO[l.tipo] || "") + l.texto })));
            if (todas.length > limite) {
                cuerpo.append(el("button", { class: "btn-secundario", type: "button", text: "Mostrar " + Math.min(LIMITE, todas.length - limite) + " líneas más",
                    onclick: () => { limite += LIMITE; pintar(); } }));
            } else if (!todas.length) {
                cuerpo.append(el("p", { class: "vacio", text: "Sin diferencias." }));
            }
            alternar.textContent = soloCambios ? "Ver archivo completo" : "Ver solo cambios";
        }

        alternar.addEventListener("click", () => { soloCambios = !soloCambios; limite = LIMITE; pintar(); });
        limpiar(destino).append(
            el("p", { class: "meta", text: datos.mensaje || "" }),
            el("div", { class: "diff-resumen" },
                el("span", { class: "badge verde", text: "+" + agregadas }),
                el("span", { class: "badge rojo", text: "−" + eliminadas }),
                diff.length ? alternar : null),
            cuerpo);
        pintar();
    }

    AXIS.diffViewer = { render };
})();
