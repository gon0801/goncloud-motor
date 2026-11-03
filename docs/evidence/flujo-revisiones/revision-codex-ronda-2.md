# Segunda revisión de las correcciones del flujo

**APROBADO para los hallazgos de la ronda anterior.** Las ocho reproducciones quedan corregidas en el código revisado. Se retiraron también los dos cortes numéricos no acordados. Esta aprobación no acredita un despliegue ni un recorrido nuevo de producción.

Se revisaron únicamente los cambios posteriores a la primera revisión y sus efectos. El resto conserva la revisión anterior. La rama es `fix/flujo-revisiones`, sobre `46be73974e388f0f28fcacb06921a7c08e05b7f2`, con cambios sin commit.

La copia estable de esta ronda está en `/var/folders/hp/sc_3c6qs7nvdswl_x3w5sdbr0000gn/T/motor-revision-flujo-r2-6i54ip1d`, con las huellas en `revision-snapshot.json`. Los archivos coincidían con el worktree al comprobar su estabilidad.

## Hallazgos cerrados

| Hallazgo anterior | Comportamiento comprobado |
| --- | --- |
| Publicar otro producto con aprobación vieja | Un cambio posterior a la aprobación reabre la revisión antes de commit, push o apertura de PR. |
| Comentario ajeno presentado como DeepSeek | Se rechaza el autor falso incluso si copia las marcas completas y el SHA. |
| Bloqueantes de DeepSeek ignorados | Reconoce la señal textual de cambios pedidos y los hallazgos estructurados. Incluye el formato `files` en plural. |
| Bloqueante borrado sin resolver | La respuesta incompleta conserva el hallazgo previo. El producto con `99` no termina cuando se pidió `42`. |
| Nota de estilo devuelve implementación | Con evidencia suficiente y sin bloqueantes, aprueba conservando la observación. |
| Ampliación causal descartada | El bloqueo sobre el archivo intacto se conserva cuando la ampliación explica su relación con el archivo modificado. |
| Cuota borra contexto | El error 429 conserva sesión y almacén, y se registra como fallo del proveedor. |
| Poteto local incompleto | El respaldo copia también las skills hermanas; las referencias comprobadas están disponibles. |

Los cambios eliminan el corte por tres errores internos y el vencimiento de treinta minutos para DeepSeek. No se sustituyeron por otro límite de rondas.

## Verificación ejecutada

Desde la copia estable:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_flujo_revisiones.CorreccionesRevisionFlujo -q
```

Resultado: nueve pruebas, OK. No se repitió la suite completa ni los recorridos que no cambiaron.

Se ejecutaron además las tres reproducciones originales de estado sobre el código corregido, conservando sus entradas y cambiando las aserciones para exigir el comportamiento correcto:

```text
AMPLIACION_CAUSAL: cambios bloqueante ()
SOLO_OBSERVACION: terminado revisor revisar_evidencia aprobado
BLOQUEANTE_SIN_RESOLVER: ejecutando cambios contenido=99 abiertos=1
```

Una revisión independiente del cambio de confianza y lectura de DeepSeek confirmó el rechazo del comentario falso con marcas completas, la clasificación del caso de fuga y la lectura de `files` con línea. Sus dos comprobaciones focalizadas pasaron, sin red ni servicios.

Estos resultados verifican los defectos concretos corregidos. No confirman por sí solos todas las afirmaciones generales de que nunca pueda existir otro defecto.

## Estado y límites

Los cambios siguen sin commit, push ni despliegue. Esta ronda no ejecutó llamadas reales de modelos ni una nueva revisión de GitHub. Conserva la distinción entre aprobar estas correcciones y comprobar después el artefacto integrado o instalado. La afirmación de la corrida de 39 pruebas procede de la entrega de Muse; no se repitió por rutina.

`git diff --check` detectó una línea vacía adicional al final de `motor/encargos/comprobacion.py`. Es una observación de formato, no un bloqueante ni motivo para otra ronda de implementación.

No se modificaron código, servicios, cuentas ni bases operativas durante la revisión.
