Estado posterior: [B cerrada tras el ajuste del lead](motor-09-fase-B-cierre.md).

# MOTOR 09: tercera revisión de B

**Los dos casos anteriores están corregidos. Queda un bloqueante en Windows: se declara ausencia sin observar descendientes. B todavía no aprobada.**

Revisión del diff local de `recuperacion/motor-09`, base `1b37d63`, sin commit. El lead no modificó implementación ni operó servicios o homes productivos.

## Evidencia que pasa

Se ejecutaron estas sondas desde el checkout de recuperación:

```sh
python3 -B .motor09/revision-B/nieto.py
python3 -B .motor09/revision-B/reinicio-liberacion.py
python3 -B .motor09/revision-B/arbol.py
python3 -B .motor09/revision-B/liberar.py
```

Todas pasan. En Mac se observa el grupo cuyo líder murió y se termina el hijo que ignora TERM. La recuperación del inbox conserva el comando ante fallo previo al commit, completa la liberación tras caída posterior al commit y no archiva mientras se observa un árbol vivo. La marca ilegible sigue como incierta. `git diff --check` pasa.

La sincronización de directorios Windows no cambió desde su comprobación nativa aprobada en la revisión anterior; no se repitió esa evidencia. Las pruebas existentes ya reportadas por el implementador tampoco se repitieron sin motivo.

## Bloqueante reproducido en Windows

Ubicación: `motor/plataforma/windows.py:510`, función `presencia_grupo`.

La nueva implementación devuelve siempre `ausente`. El comentario reconoce que no lista descendientes y que C observará el Job, pero devolver ausente permite a B afirmar que el árbol desapareció. La falta de observación debe seguir siendo incierta hasta que C aporte la comprobación real.

El lead copió únicamente el código a un directorio temporal de PC y lanzó dos procesos Python inocuos. Conservó la identidad del agente, lo terminó y comprobó que su hijo seguía vivo. No se usó el lanzador con BREAKAWAY ni se tocó ninguna tarea o servicio. La sonda terminó sus procesos propios y eliminó su carpeta temporal.

```sh
python3 -B .motor09/revision-B/windows-arbol.py
```

Resultado nativo de PC:

```json
{"agente_muerto": true, "hijo_vivo": true, "observacion": "ausente", "arbol_ausente": true}
```

Esto vuelve a permitir confirmar Pausado/Cancelado o liberar con un proceso ejecutándose. No se exige adelantar el Job de C. Basta que el código de B conserve `incierto` cuando la plataforma no puede acreditar ausencia; la observación real de propiedad y cierre Windows sigue en C. No basta cambiar el comentario ni registrar este resultado como no verificado: el falso positivo está reproducido.

La salida está en `.motor09/revision-B/windows-arbol-result.txt`; las huellas del código, en `huellas-ronda-3.json` de esa carpeta. No se ejecutaron despliegue, Telegram, tareas programadas ni servicios.

El lead no inició otra ejecución automática. Está preparado un [brief acotado](motor-09-brief-B-correccion-windows.md) para el envío manual que decida David. Tras corregir este criterio se revisa solo el cambio y la reproducción afectada, sin reabrir los casos ya acreditados salvo que se modifiquen.
