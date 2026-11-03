const API_URL = 'http://127.0.0.1:8000';

// "mock" | "real" segun MEOW_MODO del backend (se lee de /api/estado al iniciar)
let modoApi = null;

document.getElementById('btn-auditar').addEventListener('click', ejecutarAuditoria);
cargarModo();
cargarDispositivos();

// Pregunta al backend en que modo corre: en mock muestra el aviso de
// datos simulados, en real muestra los campos de usuario/contrasena.
async function cargarModo() {
    try {
        const respuesta = await fetch(`${API_URL}/api/estado`);
        if (!respuesta.ok) {
            throw new Error(`HTTP error! status: ${respuesta.status}`);
        }
        modoApi = (await respuesta.json()).modo;
    } catch (error) {
        console.error("Error al consultar el modo del backend:", error);
        return;
    }
    document.getElementById('aviso-modo').classList.toggle('oculto', modoApi !== 'mock');
    document.getElementById('credenciales').classList.toggle('oculto', modoApi !== 'real');
}

// Llena el selector con el inventario que entrega el backend (dispositivos_lab.json)
async function cargarDispositivos() {
    const selector = document.getElementById('ip_equipo');
    const boton = document.getElementById('btn-auditar');
    boton.disabled = true;

    try {
        const respuesta = await fetch(`${API_URL}/api/dispositivos`);
        if (!respuesta.ok) {
            throw new Error(`HTTP error! status: ${respuesta.status}`);
        }
        const dispositivos = await respuesta.json();

        selector.innerHTML = '';
        dispositivos.forEach(equipo => {
            const opcion = document.createElement('option');
            opcion.value = equipo.ip;
            opcion.dataset.driver = equipo.driver;
            // textContent (no innerHTML) para que un nombre raro no inyecte HTML
            opcion.textContent = `${equipo.nombre} (${equipo.ip})`;
            selector.appendChild(opcion);
        });
        boton.disabled = dispositivos.length === 0;
    } catch (error) {
        selector.innerHTML = '<option value="">No se pudo cargar la lista de equipos</option>';
        console.error("Error al cargar dispositivos:", error);
    }
}

async function ejecutarAuditoria() {
    const semaforo = document.getElementById('semaforo');
    const boton = document.getElementById('btn-auditar');
    const consola = document.getElementById('consola-detalles');
    const listaReglas = document.getElementById('lista-reglas');
    
    // 1. Capturamos la IP y armamos el JSON que espera tu FastAPI (clase CredencialesDispositivo)
    const selector = document.getElementById('ip_equipo');
    const ipSeleccionada = selector.value;
    // Usuario/contrasena los escribe el tecnico en pantalla (solo en modo real)
    const credenciales = {
        ip: ipSeleccionada,
        usuario: document.getElementById('usuario_ssh').value.trim(),
        password: document.getElementById('password_ssh').value,
        driver_name: selector.selectedOptions[0]?.dataset.driver || "ios"
    };

    if (modoApi === 'real' && (!credenciales.usuario || !credenciales.password)) {
        semaforo.className = 'rojo';
        semaforo.textContent = '⚠️ Ingrese usuario y contraseña SSH del equipo';
        return;
    }

    // Resetear UI
    semaforo.className = 'procesando';
    semaforo.textContent = modoApi === 'mock'
        ? `Auditando ${ipSeleccionada} (modo prueba)...`
        : `Conectando por SSH a ${ipSeleccionada}...`;
    boton.disabled = true;
    consola.classList.add('oculto');
    listaReglas.innerHTML = '';

    try {
        // 2. Hacemos un POST a la ruta correcta (/api/auditar)
        const respuesta = await fetch(`${API_URL}/api/auditar`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(credenciales)
        });

        if (!respuesta.ok) {
            // El backend explica el problema en 'detail' (ej: "Usuario o
            // contrasena incorrectos"), listo para mostrar en pantalla.
            let detalle = `Error HTTP ${respuesta.status}`;
            try {
                const cuerpo = await respuesta.json();
                if (typeof cuerpo.detail === 'string') {
                    detalle = cuerpo.detail;
                }
            } catch (e) {
                // Respuesta sin JSON: se queda el mensaje generico
            }
            semaforo.className = 'rojo';
            semaforo.textContent = `⚠️ ${detalle}`;
            return;
        }

        const datos = await respuesta.json();
        const sufijoModo = datos.modo === 'mock' ? ' (datos simulados)' : '';

        // 3. Evaluar Semáforo leyendo 'estado_general' (como lo definiste en tu backend)
        // hostname, check y mensaje vienen del equipo: se insertan con
        // textContent (nunca innerHTML) para que no puedan inyectar HTML.
        if (datos.estado_general === "verde") {
            semaforo.className = 'verde';
            semaforo.textContent = `✅ ${datos.hostname} Seguro y Operativo${sufijoModo}`;
        } else {
            semaforo.className = 'rojo';
            semaforo.textContent = `❌ ${datos.hostname} Vulnerabilidades Detectadas${sufijoModo}`;
        }

        // 4. Poblar la consola de detalles leyendo tu lista 'checks'
        if (datos.checks && datos.checks.length > 0) {
            datos.checks.forEach(regla => {
                const li = document.createElement('li');
                const nombreCheck = document.createElement('strong');
                nombreCheck.textContent = `${regla.check}:`;
                li.append(
                    `${regla.estado === 'verde' ? '✅' : '❌'} `,
                    nombreCheck,
                    ` ${regla.mensaje}`
                );
                listaReglas.appendChild(li);
            });
            consola.classList.remove('oculto');
        }

    } catch (error) {
        semaforo.className = 'rojo';
        semaforo.textContent = '⚠️ Error de Comunicación con API';
        console.error("Error al auditar:", error);
    } finally {
        boton.disabled = false;
    }
}