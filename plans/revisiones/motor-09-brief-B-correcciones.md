# MOTOR 09: corregir fase B y detenerse

Usa `pstack:poteto-mode` en `/Users/dn/.agents/skills/poteto-mode/SKILL.md`. Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`. Conserva el diff actual. David coordina manualmente; no avances a C ni delegues ni lances otros modelos.

Lee la [revisión B](motor-09-fase-B.md), el contrato B de `plans/motor-09.md` y el diseño aprobado. Corrige los tres hallazgos juntos:

1. Haz que pausa, cancelación, estado y liberación observen el árbol propio, incluido el agente en otro grupo y sus marcadores de identidad. No interpretes wrapper ausente o error de observación como árbol ausente. Registra antes de detener, aplica el plazo de 10 segundos y confirma solo después del cierre comprobado. Conserva resultados y efectos remotos por conciliar; no consumas intentos del modelo. Evita que una reanudación arranque un segundo agente. No rehagas aún la política de arranque Windows de C.
2. Corrige la persistencia de retención, liberación y formato para Windows. La primera creación y elevación 2→3 deben terminar correctamente sin depender de un segundo intento. Comprueba con Python nativo en carpetas temporales, sin tocar el home instalado. No ocultes fallos reales de escritura con una captura general de excepciones.
3. Haz recuperable la liberación de una generación frente a fallo de Store o caída entre la decisión y el cambio de archivo. El mismo cmd_id debe conservar la decisión; un fallo no debe dejar mantenimiento retirado sin decisión durable recuperable. Mantén el archivo independiente de configuración y SQLite para activar la retención. Usa los componentes existentes; no agregues otro motor ni un framework.

Reproduce primero con las sondas privadas de `.motor09/revision-B/`; sus asserts actuales identifican la falla, por lo que debes adaptar la comprobación esperada al arreglo. Conserva las reproducciones y resultados verificables de la corrección fuera del contenido versionado. Incluye el caso de wrapper muerto con agente vivo, reinicio/reintento de liberación y Windows nativo. No son instrucciones para ejecutar servicios productivos. Respeta AGENTS: sin batería nueva, pruebas de texto, mutantes ni kit de calidad. Reutiliza pruebas existentes pertinentes; una comprobación de sistema con procesos inocuos acredita la detención.

Actualiza `.motor09/resultado-B.json` con decisiones, comandos reproducibles, resultados y límites. Guarda las sondas que cites: no dependas de un script que desaparezca. No afirmes que Job/servicio Windows quedó comprobado por estas sondas de archivos.

No toques `/Users/dn/motor`, homes productivos, tareas, servicios, configuración compartida o secretos. Nunca SIGCONT. No push, PR, merge, instalación ni despliegue. No implementes C–G. Entrega para revisión del lead y detente.
