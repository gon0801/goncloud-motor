Estado posterior: [correcciones aprobadas para continuar E](motor-09-fase-CD-cierre.md); se conserva abajo la revisión original.

# MOTOR 09: revisión conjunta C+D

**REQUEST_CHANGES. Tres bloqueantes reproducidos; no avanzar a E ni hacer PR.**

Revisión directa del lead sobre el diff local de `recuperacion/motor-09`, base `1b37d63`, sin commit. El lead solo añadió sondas privadas y estos documentos; no modificó implementación ni operó tareas, servicios, homes o credenciales productivas. Las huellas revisadas están en `.motor09/revision-CD/lead/huellas.json`.

## 1. Trabajador separado ejecuta sin garantizar supervivencia

Ubicación: `motor/plataforma/windows.py:607–626`, especialmente el retorno de `TRABAJADOR_SEPARADO` después del fallback.

El segundo Popen sin BREAKAWAY se acepta como trabajador separado sin comprobar la propiedad requerida. Reproduje el caso con Python nativo de PC y un daemon desechable dentro de un Job propio con KILL_ON_JOB_CLOSE y sin permiso breakaway. El trabajador fue creado mediante la API nueva, alcanzó a ejecutar y murió al salir ese daemon. La política no rechazó el entorno ni impidió ejecutar.

```sh
python3 -B .motor09/revision-CD/lead/separado-job.py
```

Resultado: `separacion=sin_breakaway`, `agente_ejecuto=true`, **`trabajador_sobrevive_al_daemon=false`**.

La sonda copia el código a una carpeta temporal de PC, usa su Python uv nativo y limpia sus procesos. No usa GoncloudMotor ni pretende certificar el Job exacto de Hermes. Demuestra un entorno restrictivo válido que la API actual acepta aunque incumple su propiedad. El diseño exige garantizar supervivencia o fallar como entorno incompatible antes de ejecutar. Eliminar el error 5 por sí solo no basta.

La regresión entregada comprueba el comando contenido en un Job restrictivo. La prueba reutilizada de supervivencia corre en otro contexto. Ninguna comprueba la combinación anterior. Se debe extender la regresión del incidente ya prevista, no agregar una batería.

## 2. Un fallo confirmado al crear el agente se convierte en relevo de modelo

Ubicación: `motor/runwrap.py:317`, `motor/effects.py:1269`; selección posterior en `motor/machine.py`.

El wrapper publica `creacion_agente=fallida` y conserva el error, pero `_juzga_intento` lo traduce a `sin_resultado`, que pertenece al relevo ordinario. Reproduje un comando inexistente ejecutando `runwrap.main` real en carpeta temporal; después pasé su resultado por la clasificación y selección reales.

```sh
python3 -B .motor09/revision-CD/lead/creacion-agente.py
```

Resultado: creación fallida con `FileNotFoundError`, clasificación `sin_resultado` con detalle genérico y **selección de `modelo-B` después de fallar `modelo-A` sin crear agente**. Aunque `sin_resultado` no suma al contador de fallas que agotan escalera, sí pausa ese modelo y pasa trabajo a otro. Por eso no se afirma que agotó la escalera; el fallo observado es el relevo automático por un error confirmado de creación y la pérdida de su causa en el resultado efectivo.

C exige distinguir creación fallida de falla del modelo también cuando el wrapper arrancó pero el agente no pudo crearse. Conserva causa saneada y estado visible, sin fabricar intento de modelo ni repetir automáticamente con otros modelos. No borres eventos previos para corregirlo.

## 3. Una URL inválida de avisos persiste el token en el registro de errores

Ubicación: `motor/notify.py:333` y `motor/daemon.py:837–851`.

`Notifier` acepta un base_url sin esquema. `urlopen` lanza ValueError incluyendo la URL completa con `/bot<TOKEN>/sendMessage`. La captura de transporte solo cubre OSError; el ValueError llega a `_fase`, que guarda `str(e)` en `EffectFailed`. Además, la salud de avisos no presenta causa.

```sh
python3 -B .motor09/revision-CD/lead/transporte-invalido.py
```

Resultado: `fase_ok=false`, **`token_ficticio_en_evento=true`**, `salud.causa=""`. Se usó un token inventado y una URL sin esquema que no hace ninguna petición; no se leyeron ni imprimieron credenciales reales.

La configuración actual no valida ese esquema antes de construir el notifier. La frontera de transporte debe rechazar o clasificar el error con causa visible y saneada antes de que escape hacia los logs. La validación no debe imprimir la URL que contenga el token. La recarga debe preparar el notifier válido antes de sustituir el contexto.

## Evidencia conservada y límites

`git diff --check` pasa. Se inspeccionaron la regresión Windows y el registro entregado de cuatro pruebas nativas correctas; se reutiliza esa evidencia para sus casos exactos, sin presentarla como cobertura de las reproducciones nuevas. No se repitieron baterías ni las mismas pruebas sin cambios. Las sondas del lead ejecutadas arriba prueban los tres fallos de esta revisión.

La entrega distingue correctamente que el contexto de la tarea GoncloudMotor/Hermes y el acceso a un Job entre sesiones Windows no se comprobaron. Ese límite sigue pendiente antes de acreditar la recuperación operativa; no autoriza probar sobre servicios productivos.

Nota de alcance, no cuarto bloqueo: la aceptación ahora conserva el comportamiento previo de no mandar un Telegram nuevo al tomar un plan. El brief/diseño mencionaban incluir solicitud de aviso con la aceptación; el implementador cambió `docs/diseno.md` para excluirla. Debe quedar explícita esta diferencia en la entrega y en el cierre del diseño, sin introducir una nueva notificación por suposición. Los avisos de rechazo y de progreso sí deben mantener la recuperación pedida.

Siguiente paso manual: [brief de correcciones C+D](motor-09-brief-CD-correcciones.md). Los tres hallazgos se corrigen en una sola ronda; no se avanza a E ni a PR. El lead no lanzó otro implementador.
