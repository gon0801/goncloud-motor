Estado posterior: los dos casos de esta ronda pasan; ver [tercera revisión](motor-09-fase-B-ronda-3.md) para el único bloqueante restante en Windows.

# MOTOR 09: segunda revisión de B

**B sigue sin aprobación. Dos bloqueantes reproducidos; Windows corregido.**

Se revisaron las correcciones locales sobre base `1b37d63`, rama `recuperacion/motor-09`, sin commit. No se modificó código de implementación ni se operaron servicios o homes productivos. Las sondas solo crean procesos propios y homes temporales. Las huellas de los archivos revisados están en `.motor09/revision-B/huellas-ronda-2.json`.

## Comprobaciones que ahora pasan

El lead ejecutó las tres sondas conservadas por el implementador:

```sh
python3 -B .motor09/revision-B/arbol.py
python3 -B .motor09/revision-B/liberar.py
python3 -B .motor09/revision-B/windows.py
```

Las tres pasan. La tercera se ejecuta con Python nativo de PC sobre código copiado a una carpeta temporal: primera activación y liberación de retención, primera apertura de Store y elevación 2→3 correctas. La falla de sincronización de directorios de la revisión inicial queda corregida para estos casos observados.

También se observó agente de otro grupo con supervisor muerto, cierre de ese agente y conservación de una marca ilegible como incierta. El fallo SQLite previo al recibo ya conserva la retención y el reintento directo de `_liberar` devuelve la misma decisión. `git diff --check` pasa.

No se repitieron las pruebas existentes que la entrega ya reporta válidas: las reproducciones adicionales verifican condiciones que aquellas no cubren.

## Bloqueante 1: un hijo vivo se declara árbol ausente

Ubicación: `motor/effects.py:observar_arbol`, `motor/effects.py:cerrar_arboles`, `motor/daemon.py:_cierra` y `_hay_arbol`.

Ahora se observan wrapper y agente, pero no la supervivencia de sus descendientes. Reproduje un agente en sesión propia que lanza un hijo en su mismo grupo. El hijo ignora SIGTERM; el agente muere con SIGTERM. `cerrar_arboles` deja de esperar y no escala porque el líder ya no está vivo. `observar_arbol` devuelve ausente aunque el hijo siga ejecutándose en el grupo registrado.

Desde el checkout de recuperación:

```sh
python3 -B .motor09/revision-B/nieto.py
```

Resultado observado:

```json
{"intentos_pendientes": 0, "observacion": "ausente", "nieto_vivo": true}
```

La sonda limpia su propio grupo en `finally`. No toca procesos instalados. El efecto sigue siendo el bloqueante original: confirmar pausa/cancelación o liberar mientras queda trabajo vivo. Comprobar dos PIDs no demuestra ausencia del árbol. El criterio debe sobrevivir a la muerte del líder; si no puede demostrarlo, debe mantener observación incierta y no confirmar.

## Bloqueante 2: el reinicio por inbox omite completar la liberación

Ubicación: `motor/daemon.py:ingest_inbox`, `_liberar` y `_termina_liberacion`.

La decisión `liberado` ahora se registra antes de borrar el archivo. Pero la recuperación solo está dentro de `_liberar`. En el camino real del inbox, si `_ya_recibido` encuentra esa decisión, se salta `_procesa` y mueve el comando a procesados. Por eso una caída después del commit y antes de borrar deja una protección que no se retira, aunque el recibo diga liberado.

```sh
python3 -B .motor09/revision-B/reinicio-liberacion.py
```

La sonda usa Store e inbox reales en un home temporal, interrumpe justo antes de `control.liberar` y reconstruye el objeto consumidor con ese estado persistido. Luego invoca `ingest_inbox` sin sustituir su comportamiento.

Resultado observado:

```json
{"recibo": "liberado", "retenido_tras_reinicio": true, "comando_movido_a_procesados": true}
```

El comando ya no queda pendiente para terminar la operación. La sonda del implementador repetía `_liberar` directamente y por eso no detectaba esta omisión. Hace falta recuperación en el camino real, conservando la generación: nunca debe borrar una retención posterior ni eludir la comprobación de árboles al completar una operación pendiente.

## Alcance y decisión

No se exige ejecutar Job restrictivo, servicios, despliegue ni Telegram para corregir estos hallazgos de B. Tampoco una batería nueva. No se verificó la seguridad del despliegue ni se autorizó C–G.

La detención del árbol sigue bloqueante en dos revisiones consecutivas. Conforme a las instrucciones de David, no se inicia otra ronda automática; la decisión vuelve al operador. La recomendación del lead es corregir estos dos casos antes de aprobar B. Está preparado el [brief de corrección](motor-09-brief-B-correcciones-ronda-2.md) para el envío manual que David decida.
