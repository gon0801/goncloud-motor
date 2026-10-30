# Corregir el flujo de implementación y revisión del motor

El motor debe ejecutar el plan del usuario con poteto-mode, revisión de otro modelo y revisión de DeepSeek en el PR. Las correcciones deben conservar el trabajo aprobado. Un error pequeño no debe reiniciar la implementación ni provocar otra revisión completa.

Este plan amplía la corrección de fase 5. Conserva los arreglos anteriores y la [decisión de máquinas independientes](/Users/dn/dev/goncloud-motor-encargos-5/docs/decision-maquinas-independientes.md).

## Qué está mal

El flujo revisado exige archivos de procedimiento y una revisión de cobertura para cualquier encargo. Si falla ese protocolo, devuelve el trabajo al implementador. En PC abre sesiones efímeras para los intentos, lo que obliga a reconstruir contexto.

También mezcla archivos internos con el producto. En un caso observado, el archivo solicitado tenía el texto correcto, pero la revisión rechazó los archivos auxiliares que el propio motor había exigido.

La revisión independiente debe mantenerse. Hay que corregir qué revisa cada vuelta, cómo conserva su resultado y qué errores justifican devolver trabajo.

## Flujo acordado

1. El usuario entrega un pedido o plan. El motor lo recibe sin exigir bloques ejecutables ni comandos de prueba previos.
2. El implementador carga siempre poteto-mode completo. Investiga, implementa y comprueba el resultado con las herramientas adecuadas al trabajo.
3. Otro modelo revisa el cambio completo contra el pedido. Entrega juntos los hallazgos y distingue bloqueantes de observaciones.
4. Si hay bloqueantes, el implementador los corrige. Conserva el producto, contexto y comprobaciones que siguen siendo válidos.
5. El revisor comprueba esas correcciones y sus efectos. Aprueba cuando el pedido se cumple y no quedan bloqueantes.
6. Después de la aprobación, se abre el PR y se lee la revisión de DeepSeek. Esta revisión también es obligatoria.
7. Se corrigen los bloqueantes de DeepSeek. Sus revisiones posteriores comprueban el cambio desde su revisión anterior y los hallazgos pendientes.
8. Con las revisiones resueltas, el trabajo continúa hasta la entrega que autorice el encargo. Abrir un PR, integrarlo y desplegarlo son resultados distintos; el motor informa cuál realizó.

La validación del formato y de los recibos corresponde al código del motor. Las revisiones adicionales se eligen cuando aportan evidencia útil al pedido o a un riesgo concreto.

## Qué puede bloquear

El revisor explica el impacto real y la evidencia de cada hallazgo. Para bloquear, identifica qué parte del pedido no se cumple o qué riesgo concreto queda sin resolver y cómo reproducirlo.

Una preferencia sin impacto demostrado no impide aprobar. Un documento puede bloquear si incumple lo solicitado o contiene un error operativo demostrable. La aprobación no exige eliminar todas las observaciones.

Las pruebas, verificaciones y revisiones se eligen por la evidencia útil que aportan al comportamiento solicitado y al riesgo del cambio. Una prueba permanente puede conservar esa evidencia para cambios futuros; una observación directa puede bastar cuando demuestra el resultado. La elección explica qué se comprueba y qué incertidumbre resuelve.

## Cómo se revisan las correcciones

El motor conserva la versión revisada, los hallazgos y el resultado de la revisión. La siguiente vuelta recibe esa referencia y los cambios posteriores.

El revisor comprueba los bloqueantes pendientes y las partes afectadas por las correcciones. Lo aprobado que no cambió conserva su aprobación. Amplía o repite la revisión cuando un cambio, un riesgo o información nueva lo justifiquen.

Una corrección puede afectar una parte que no se editó directamente. El revisor amplía su revisión cuando identifica esa relación y explica el motivo. También puede señalar un problema grave nuevo respaldado por evidencia concreta.

La misma regla se aplica a DeepSeek. Sus correcciones no reinician automáticamente la revisión inicial. Solo requieren volver al revisor independiente cuando afecten algo que este había aprobado.

## Cómo se evita repetir trabajo

Si falla un comando de comprobación, el implementador corrige ese comando y vuelve a observar el resultado. No rehace un producto válido. Un error de JSON o del adaptador tampoco se clasifica como defecto del producto.

Los archivos de contexto, procedimientos y recibos internos se guardan fuera de la entrega del usuario. No cuentan como archivos adicionales del producto.

El motor conserva la sesión del implementador durante las correcciones cuando el runtime lo permite. Tras una interrupción, recupera el pedido, los hechos y el trabajo pendiente. Mantiene poteto aplicado sin obligar a reconstruir toda la sesión en cada vuelta.

Ante el mismo bloqueo sin información nueva, el responsable investiga por qué la corrección no lo resolvió. No repite la misma acción a ciegas. Si no puede avanzar, informa la causa concreta y conserva el trabajo para continuar desde ahí. No declara éxito ni vuelve a crear el encargo para empezar de cero.

Este plan no fija un número de rondas. Corrige la repetición sin progreso y la revisión innecesaria de trabajo ya válido.

## Qué implementar

Trabaja sobre el estado vigente de `/Users/dn/dev/goncloud-motor-encargos-5`. Consulta los cambios en curso y conserva el avance de Muse antes de editar.

1. Ajusta el cierre del implementador y el cambio de rol en `motor/encargos/ciclo.py` y `comprobacion.py`. Permite comprobaciones proporcionales al pedido y separa los errores internos de los defectos del producto.
2. Asegura la carga de poteto-mode y la selección de modelos distintos para implementar y revisar. Usa la política común y las suscripciones configuradas.
3. Conserva las referencias de revisión y los hallazgos entre correcciones y reinicios. Genera la siguiente revisión a partir del cambio y sus efectos, sin reiniciar el recorrido completo.
4. Conecta la apertura del PR tras la aprobación y la lectura de la revisión real de DeepSeek. Reutiliza su integración existente, incluidos los workflows `ai-review-worker.yml` y `ai-review-publish.yml` donde correspondan.

Conserva las libretas independientes, la parada, la recuperación, las generaciones de herramientas y los recibos que evitan repetir efectos. Este cambio no requiere rediseñar esas partes.

## Cómo comprobar que quedó bien

Comprueba el recorrido por las entradas que usa el usuario. Un cambio pequeño debe producir una implementación con poteto, revisión de otro modelo, PR y revisión real de DeepSeek.

En una corrección, muestra qué hallazgo se resolvió y qué cambió. La siguiente revisión debe concentrarse en esa corrección y sus efectos. Lo aprobado anteriormente debe conservarse cuando siga siendo válido.

Comprueba también los problemas ya observados: un fallo del instrumento no rehace la implementación y los archivos internos no contradicen una entrega que pide un solo archivo. Una observación no bloqueante no abre otra vuelta.

Elige el alcance de las comprobaciones por esos comportamientos y sus riesgos. Conserva los resultados válidos y repite una comprobación cuando un cambio o información nueva puedan afectar su conclusión.

## Entrega

Deja los cambios listos para revisión con una explicación de lo corregido y la evidencia del recorrido. Identifica los modelos usados, las versiones revisadas, los hallazgos resueltos, el PR y la revisión de DeepSeek. Distingue lo comprobado de lo pendiente.

Actualiza la documentación del motor para que describa este flujo.
