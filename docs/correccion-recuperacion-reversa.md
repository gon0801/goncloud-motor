# Corrección de recuperación y reversa

Corregido el hallazgo funcional de la revisión de ronda5 sobre bf10a83. Los cambios están en el worktree de fases3–4, sin merge ni despliegue.

## Comportamiento

Aplicación, recuperación y reversa toman el mismo candado durante la operación, incluida la lectura y actualización de recibos. El arranque reserva su generación con ese candado. El cambio de generación consulta los ocupantes y conserva la versión y los recibos cuando hay trabajo vivo o incierto. Después del cierre puede continuar la misma operación.

La reversa independiente usa sólo biblioteca estándar para leer recibos y coordinarse. Observa el árbol con el runtime anterior, nunca con el motor que se retira. Comprueba por sí misma las reservas sin PID y usa sesion.observar para los árboles: no confía en filtros de flota antiguos que ignoraban hijos vivos. Si no puede observar mediante el runtime anterior, conserva la instalación e informa el error.

El candado de Windows espera mientras otro proceso lo ocupa, también más allá de diez segundos. Un error al reservar la generación impide lanzar un trabajador sin protección.

La herramienta de reversa se distribuye como conjunto: ops/reversa_ensayo.sh, ops/reversa_ensayo.py y motor/flota/cerrojo.py. Este último es un módulo independiente de biblioteca estándar; cargarlo por ruta no importa el paquete motor ni ejecuta su entrada. La reversa conserva las generaciones almacenadas.

## Comprobaciones realizadas

- Antes del arreglo, la regresión con trabajador real falló en revertir y en el script: ambos cambiaban la generación ocupada.
- Después: python3 -m unittest tests.test_reversa_flota tests.test_encargo_motor06 -q, 10 pruebas OK, 4.310s. Incluye recuperación y ambas reversas con trabajo vivo, cierre y continuación posterior.
- Reproducción intercalada de recuperación: encargo_reservado_A True; recuperacion ocupada; entorno_A True; marca_real buena.
- Revisor independiente: runtime anterior93d3d2e, supervisor propio muerto e hijo vivo. Antes y después nueva; script devuelve ocupada. Se conserva el árbol sin importar el motor retirado.
- PC nativa: contención del candado entre procesos durante11s; el segundo proceso espera y adquiere después de liberar. Salida0.
- Mini nativa: mismo recorrido durante0.5s. Salida0.
- Los ensayos remotos sólo crearon temporales propios, eliminados al terminar. No alteraron instalaciones ni servicios.
- Ruff limpio en los dos módulos nuevos. En control.py, ciclo.py y test_reversa_flota.py no aumentaron los diagnósticos frente a bf10a83; los avisos anteriores quedan fuera de esta corrección.
- Pre-commit sobre los archivos modificados: detección de secretos y conflictos OK. git diff --check OK.

Los ResourceWarning de las pruebas señalan objetos Popen sin recoger en código previo; se consultaron los PID al finalizar y ya estaban ausentes.

## Alcance pendiente

Este arreglo cierra la conservación de generación en recuperación y reversa. No declara completas las fases3–4: siguen los pendientes de integración y demostración del controlador multicomponente descritos en revision-fases-3-4-ronda-5.md. Tampoco activa la fase5.
