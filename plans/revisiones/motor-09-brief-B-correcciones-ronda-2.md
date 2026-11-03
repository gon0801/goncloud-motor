# MOTOR 09: completar las correcciones de B

Usa `pstack:poteto-mode`. Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`. Conserva el diff y la corrección de Windows, comprobada por el lead. Implementa solo estas correcciones de B si David te envía este brief; después detente.

Lee `plans/revisiones/motor-09-fase-B-ronda-2.md`. Hay dos reproducciones nuevas, ambas de los comportamientos ya pedidos:

1. Ejecuta `python3 -B .motor09/revision-B/nieto.py`. El agente muere con TERM, pero su hijo del mismo grupo ignora TERM y sobrevive. El cierre devuelve cero pendientes y la observación dice ausente. Corrige la observación y el cierre para que la muerte del líder no oculte descendientes. Aplica el plazo y la escalada con identidad/propiedad segura; si no puedes demostrar ausencia conserva estado incierto. Ni pausa/cancelación ni liberación pueden confirmar con descendientes vivos. No mates PIDs ajenos ni uses SIGCONT. No sustituyas este caso por otro que mate solo un agente sin hijos.
2. Ejecuta `python3 -B .motor09/revision-B/reinicio-liberacion.py`. Hay un commit de liberado y una caída antes de borrar mantenimiento. Al volver, `ingest_inbox` encuentra el recibo, se salta la recuperación de `_liberar` y archiva el comando dejando retención activa. Recupera la operación desde el camino real del inbox/reinicio. Conserva decisión por cmd_id y generación, no borres retenciones posteriores y no omitas la comprobación de árboles al completar una operación pendiente. Comprueba ambos lados del fallo: antes del commit y después del commit/antes del borrado.

Las sondas actuales afirman las fallas para reproducirlas; ajusta sus expectativas para demostrar la corrección y conserva versiones/resultados fuera del contenido versionado. Respeta AGENTS: comprobaciones pertinentes y procesos inocuos, sin baterías nuevas, pruebas de texto, mutantes ni kit nuevo.

Repite las sondas anteriores solo si el cambio invalida su evidencia. La prueba Windows de archivos ya pasó; no es evidencia de Job, servicio ni control del árbol de C. Actualiza `.motor09/resultado-B.json` con comandos reproducibles, resultados y lo no verificado.

No avances a C–G, no delegues ni lances otros modelos. No toques `/Users/dn/motor`, homes productivos, tareas, servicios, configuración compartida o secretos. Nunca SIGCONT. No push, PR, merge, instalación ni despliegue. Entrega para revisión del lead y detente.
