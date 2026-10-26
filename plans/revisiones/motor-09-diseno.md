# MOTOR 09: recuperar el control y conservar el trabajo

**Propuesta del 25 de octubre de 2026. Diseño terminado para revisión. No implementado, no instalado y no entregado al motor.**

David pidió diseñar los arreglos y el plan, sin implementar. El resultado buscado es recibir un plan, ejecutarlo, informar y obedecer una detención sin perder trabajo ni repetir operaciones externas. La base estudiada es `1b37d63`, v1.0.34, instalada en Mac, Mini y PC. El plan de trabajo está en [motor-09.md](../motor-09.md).

## Alcance y evidencia

Se conserva `Store → fold → decide → Effects`, el historial SQLite y los resultados existentes. No se crea otro motor, servicio de agentes, framework de operaciones ni una segunda base. No se reparan Orbit, accounting, proveedores, modelos o credenciales dentro de este encargo.

Las tres SQLite pasaron `PRAGMA integrity_check`. Los eventos se pudieron reconstruir estructuralmente. La comprobación de reconstrucción usó `maquina=None` y no certifica la vigencia de las mediciones frente al entorno. Mini tiene dos intenciones aplicadas y ninguna incierta. PC no tiene planes activos registrados. Mac conserva `motor-win-spawn`, con su intento 5 suspendido y los bloques siguientes esperando.

La sonda de Windows reprodujo `WinError 5` dentro de un Job restrictivo y terminó correctamente al omitir solamente `BREAKAWAY`. Desde SSH normal funcionaron ambas variantes. No se inspeccionó el Job exacto de Hermes. La aceptación parcial, pérdida de reintento de aviso y marca de lanzamiento se reprodujeron aisladamente; no se atribuyen automáticamente a los síntomas históricos.

La evidencia local original está en `/tmp/motor-incidente-20261025/recuperacion.txt`. No es una dependencia permanente de ejecución. Antes de implementar, conserva sus resultados saneados y vuelve a observar el estado vivo; no copies las bases, secretos ni registros crudos al repositorio.

## Decisión de arquitectura

Se compararon dos propuestas independientes. A completa las transiciones existentes y añade una barrera local pequeña. B introduce una proyección global de obligaciones y decisiones. Se elige A por su menor cambio y por permitir detener el arranque sin depender del daemon. De B se incorporan la propiedad explícita de procesos Windows y la elevación real del marcador de formato.

| Alternativa | Decisión y motivo |
| --- | --- |
| Completar las fronteras existentes | Base elegida. La aceptación, el arranque, el aviso y la instalación tienen dueños concretos. |
| Unificar todo en `fold_journal`, `Obligation` y `perform` | Descartada. Amplía la arquitectura antes de reparar el incidente. |
| Volver globalmente a v1.0.33 | Descartada. Reintroduce previa y rechazo de configuración; el rollback actual solo cambia código. |
| Borrar todas las banderas de Windows | Descartada. Hubo descendientes que sobrevivían a los timeouts. |

Estos principios determinaron decisiones concretas. [Model the Domain](/Users/dn/.agents/skills/principle-model-the-domain/SKILL.md) separa reserva, proceso observado y error de creación. [Make Operations Idempotent](/Users/dn/.agents/skills/principle-make-operations-idempotent/SKILL.md) conserva el mismo recibo al repetir una orden o instalación. [Laziness Protocol](/Users/dn/.agents/skills/principle-laziness-protocol/SKILL.md) evita una nueva capa universal. [Sequence work into verifiable units](/Users/dn/.agents/skills/principle-sequence-verifiable-units/SKILL.md) ordena cambios comprobables antes de una sola entrega coherente.

## Contrato propuesto para David

Los comandos siguientes todavía no existen. `H` representa el directorio de estado del motor; `P` es el ID de un plan.

```text
motor mantenimiento activar --home H --motivo recuperacion
motor mantenimiento liberar --home H --generacion G
motor pausa P --home H
motor reanuda P --home H
motor cancela P --home H
motor estado --home H --json
motor recibo C --home H --json
```

`G` es la generación devuelta al activar mantenimiento. `C` es el identificador de la orden. Una liberación vieja nunca elimina una retención nueva.

| Orden | Resultado exigido |
| --- | --- |
| Activar mantenimiento | Persiste la retención aunque el daemon no arranque. Confirma retención escrita, no procesos detenidos. |
| Pausar un plan | Impide nuevas asignaciones y solicita terminar sus agentes. Conserva trabajo y resultados. |
| Reanudar un plan | Conserva trabajo, sin `SIGCONT` ni repetición de efectos inciertos. No libera mantenimiento. |
| Cancelar un plan | Terminal para ese ID. No limpia, no revierte ni permite reactivarlo con nota o reentrega. Otro trabajo usa otro ID. |
| Consultar estado | Distingue retención del home, control del plan, árboles pendientes de detener, efectos por conciliar y salud de avisos. |
| Consultar recibo | Distingue orden encolada, aplicada, rechazada y desconocida. Un ID inexistente no aparece encolado. |

Una pausa puede confirmar que cesaron las asignaciones y los agentes locales sin afirmar que un despliegue remoto se deshizo. El estado muestra por separado cualquier efecto externo pendiente. No libera un plan para repetir una operación mientras su resultado siga incierto.

## Retención del home y control del plan

`motor/control.py` será un módulo pequeño para un archivo durable `H/mantenimiento.json`. No tendrá SQLite, scheduler ni red. El archivo contendrá versión, generación, ID de solicitud, fecha y motivo. Su escritura será temporal y atómica, con persistencia del archivo y directorio donde corresponda. Archivo ilegible o corrupto significa retenido, con causa visible.

La retención se lee antes de cargar configuración o ejecutar su recuperación automática. Un candado corto propio serializa activación y liberación; no usa el `motor.lock` vitalicio. El daemon compatible es el único que libera una generación después de comprobar sus condiciones. Los estados de planes viven exclusivamente en eventos, escritos por el dueño actual del Store.

Con configuración inválida sigue disponible el camino local mínimo para activar retención y consultar estado y recibos en solo lectura. Eso no significa que un daemon completo o Telegram puedan funcionar sin contexto válido. La detención del servicio sigue siendo una operación del sistema; el error local explica esa limitación y conserva avisos pendientes.

El daemon bajo mantenimiento solo procesa controles, recoge resultados, observa efectos existentes, detiene árboles propios e informa. No acepta trabajo nuevo, no lanza agentes ni inicia nuevas escrituras externas. Los comandos de plan entregados durante mantenimiento reciben rechazo con causa. Una entrega posterior usa un `cmd_id` nuevo; repetir el anterior conserva el rechazo. Las órdenes de control tienen prioridad sobre los planes. `execute` comprueba retención antes de cada despacho. Se elimina cualquier camino previo a esa comprobación que pueda iniciar trabajo.

El control de plan es `Activo`, `PausaSolicitada`, `Pausado`, `CancelacionSolicitada` o `Cancelado`. `fold` deriva el estado desde eventos. Pausa y cancelación se registran antes de detener. El plazo normal de cierre de agentes es 10 segundos; después se escala al mecanismo de terminación del árbol. Si no se demuestra la ausencia del árbol, continúa pendiente y no se confirma detención. El motivo `operador` no consume intentos del modelo.

Un merge, despliegue remoto o tarea ya aceptada puede finalizar después de solicitar pausa. No se inicia la siguiente operación. Se consulta su recibo concreto y se conserva `aplicado`, `no_aplicado` o `incierto` según evidencia. Nunca se deduce `no_aplicado` de la ausencia de un proceso local. Una nota sigue siendo una nota y no cancela ni reanuda.

## Lanzamiento de procesos y recibo de inicio

Una única política de creación en plataforma atiende `lanzar_separado`, `runwrap.main` y `effects._run`. Se mantienen las APIs actuales de vida, grupos, timeouts y guardián WSL. La propiedad distingue `TrabajadorSeparado`, que debe sobrevivir al reinicio del daemon, de `ComandoContenido`, cuyo árbol se termina con su dueño.

En Windows se intenta la combinación actual. Solo un error de creación con `winerror == 5` permite un segundo intento idéntico, quitando únicamente `CREATE_BREAKAWAY_FROM_JOB`. Se conservan `CREATE_NEW_PROCESS_GROUP` y `CREATE_NO_WINDOW`. No hay tercer intento. Otros errores se propagan. No se repite una operación por errores posteriores a la creación.

La alternativa sin `BREAKAWAY` no acredita por sí sola supervivencia ni contención. El Job restrictivo de la sonda y el contexto real de servicio se observan como condiciones distintas; el Job exacto de Hermes sigue sin identificarse. Se comprueba la vida del wrapper al reiniciar solamente el daemon y la terminación de padre, hijo y nieto al pedir parar. La contención del agente se establece antes de permitirle ejecutar. Si no puede cumplirse la propiedad requerida, se registra fallo de entorno y no se ejecuta el agente. No se introduce un broker nuevo para ocultarlo.

Las restricciones de separación e herencia se contrastaron con [Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags) y [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) de Microsoft. Esas reglas no sustituyen observar el contexto de la PC.

La marca de intento diferencia `Reservado`, `ProcesoObservado`, `CreacionFallida` y `ArranquePorConciliar`. La reserva mantiene un token estable y precede a `Popen`. `runwrap` conserva un candado por intento y publica identidad durable antes de iniciar el agente. La identidad contiene PID, arranque de máquina y hora de creación.

Si `Popen` falla de forma confirmada, se persiste su error saneado. Nunca produce `AttemptStarted` ni `perdido`. Si el padre cae en la ventana ambigua, se busca el recibo del wrapper, identidad y resultado con ese token. No se crea otro agente por un plazo vencido o por no encontrar un PID. Una reserva no resuelta queda visible como arranque por conciliar, sin gastar modelos ni relanzar automáticamente. Los marcadores antiguos con `proc={}` se interpretan como reservas ambiguas, sin inventar su causa.

## Aceptación y avisos coherentes

`Effects.prepara_repos(plan)` obtiene todos los pines y consultas que pueden fallar, sin publicar eventos ejecutables. `Store.append_batch` publica en una transacción los pines, `PlanLoaded`, el recibo de comando y la solicitud de aviso correspondiente. Ninguna llamada de red entra en la transacción SQLite.

Una repetición del mismo `cmd_id` devuelve su decisión completa anterior. Una colisión inesperada aborta el lote; no se ignoran filas sueltas. Un error anterior al commit no activa el plan. Un error de almacenamiento conserva el inbox pendiente y no fabrica un rechazo definitivo que no pudo guardar. Si solo falla mover el archivo después del commit, el replay no crea una versión nueva.

La obligación de aviso se guarda junto con el hecho como `NoticeRequested`. La cola existente se materializa con ID estable. Al arrancar y en cada tick se reparan solicitudes sin confirmación de materialización. `ConfRechazada`, rechazo de comando y avisos de planes usan el mismo orden. Los eventos históricos no se reenvían en masa; se adoptan sus archivos existentes y lo desconocido se muestra como desconocido.

Telegram cuenta como entrega solo con respuesta válida `ok=true`. JSON inválido, falta de `ok` o `ok=false` nunca son éxito. Red, 429 y 5xx conservan pendiente con espera acotada. Los demás 4xx conservan el aviso fallido y muestran una falla visible localmente. Un reintento explícito tras corregir transporte conserva el ID del aviso. La garantía es al menos una vez; una caída después del envío puede duplicarlo.

La recarga prepara y valida configuración, policy y notifier juntos antes de sustituir el contexto. No reviven los vetos retirados de pstack. La salud global y los avisos fallidos aparecen en `estado` y `doctor`, sin token ni URL que lo contenga. Una cola vacía no significa entrega.

## Instalación, reversa y formato

El instalador recibe una configuración fijada por SHA y valida el destino exacto en copia antes de cambiar instalaciones o referencias previas. Añade selección explícita `--maquina mac|mini|pc` para desplegar progresivamente y `--conf-sha SHA` para no seguir una rama cambiante. El código destino es el HEAD mergeado y etiquetado que ya comprueba el script.

Cada máquina conserva un recibo local de instalación, fuera del Store. Contiene ID de intento, pareja anterior de código y configuración, destino, formato y etapa. Conserva copia local privada de `maquina.toml` si se cambia, sin incorporar secretos al recibo ni al repo. El intento pendiente y la última instalación completada son objetos distintos.

Si el destino ya coincide, la instalación es un no-op y conserva el anterior real. Si hay cambio, registra el origen antes del primer cambio. Tras observar ambos SHA y la configuración efectiva, publica el recibo completado. Una caída parcial se recupera con el mismo origen; nunca toma el destino parcialmente instalado como nuevo anterior. `etiqueta-anterior` queda como salida compatible del recibo, sin ser otra fuente de verdad.

La reversa usa la pareja anterior, comprueba compatibilidad de código, configuración y formato antes de mutar y conserva mantenimiento. Una falla deja la máquina retenida con su etapa, no un éxito global. No hay atomicidad distribuida entre las tres máquinas; cada una tiene resultado observable.

El protocolo nuevo necesita formato **3** porque v1.0.34 ignoraría cancelaciones y nuevos recibos. El lector nuevo conserva formatos anteriores. Con candado exclusivo, eleva durablemente el marcador existente de 2 a 3 antes del primer evento con semántica nueva. Cambiar solamente `VERSION` no alcanza, porque `activa` hoy retorna cuando encuentra un marcador. Un marcador 3 sin eventos nuevos es una actualización conservadora válida. Nunca se rebaja para permitir una reversa.

Todo escritor nuevo, incluido el camino de `aparte` que abre Store, comprueba formato. Los scripts de instalación y reversa no se conforman con encontrar `formato.py`; consultan el límite del código candidato. Un destino antiguo que no respeta control o formato se rechaza antes del checkout. Esta restricción se muestra al operador, sin borrar el historial para eludirla.

## Firmas que coordinan la implementación

Este es un boceto documental, no código importable. Los tipos son privados salvo las interfaces entre dueños existentes.

```python
Hold = {version, generation, request_id, created_at, reason}
ProcessIdentity = {pid, boot_id, started_at}
PreparedPlan = {plan, version, pins}
NoticeRequest = {notice_id, text}
InstalledPair = {code_sha, conf_sha, machine_config_digest, state_format}
LaunchRecord = Reserved | ProcessObserved | CreationFailed | UncertainLaunch

control.activar(home, request_id, reason) -> Hold
control.leer(home) -> Hold | None
control.liberar(home, expected_generation, observed_state) -> None
Store.append_batch(command_id, events) -> Committed | AlreadyCommitted
Effects.prepara_repos(plan) -> PreparedPlan
plataforma.crear_proceso(command, ownership, **popen_options) -> ProcessCreated
Notifier.materializa(request) -> None
Notifier.envia_pendientes() -> tuple[DeliveryObservation, ...]
```

`ProcessCreated` lleva el proceso y el modo observado de separación. `DeliveryObservation` es entrega confirmada, reintento posterior o rechazo del transporte. El daemon registra esas observaciones. No se crea `fold_journal`, un despachador universal ni otra copia mutable del estado de planes.

## Condiciones para la recuperación posterior

La implementación se construye en una copia aislada de `origin/main`. No en el checkout instalado ni en esta rama de planes atrasada. El código nuevo no se usa para autorrepararse mediante el plan `motor-win-spawn` que sigue suspendido.

Antes de reemplazar instalaciones en Mac y Mini, un administrador debe deshabilitar y descargar sus servicios. El código viejo no lee la retención nueva. Nunca se envía `SIGCONT`. Se comprueban identidades y árboles, se terminan los procesos suspendidos y se confirma liberación de candados. Sin ese permiso se conserva la suspensión y no se intercambia código vivo.

Se instala la pareja nueva todavía retenida. El daemon nuevo arranca primero en mantenimiento y recoge resultados. Se cancela o pausa explícitamente `motor-win-spawn` antes de liberar la retención. La liberación no reabre otros planes de forma implícita. La primera comprobación usa un único plan controlado y luego avanza máquina por máquina.

## Spec delta propuesto y límites de comprobación

Al adoptar este diseño, modifica las cláusulas de `docs/diseno.md` que excluyen pausa y presentan revisión obligatoria antigua. Conserva el contrato vigente de MOTOR 08 sobre asesores y efectos inciertos. Añade control durable, aceptación coherente, entrega observable y reversa compatible. Hasta su aprobación este documento es una propuesta; no cambia las garantías del binario instalado.

No se crean baterías generales, pruebas de texto, mutantes, diez carriles, mediciones de rendimiento ni un kit de calidad. Solo se agregan pruebas en los cuatro casos autorizados por David. El incidente Windows lleva una regresión; instalación, detención del sistema y reversa tienen sus comprobaciones pertinentes. Los defectos aislados sin incidente de producción se comprueban ejecutándolos en un home desechable, sin inventar otra batería. Las comprobaciones existentes válidas para un SHA se reutilizan.

La revisión de diseño comparó dos alternativas y una evaluación independiente de seguridad, operación y regresiones. El juez eligió A y los dos aportes indicados de B. Queda por comprobar durante la implementación la propiedad real de los Jobs de Windows, la detención completa y el recorrido operativo. Ninguno está declarado aprobado por este documento.
