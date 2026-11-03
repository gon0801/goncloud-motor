# MOTOR 09: implementar C + D y detenerse

Usa `pstack:poteto-mode` en `/Users/dn/.agents/skills/poteto-mode/SKILL.md`. Eres el implementador; el lead revisará C+D juntas. David coordina manualmente. Implementa primero C y compruébala, después D, sin pedir autorización entre ambas. Al terminar detente. No delegues ni lances otros modelos.

## Punto de partida

Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`, base `1b37d63dfbc7157ffa42b5680505ed8e8962f4a1`. La entrega de B es un diff local, no un commit: conserva sus cambios y documentos. No hagas reset, limpieza, cambio de rama ni reemplazo desde main. `/Users/dn/motor` es la instalación productiva: no la edites ni ejecutes para probar este trabajo.

Lee `AGENTS.md`, C y D de `plans/motor-09.md`, `plans/revisiones/motor-09-diseno.md` y `plans/revisiones/motor-09-fase-B-cierre.md`. El diseño aprobado manda sobre una interpretación distinta de este brief. No repitas A, how, why, arena ni investigación general. B está aprobada para continuar el desarrollo, no para desplegar.

B ya tiene `Store.append_batch`, retención, controles de plan y formato 3. Reutilízalos. Conserva el cierre POSIX y la recuperación de liberación por inbox. En Windows `presencia_grupo` conserva `incierto` porque aún no observa el Job: C debe aportar la observación real, no cambiarlo a `ausente` por defecto.

## C: recuperar arranque y propiedad de procesos Windows

1. Unifica la política de creación usada por `plataforma.lanzar_separado`, `runwrap.main` y `effects._run`. Distingue trabajador separado, que debe sobrevivir al reinicio del daemon, de comando contenido, cuyo árbol termina con su dueño. Conserva timeouts y guardián WSL. Establece contención antes de ejecutar el agente.
2. Solo si falla la creación con `winerror == 5`, reintenta una vez quitando únicamente `CREATE_BREAKAWAY_FROM_JOB`. Conserva las demás opciones. Otros errores y errores posteriores a la creación no deben repetir el proceso. Un fallback que arranca pero pierde propiedad del árbol no acredita el arreglo.
3. Distingue reserva con token estable, proceso observado, creación fallida y arranque por conciliar. El wrapper publica identidad durable y conserva candado por intento antes de iniciar el agente. Un error confirmado no crea un `AttemptStarted` vacío, no se convierte en perdido ni consume modelos. Una ventana ambigua se concilia con token, identidad y resultado; no se relanza por timeout o falta de PID. Trata los antiguos `proc={}` como ambiguos, sin inventar su causa.
4. Observa y termina el árbol completo aunque muera el líder. No confirmes ausencia por error de inspección. Conserva la incertidumbre si no puedes demostrar propiedad o cierre. No agregues un broker ni otro motor.
5. Añade la única regresión prevista del incidente productivo en `tests.test_windows.WindowsTest.test_job_restringido_permite_lanzar_y_cerrar`. Ejecútala en Windows nativo y reutiliza `test_proceso_separado_sobrevive_al_cierre_del_daemon`, `test_matar_grupo_termina_el_arbol` y `test_job_mata_al_arbol_al_cerrar_el_handle`. Demuestra supervivencia al reiniciar solo daemon y cierre de padre, hijo y nieto en un home de diagnóstico. Un skip en Mac, SSH o un echo no acreditan el contexto real del servicio.

Usa SSH a `pc` y su Python nativo `C:\Users\ehven\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe` sobre una copia temporal del código y homes desechables, siguiendo las sondas privadas de `.motor09/revision-B/`. No uses el paquete productivo instalado para representar el diff. No cambies ni habilites la tarea `GoncloudMotor` o servicios productivos para probar. Si el contexto real del servicio no puede comprobarse dentro de esos límites, registra exactamente qué falta y no declares ese criterio aprobado. Una restricción externa permite continuar los trabajos independientes de D, sin ocultar el pendiente de C.

## D: recepción y avisos coherentes

1. Implementa `Effects.prepara_repos(plan)` sin publicar eventos ejecutables. Prepara todos los pines antes de aceptar. Usa el `Store.append_batch` existente para publicar pines, `PlanLoaded`, recibo y `NoticeRequested` en una transacción; ninguna red dentro de SQLite. Repetir `cmd_id` conserva la decisión anterior; una colisión inesperada aborta el lote. Un fallo de almacenamiento deja inbox pendiente, sin activar ni fabricar rechazo definitivo. Una caída tras commit y antes de mover el archivo no crea otra versión.
2. Materializa la cola de avisos desde `NoticeRequested` con ID estable y recuperación al arrancar y en cada tick. Incluye rechazo de configuración y comandos. Adopta archivos históricos existentes; no reenvíes masivamente el historial.
3. Solo una respuesta válida `ok=true` confirma entrega. JSON inválido, falta de `ok` u `ok=false` no son éxito. Red, 429 y 5xx conservan pendiente con espera acotada; otros 4xx dejan fallo visible. El reintento explícito conserva ID. Documenta la garantía de al menos una vez y la posible duplicación tras caída posterior al envío.
4. Prepara y valida configuración, policy y notifier antes de sustituir el contexto. No reintroduzcas los vetos retirados de pstack. `estado`, `doctor` y `recibo` deben distinguir pendiente, entregado, fallido y desconocido, mostrar causas globales sin secretos y no deducir entrega de una cola vacía.
5. Comprueba repo inaccesible, fallo al guardar aceptación, fallo al materializar aviso, reinicio entre commit y movimiento de inbox, y transporte inválido con homes temporales y dobles locales de red. No envíes Telegram real. Conserva las sondas y resultados reproducibles; no inventes una batería nueva para incidentes no ocurridos.

## Alcance, comprobaciones y entrega

Archivos de código: la unión de los permitidos en C y D del plan. Puedes actualizar su contrato en `docs/diseno.md`. Si otro archivo resulta imprescindible para migrar un llamador existente, justifica el cambio mínimo; no amplíes el producto ni hagas refactors generales.

Respeta AGENTS: sin pruebas de texto, mutantes, pytest, kit de calidad ni baterías generales. Ejecuta comprobaciones focalizadas al final del cambio pertinente y reutiliza evidencia válida que no hayas invalidado. Conserva las garantías de B, especialmente cancelación terminal, retención, cierre incierto y efectos externos por conciliar.

Entrega el diff y `.motor09/resultado-CD.json` con archivos cambiados, decisiones, comandos reproducibles, resultados separados por plataforma y criterio, rutas de sondas y pendientes. Distingue código implementado de comportamiento observado; no presentes no verificado como aprobado. Si reanudas tras perder contexto, lee ese resultado parcial y el diff; no empieces desde cero ni repitas evidencia vigente.

No implementes E–G. No modifiques planes activos de Orbit, modelos elegidos, homes productivos, configuración compartida, tareas, servicios ni secretos. Nunca SIGCONT. No ejecutes `motor-win-spawn` para autorreparar el motor. No commit, push, PR, merge, instalación, reversa ni despliegue. Conserva todos los cambios locales, entrega y detente para revisión conjunta del lead.
