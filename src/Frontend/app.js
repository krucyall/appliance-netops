document.getElementById('btn-auditar').addEventListener('click', ejecutarAuditoria);

async function ejecutarAuditoria() {
    const semaforo = document.getElementById('semaforo');
    const boton = document.getElementById('btn-auditar');
    const consola = document.getElementById('consola-detalles');
    const listaReglas = document.getElementById('lista-reglas');
    
    // 1. Capturamos la IP y armamos el JSON que espera tu FastAPI (clase CredencialesDispositivo)
    const ipSeleccionada = document.getElementById('ip_equipo').value;
    const credenciales = {
        ip: ipSeleccionada,
        usuario: "admin",
        password: "password123",
        driver_name: "ios"
    };

    // Resetear UI
    semaforo.className = 'procesando';
    semaforo.innerHTML = `Conectando por SSH a ${ipSeleccionada}...`;
    boton.disabled = true;
    consola.classList.add('oculto');
    listaReglas.innerHTML = '';

    try {
        // 2. Hacemos un POST a la ruta correcta (/api/auditar)
        const respuesta = await fetch('http://127.0.0.1:8000/api/auditar', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(credenciales)
        });

        if (!respuesta.ok) {
            throw new Error(`HTTP error! status: ${respuesta.status}`);
        }

        const datos = await respuesta.json();

        // 3. Evaluar Semáforo leyendo 'estado_general' (como lo definiste en tu backend)
        if (datos.estado_general === "verde") {
            semaforo.className = 'verde';
            semaforo.innerHTML = `✅ ${datos.hostname} Seguro y Operativo`;
        } else {
            semaforo.className = 'rojo';
            semaforo.innerHTML = `❌ ${datos.hostname} Vulnerabilidades Detectadas`;
        }

        // 4. Poblar la consola de detalles leyendo tu lista 'checks'
        if (datos.checks && datos.checks.length > 0) {
            datos.checks.forEach(regla => {
                const li = document.createElement('li');
                li.innerHTML = `${regla.estado === 'verde' ? '✅' : '❌'} <strong>${regla.check}:</strong> ${regla.mensaje}`;
                listaReglas.appendChild(li);
            });
            consola.classList.remove('oculto');
        }

    } catch (error) {
        semaforo.className = 'rojo';
        semaforo.innerHTML = '⚠️ Error de Comunicación con API';
        console.error("Error al auditar:", error);
    } finally {
        boton.disabled = false;
    }
}