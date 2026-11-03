# MOTOR 09: cierre de E tras corrección del lead

**E corregida y comprobada para preparar la revisión del PR de B–E. No acredita instalación productiva ni autoriza despliegue o reapertura.**

David autorizó al lead implementar directamente la última corrección. Se conserva el diff previo, la rama `recuperacion/motor-09` y la base `1b37d63`, sin commit.

## Cambio

Un origen por intento conserva los bytes y permisos del TOML efectivo y el destino/tipo del enlace `conf` antes de la primera mutación. La copia privada y su manifiesto quedan bajo `~/.motor/privado/`, con archivos modo 600. El recibo solo lleva referencia y SHA256; no incorpora el contenido del TOML ni secretos.

El reintento valida la misma referencia contra el recibo pendiente y no vuelve a capturar el destino parcial. La reversa valida el manifiesto y la copia antes del primer checkout, restaura el origen efectivo y comprueba su huella y enlace. Una copia dañada produce rechazo previo; una restauración fallida conserva la etapa de reversa. Se mantienen formato y retención.

El helper nuevo `ops/maquina_local.py` comparte estas operaciones concretas entre POSIX y PC. Es la única adición fuera de los archivos inicialmente enumerados y evita duplicar esa lógica en dos programas inline. El controlador envía su fuente por base64 para que la operación no dependa de un helper ausente en el código antiguo instalado. En Windows restaura junctions como junctions, sin sustituirlas por un enlace que requiera otros permisos. Los recibos antiguos sin referencia conservan su camino anterior; no se inventa una copia histórica inexistente.

Se modificaron `ops/instala-todo.sh`, `ops/vuelve-a-la-anterior.sh`, `ops/lib.sh`, la comprobación existente `ops/tests/test-instala.sh`, y se añadió el helper. No se modificó motor/effects.py ni el resto de B–D para este ajuste.

## Evidencia

- `bash ops/tests/test-instala.sh`: salida **0**, final `todo bien`. Resultado completo en `.motor09/revision-E/lead/instala-ajuste-local.txt`. Se extendió el escenario existente con TOML local y enlace distintos del repo, rechazo de copia privada dañada antes de cambiar conf y conservación del TOML original al reintentar una instalación parcial. También pasan los escenarios anteriores de misma pareja, formato, origen y continuación de reversa parcial. El candidato de los repos desechables incluye el árbol B–E actual.
- `python3 -B .motor09/revision-E/lead/toml-local.py`: salida **0** tras actualizar su expectativa. Confirma `maquina_local_restaurada=true` y conservación de origen/mantenimiento. El TOML restaurado es deliberadamente distinto del TOML del repo: es el original de la máquina.
- `python3 -B .motor09/revision-E/lead/pc-recibos.py`: salida **0**, resultado `origen_y_junction_restaurados`. Usa los helpers actuales mediante CMD/Python nativos de PC, sobre un perfil temporal único. Captura TOML y junction, simula un destino parcial, conserva el mismo origen al reintentar y restaura ambos. El perfil temporal se elimina. Log en `.motor09/revision-E/lead/pc-origen-ajuste.txt`.
- La prueba existente `tests.test_motor08_efectos.RecuperaEfectosTest.test_caida_tras_lanzar_recupera_resultado_sin_repetir_instalacion` ya pasó durante la revisión de esta entrega, con la identidad corregida. Este ajuste no modifica sus archivos ni su camino; se reutiliza esa evidencia.
- `bash -n` sobre los scripts modificados y `git diff --check`: correctos. Las huellas finales están en `.motor09/revision-E/lead/huellas-cierre.json`.

No se agregaron baterías ni pruebas de texto/mutantes. La ampliación de la comprobación existente cubre instalación/reversa, uno de los casos autorizados en AGENTS.

## Límite operativo

La prueba nativa acredita el manejo/restauración del origen local en Windows, **no toda la instalación/reversa Windows ni el Job real de GoncloudMotor/Hermes**. Esos pendientes siguen siendo condiciones para instalar/habilitar/reabrir la PC. El rechazo seguro de un Job incompatible no equivale a recuperar su funcionamiento.

No se tocó `/Users/dn/motor`, homes productivos, configuración compartida, tareas, servicios o credenciales reales. No se hizo commit en esta rama, push, PR, merge, instalación/reversa productiva, despliegue ni SIGCONT. Los commits creados por las comprobaciones viven únicamente en repos desechables.

El siguiente paso es preparar un único PR de B–E cuando David lo indique. F/G siguen sin autorización operativa.
