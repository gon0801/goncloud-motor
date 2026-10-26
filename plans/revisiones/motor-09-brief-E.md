# MOTOR 09: implementar E y detenerse

Usa `pstack:poteto-mode` en `/Users/dn/.agents/skills/poteto-mode/SKILL.md`. Eres el implementador; el lead revisa. David coordina manualmente. Implementa solamente E, sin delegar ni lanzar otros modelos, y detente.

Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`, base `1b37d63`. B y C+D están aprobadas para continuar desarrollo y son cambios locales sin commit: consérvalos. No hagas reset, limpieza, cambio de rama ni reemplazo desde main. `/Users/dn/motor` es la instalación, no el checkout de trabajo.

Lee `AGENTS.md`, E de `plans/motor-09.md`, `plans/revisiones/motor-09-diseno.md` (instalación, reversa y formato), y `plans/revisiones/motor-09-fase-CD-cierre.md`. No repitas investigación general ni rehagas B–D.

Entrega:

1. En los scripts de instalación añade `--maquina mac|mini|pc` y `--conf-sha SHA`. Resuelve código y configuración exactos y valida el destino completo en copia antes de cambiar instalación, marcas o referencias anteriores. No sigas una rama de configuración cambiante. Mantén mantenimiento activo.
2. Persiste recibo local por máquina, fuera del Store, con identidad de intento, pareja anterior código/configuración, destino, formato y etapa. Distingue intento pendiente de última instalación completa. Conserva copia privada de maquina.toml si se cambia; nunca secretos en recibos o repo.
3. Reinstalar el mismo destino es no-op y conserva el anterior real. Registra origen antes de la primera mutación. Tras una caída parcial reutiliza ese origen, nunca el destino instalado a medias. Completa el recibo solo tras observar ambos SHA y configuración efectiva. etiqueta-anterior deriva del recibo; no es otra fuente de verdad.
4. La reversa valida pareja y compatibilidad de formato antes de mutar y conserva retención. No rebajes formato 3 para permitir código viejo ni borres eventos o controles. Un fallo queda retenido y muestra etapa y causa; no afirmes atomicidad entre máquinas.
5. Extiende la prueba de instalación existente con la reproducción de reinstalación, misma versión, rechazo previo y recuperación parcial en repos/homes desechables. Ejecuta `bash ops/tests/test-instala.sh` y reutiliza `python3 -B -m unittest -v tests.test_motor08_efectos.RecuperaEfectosTest.test_caida_tras_lanzar_recupera_resultado_sin_repetir_instalacion`. Comprueba compatibilidad consultando el límite del código candidato, no solo existencia de formato.py. Una simulación de PC no acredita instalación/reversa Windows nativas.

Archivos de E: `ops/instala-todo.sh`, `ops/vuelve-a-la-anterior.sh`, `ops/lib.sh`, `ops/huellas.sh`, `ops/tests/test-instala.sh`, `motor/formato.py`. Si un llamador adicional resulta imprescindible, justifica el cambio mínimo; no amplíes la arquitectura. Respeta AGENTS: comprobaciones focalizadas de instalación/reversa y del incidente; sin baterías generales, pruebas de texto, mutantes, pytest ni kit nuevo.

Solo ejecuta caminos de prueba con destinos temporales controlados. Nunca ejecutes los instaladores/reversa con sus destinos productivos por omisión. Usa dobles de SSH donde lo requiera la prueba existente y, para evidencia nativa, copia temporal de código y home temporal. No intercambies la instalación real ni modifiques tareas/servicios para comprobar E. Si la comprobación nativa no puede aislarse, registra el límite y no la declares aprobada.

La restricción de Job del servicio PC sigue pendiente; esta fase no la elude ni acredita recuperación operativa. No hagas fallback inseguro para conseguir que una instalación aparente funcionar.

Entrega el diff y `.motor09/resultado-E.json` con archivos, decisiones, comandos reproducibles, rutas de evidencia privada, resultados por escenario y límites por plataforma. Conserva las sondas citadas. Si reanudas, lee ese resultado y el diff sin rehacer trabajo ni repetir evidencia válida.

No implementes F/G. No toques `/Users/dn/motor`, homes productivos, planes de Orbit, modelos, configuración compartida, tareas, servicios ni secretos. Nunca SIGCONT. No commit, push, PR, merge, instalación/reversa real ni despliegue. No uses el motor para autorrepararse. Entrega E y detente para revisión.
