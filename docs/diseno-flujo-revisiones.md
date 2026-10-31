# Diseño: flujo de revisiones del motor durable

Implementa [plan-flujo-revisiones.md](diseno-motor-encargos/plan-flujo-revisiones.md).
El comportamiento vigente se describe en [flujo-revisiones.md](flujo-revisiones.md);
este documento registra las decisiones de forma y por qué.

## La idea en una línea

El `Encargo` sigue siendo el agregado único y su log de eventos la fuente de
verdad. El juicio dejó de ser una clase global y pasó a ser una **referencia de
revisión persistida en el fold**: versión del producto (firma + manifiesto por
archivo), hallazgos tipados con id estable y rutas aprobadas conservadas. De
esa referencia se derivan el cierre proporcional, la re-revisión acotada, la
entrega externa y la anti-repetición, sin estado lateral.

## Decisiones que sostiene el diseño

- **Bloquear es un tipo, no texto** (model-the-domain). Un hallazgo solo nace
  bloqueante con criterio de la lista de David y reproducción; el código lo
  degrada a observación si falta cualquiera de los dos. Sin tercer agente.
- **Una sola vía de cierre.** Se retiró `--sin-revisor` (aprobaba con rc 0 +
  cobertura auto-declarada). Se conserva de ese diff solo la extracción de
  `_cerrar_con_juicio` como único punto de cierre. Los eventos históricos con
  `sin_revisor` se ignoran al leer.
- **Internos fuera por posición, no por nombre.** `espacio/.interno/` guarda
  contexto, evidencia, comprobaciones opcionales y pedidos de efecto. La
  firma excluye `.interno` (y `.git`, `__pycache__`, `.runtime`) por parte de
  ruta; no hay lista de nombres sincronizada con los prompts. Se eligió la
  subcarpeta interna sobre una segunda raíz en la jaula para no tocar el
  aislamiento de macOS y Windows.
- **Poteto garantizado en código, no en prompt.** Preflight con digest en
  `intento_abierto`; si falta, espera visible. El reducer rechaza un intento
  del responsable cuyo evento trae el digest vacío.
- **Otro modelo definido por identidad `proveedor/modelo`.** La exclusión del
  autor recorre toda la chain de la política común (`codigo`/`planear`);
  `vincular` respeta el modelo ya elegido si está en la chain. Sin segundo
  modelo disponible el encargo espera; nunca se auto-revisa.
- **Sesión conservada sin fingir.** Cada intento corre con un `HOME` nuevo,
  así que el supervisor guarda el estado de sesión del CLI (rollouts de
  `.codex`, sesiones de `.grok`) en un almacén por encargo y rol fuera de la
  jaula y lo restaura antes de lanzar; la autenticación no entra al almacén.
  Si la reanudación muere, el id se olvida (`sesion_perdida`, que también
  sube la época de la que se deriva el id de sesión de Grok y borra el
  almacén) y la siguiente vuelta corre `reconstruida` desde el fold.
- **Errores internos separados del producto** (boundary-discipline). Todo
  fallo de instrumento/JSON/adaptador reintenta el paso que falló, con
  eventos idempotentes por paso+SHA en la entrega (make-operations-idempotent).
  Un `instrumento_fallo` repetido no escala por conteo: reintenta el mismo
  paso y no toca `sin_progreso`. Solo la anti-repetición de abajo escala.
- **Anti-repetición sin tope.** Sin número fijo de rondas: progreso = se
  cierra un bloqueante (revisor independiente, DeepSeek o checks requeridos).
  Cambiar archivos no cuenta. Sin progreso, primero diagnostica el
  responsable; la segunda vuelta seguida sin progreso es `necesita_usuario`.

## Alternativa considerada y rechazada

Delegar la entrega (PR/DeepSeek/merge) al motor por planes (`machine.py` /
`effects.py`), que ya sabe hacerlo. Perdió por dos razones: crea dos fuentes
de verdad para el mismo trabajo y obliga a fabricar un plan sintético por
cada entrega y corrección, sobre una máquina pensada para planes con bloques
y `Acepta:`. El diseño elegido reusa solo `motor/github.py`, que ya es un
puerto limpio (`abrir_pr`, `marcar_listo`, `comentarios_deepseek`, `merge`),
y orquesta desde el mismo encargo con eventos `entrega_externa`.

## Tradeoffs aceptados

- El fold es más pesado (manifiesto + hallazgos por revisión) a cambio de una
  conservación de aprobaciones mecánica y auditable.
- Un encargo puede quedar esperando un segundo modelo a cambio de no llamar
  «independiente» a una auto-revisión.
- Siguen dos sesiones por vuelta a cambio de revisión real; el ahorro sale
  de no repetir por errores de forma ni re-revisar lo ya válido.
- DeepSeek se parsea conservador (bloquea solo con señal prohibitiva no
  negada, por línea) porque su comentario no trae severidades legibles por
  máquina en este repo.
- Un encargo histórico `directa` con nivel externo se rechaza al volver a
  plegar su evento de entrega; la vía directa nueva con nivel externo también
  se rechaza al encargar, y ningún camino de cierre termina un encargo sin
  su nivel. Si existiera un registro histórico con esa combinación, hay que
  migrarlo antes de volver a leerlo con esta validación.

## Comprobación

Recorridos del diseño (tests/test_flujo_revisiones.py y focalizadas de
encargos/servicio/migración/flota) y recorrido completo por la CLI con
agente de ensayo: un archivo aprobado con una observación en 3 pasos del
supervisor, sin protocolo obligatorio. La sesión de Codex se comprobó con
el CLI real: sin restaurar el almacén, `resume` responde «no rollout
found»; restaurado, reanuda y continúa el mismo hilo. Los fallos de la
suite completa observados en esta Mac se reproducen idénticos en HEAD
`46be739` sin este diff, y domina un componente ambiental por sesiones
`pytest` concurrentes (por eso el conteo alternó entre corridas); no son
regresiones de este cambio.
