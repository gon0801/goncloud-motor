# MOTOR 09: cierre de revisión C+D

**C+D aprobadas para continuar el desarrollo de E. No autoriza PR, instalación ni reapertura, ni acredita que PC pueda ejecutar desde su servicio actual.**

El lead revisó las correcciones locales en `recuperacion/motor-09`, base `1b37d63`, sin commit. Comparó sus huellas con la revisión anterior y examinó los cambios de Windows, clasificación de creación, selección de modelos, diagnóstico y frontera de avisos. No modificó código de implementación ni operó producción.

## Tres bloqueantes corregidos

Se ejecutaron desde este checkout:

```sh
python3 -B .motor09/revision-CD/lead/creacion-agente.py
python3 -B .motor09/revision-CD/lead/transporte-invalido.py
python3 -B .motor09/revision-CD/lead/separado-job.py
```

Los tres comandos terminaron con salida 0.

- **Windows nativo:** un trabajador que no puede salir del Job es rechazado antes de ejecutar. Resultado: rechazado=true, agente_ejecuto=false, pid=null. El comando contenido conserva su fallback; no se elude la propiedad del trabajador separado para declarar éxito. La sonda usa copia y procesos temporales, sin tarea ni servicio productivo.
- **Creación del agente:** el ejecutable inexistente conserva FileNotFoundError y errno=2 bajo creacion_fallida. La selección siguiente devuelve null, en lugar de pasar al modelo B. No se clasifica como falla del modelo ni se gasta escalera. El diagnóstico expone el motivo.
- **Avisos:** la URL sin esquema deja causa visible, mantiene pendiente y no guarda el token ficticio en EffectFailed. La recarga inválida conserva el notifier anterior. La comprobación usa un token inventado y no envía a Telegram.

`git diff --check` pasa. Se inspeccionó el registro entregado de las cuatro pruebas Windows ampliadas (salida 0) y se reutiliza para sus casos precisos. No se repitieron baterías ni evidencia válida sin cambio que la invalide. La recepción atómica y recuperación de avisos ya revisadas no se reabren por este cierre.

Las huellas del código están en `.motor09/revision-CD/lead/huellas-cierre.json`. Las sondas quedan en esa misma carpeta y los resultados anteriores se conservan en su subcarpeta `antes`.

## Límites operativos y nota de precisión

El rechazo de un Job incompatible es un resultado seguro, **no una recuperación funcional de PC**. La tarea/servicio GoncloudMotor/Hermes, su propiedad real, supervivencia al reiniciar solo daemon y acceso al Job entre sesiones Windows siguen sin acreditarse. Deben resolverse antes de habilitar o reabrir producción. No se debe instalar esperando que un rechazo seguro haga funcionar ese entorno.

Una falla confirmada de creación requiere una nota posterior para reintentar en el camino observado. La entrega dice que también basta cambiar la máquina, pero el nuevo bloqueo lleva huella vacía y la selección usa hecho_ts; no se acreditó ese reintento por cambio de máquina solamente. Es una precisión de documentación, no un nuevo bloqueo: el reintento explícito por nota existe y no se exige repetir automáticamente.

Se conserva el comportamiento de no emitir un Telegram nuevo al tomar un plan; el recibo local registra la aceptación. La diferencia con la solicitud de aviso de aceptación del diseño queda documentada en la revisión inicial. No se añadió una notificación por suposición.

No se probaron Telegram real, instalación o reversa, no se tocaron servicios y no se hizo commit, push, PR, merge ni despliegue. El paso siguiente es E mediante su [brief manual](motor-09-brief-E.md).
