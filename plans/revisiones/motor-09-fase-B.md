Estado posterior: [B cerrada tras el ajuste del lead](motor-09-fase-B-cierre.md).

Estado posterior: ver la [segunda revisión de B](motor-09-fase-B-ronda-2.md). Windows corregido; quedan dos bloqueantes reproducidos.

# MOTOR 09: revisión de fase B

**REQUEST_CHANGES. B no aprobada; C no empieza.**

Revisión directa del lead sobre el diff local en `recuperacion/motor-09`, base `1b37d63dfbc7157ffa42b5680505ed8e8962f4a1`, sin commit de entrega. Hay tres fallos reproducidos de comportamiento pedido. No se modificó código de implementación ni se operaron servicios productivos.

## 1. Se declara cerrado un intento cuyo agente sigue vivo

Ubicación: `motor/effects.py:144`, `motor/effects.py:1336`, `motor/daemon.py:887`.

`intento_vivo` solo observa el PID del wrapper. `cerrar_arboles` envía TERM a su grupo y deja de buscar cuando ese PID muere. El agente tiene otro grupo, como permite el lanzamiento de runwrap mediante `plataforma.kwargs_grupo()`. Aunque existen `agent.pid` y `agent.inicio`, este cierre no los consulta. `_cierra` usa ese resultado para escribir `AttemptEnded` y confirmar Pausado/Cancelado; `_hay_arbol` usa la misma observación para liberar mantenimiento.

Reproducción con dos procesos inocuos en grupos distintos y los marcadores del agente presentes:

```sh
python3 -B /Users/dn/dev/goncloud-motor-recuperacion/.motor09/revision-B/arbol.py
```

Resultado: `intentos_que_declara_vivos=0`, `supervisor_vivo=false`, **`agente_vivo=true`**. El script termina sus propios procesos al salir. Esto permite confirmar detención y eventualmente reanudar con un agente anterior todavía ejecutándose. La ausencia del wrapper no prueba ausencia del árbol; identidad desconocida tampoco prueba cierre.

## 2. La sincronización nueva rompe retención y apertura de Store en Windows

Ubicación: `motor/control.py:127`, `motor/formato.py:79`, llamado desde `motor/store.py:52`.

Ambos caminos abren un directorio con `os.open(..., os.O_RDONLY)` para sincronizarlo. Reproduje ambos en la PC con su Python real, copiando solamente código a una carpeta temporal y creando homes desechables. Los dos lanzan **PermissionError, errno 13**. No es el WinError 5 de creación de procesos pendiente en C: es otra falla introducida por B.

```sh
python3 -B /Users/dn/dev/goncloud-motor-recuperacion/.motor09/revision-B/windows.py
```

La sonda usa SSH a `pc` y borra su carpeta temporal al terminar. Resultado detallado: `.motor09/revision-B/windows-result.txt`.

En retención queda `mantenimiento.json` escrito pero el comando falla. En Store queda `formato=3`, pero no se abre ni crea la base en esa llamada. También la liberación usa el mismo helper después de borrar el archivo: puede fallar tras quitar la protección. El arreglo debe usar una operación de persistencia compatible con cada plataforma y comprobar la primera apertura/elevación, no solo un segundo intento que encuentre el marcador ya escrito.

## 3. Liberar borra la protección antes de persistir su decisión

Ubicación: `motor/daemon.py:837` y `motor/daemon.py:840`.

`_liberar` elimina `mantenimiento.json` y después intenta escribir el recibo. Inyecté un fallo SQLite en ese último paso sobre un Store y home temporales. Resultado: **sin retención, sin recibo**. Al repetir el mismo comando responde `rechazado: no hay retención`, en lugar de recuperar la decisión. Una caída entre esos pasos deja la misma ventana. El daemon que vuelva a arrancar ya no encuentra la protección.

```sh
python3 -B /Users/dn/dev/goncloud-motor-recuperacion/.motor09/revision-B/liberar.py
```

La sonda usa Store real y sustituye únicamente `append_batch` por un fallo controlado. Debe existir una decisión durable y recuperable para esa generación; un fallo no puede dejar una liberación silenciosa ni convertir el reintento en otro resultado.

## Comprobaciones y límites

Los tres comandos existentes declarados por el implementador sí pasan: `FormatoTest`, `MergeNoRepiteTest` y `test_estado_abre_la_base_en_solo_lectura_y_sin_daemon` (3 pruebas, OK). `git diff --check` también pasa. Esas comprobaciones no cubren los fallos anteriores.

La sonda original `/tmp/motor09-b.py`, citada en la entrega, ya no existe; sus afirmaciones adicionales no se acreditan como reproducción independiente en esta revisión. Esto se registra como límite, no como un cuarto bloqueo. Las sondas del lead y huellas de los archivos revisados quedan en `.motor09/revision-B/`, fuera del contenido versionado.

No se verificaron aquí tareas programadas, Job restrictivo, Telegram, instalación ni despliegue: corresponden a fases posteriores. No se exige una batería nueva ni se condiciona B a ejecutar esas fases.

Siguiente paso manual: enviar a Grok el [brief de correcciones B](motor-09-brief-B-correcciones.md). Debe corregir estos tres puntos en una sola ronda y detenerse para revisión.
