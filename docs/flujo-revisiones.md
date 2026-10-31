# Flujo de revisiones del motor durable

El motor ejecuta el plan del usuario con poteto-mode, revisión de otro modelo
y, cuando el encargo lo autoriza, revisión de DeepSeek en el PR. Las
correcciones conservan el trabajo aprobado. Un error pequeño no reinicia la
implementación ni provoca otra revisión completa.

Un solo `Encargo` y su log de eventos son la fuente de verdad. El juicio del
revisor ya no es una clase global: es una **referencia de revisión persistida**
en el fold — versión del producto (firma + manifiesto por archivo), hallazgos
tipados con id estable y rutas aprobadas. Todo lo demás se deriva de ahí.

## El recorrido

1. El usuario entrega texto o un archivo. No se exigen bloques ejecutables ni
   comandos de prueba previos.
2. El supervisor lanza al responsable con poteto-mode completo. La carga se
   verifica antes de cada lanzamiento (`exigir_poteto` en la flota) y el digest
   del `SKILL.md` queda en el evento `intento_abierto`: si poteto falta, el
   encargo espera visible con la causa. Nunca corre sin poteto en silencio.
   En la vía de respaldo local (`~/.agents/skills/poteto-mode`) se copia el
   árbol completo de skills hermanas a `runtime/skills/` y se devuelve
   `runtime/skills/poteto-mode/SKILL.md`, para que las referencias a skills
   hermanas y principios resuelvan dentro del runtime.
3. El responsable escribe solo producto en el espacio. Sus archivos internos
   van en `espacio/.interno/` (`contexto.md`, `evidencia.txt`, y opcionales
   `comprobaciones.json` y `pedido-efecto.json`). La firma del producto excluye
   `.interno/` (y `.git`, `__pycache__`, `.runtime`) por parte de ruta, nunca
   por nombre: el espacio fuera de `.interno/` es solo producto.
4. Al morir el proceso, el supervisor anota `producto_listo` con la firma y el
   manifiesto que él calcula. Si hay comprobaciones declaradas, las ejecuta
   enjauladas y las anota como `observacion_supervisor`. Declarar ninguna es
   válido: para cambiar una cifra basta leer el valor final y dejarlo en
   `evidencia.txt`. No hay `procedimiento.json` obligatorio ni cobertura
   forzada.
5. Otro modelo revisa. "Otro" es por identidad `proveedor/modelo`, derivada de
   las cadenas de la política común (`codigo` implementa, `planear` revisa) y
   excluyendo los modelos que ya implementaron en este encargo. Una sesión
   distinta con el mismo modelo no cuenta como revisión independiente: si no
   hay modelo distinto disponible (por ejemplo, una sola cadena codex en PC),
   el encargo espera visible, jamás se auto-revisa.
6. El revisor entrega juntos sus hallazgos. **Bloquear es un tipo, no texto**:
   cada hallazgo explica su impacto real y la evidencia que lo sostiene.
   Un bloqueante identifica un incumplimiento del pedido o un riesgo concreto
   sin resolver y cómo reproducirlo. Se clasifica como seguridad, datos,
   comportamiento, comprobación necesaria o problema operativo. Las pruebas
   y revisiones se eligen por la evidencia útil que aportan al comportamiento
   y al riesgo del cambio. El código valida el formato del juicio. Una revisión
   adicional se justifica por lo que permite comprobar, sin repetir evidencia
   válida por rutina.
7. Con el nivel de entrega autorizado, el cierre decide (§ Niveles).

## Correcciones

La primera revisión es completa. Tras una corrección, el alcance se deriva del
fold: hallazgos abiertos + rutas cuyo hash cambió desde la versión revisada.
Las rutas aprobadas cuyo hash no cambió **conservan su aprobación sin
re-revisarse**. El revisor puede ampliar a una ruta no tocada si declara el
motivo causal, y puede abrir un bloqueante nuevo grave con criterio y
reproducción. No puede reabrir lo conservado por gusto: el código degrada
bloqueantes nuevos sobre rutas conservadas fuera de lo cubierto.

La sesión del implementador se conserva cuando el runtime lo permite: grok
reusa su `--session-id` por rol; codex ya no corre `--ephemeral` — se captura
el id de hilo de su salida `--json` y las vueltas siguientes reanudan con
`codex exec resume`. Como cada intento corre con un `HOME` nuevo, el
supervisor guarda el estado de sesión del CLI (los rollouts de `.codex`, el
equivalente de `.grok`) en un almacén por encargo y rol fuera de la jaula, y
lo restaura antes de cada intento; sin esa copia, `resume` no encuentra el
hilo. La autenticación no entra al almacén. Un error del proveedor al
reanudar (cuota, 429, rate limit, auth, modelo o transitorio) no borra la
sesión ni su almacén: sigue el camino `proveedor_fallo` y conserva el
contexto. Solo es `sesion_perdida` una reanudación cuyo mensaje afirma que
falta la sesión o el rollout (por ejemplo `no rollout found`,
`thread/resume failed`, `session not found`, `no session found` o
`conversation not found`); solo entonces se borra el almacén del rol, se
olvida el id y la siguiente vuelta corre en modo `reconstruida`, declarado
en `intento_abierto`, con el prompt armado desde el fold (pedido,
hallazgos abiertos, referencia de revisión). Reconstruir es válido; fingir
continuidad, no.

## Errores de instrumento y repetición sin progreso

Un fallo que no es del producto se anota como `instrumento_fallo` por fuente
(comprobación, juicio, adaptador, github, sesión, poteto) y reintenta **el
paso que falló**:

- JSON del juicio inválido o aprobación mal formada: nueva sesión del revisor
  sobre la misma versión, con el error de forma en su prompt. El producto ni
  se entera.
- Un argv de comprobación roto (por ejemplo un `SyntaxError` del `-c`) o un
  `comprobaciones.json`/`pedido-efecto.json` inválido: el responsable repara
  ese archivo interno; no se rehace un producto válido.
- Fallo de `gh` o de red: espera y reintento del paso de entrega, idempotente
  por paso + SHA.

Un `instrumento_fallo` repetido no escala por conteo: el tercer fallo
idéntico no pasa el encargo a `necesita_usuario` por sí solo. Reintenta el
mismo paso y el encargo sigue su curso.

No hay tope de rondas mientras haya avance. Una vuelta es avance solo si
cierra al menos un bloqueante: del revisor independiente, de DeepSeek o de
los checks requeridos del PR. Cambiar archivos no es avance, y la firma del
producto no entra en la cuenta. Que aparezca un bloqueante nuevo junto con
uno cerrado sí es avance; uno nuevo sin cerrar ninguno no, y volver a cerrar
algo que ya se cerró y se reabrió (por ejemplo un check que alterna rojo y
verde) tampoco. El primer bloqueante que se ve no tiene con qué compararse y
cuenta como avance.

Una vuelta sin avance incluye al revisor que no concluye (`no_concluyente`
sin inspección faltante), al implementador que cambia archivos con el mismo
bloqueante abierto, a los checks requeridos que siguen en rojo y a DeepSeek
que mantiene el mismo bloqueante tras republicar el PR. La primera vuelta sin
avance no repite: el responsable diagnostica por qué la corrección no lo
resolvió. La segunda seguida deja el encargo en `necesita_usuario` con el
diagnóstico concreto (qué bloqueante sigue abierto y qué se intentó), el
trabajo intacto y sin lanzar más agentes. Cerrar un bloqueante o una
aprobación sin nada abierto reinicia la cuenta, también al pasar a la fase
siguiente. Nunca se declara éxito ni se vuelve a crear el encargo.

## Niveles de entrega

El nivel se fija al encargar (`--entrega`), no a mitad de camino. Los niveles
`pr`, `integrado` y `desplegado` exigen `--repo` y `--base-rama` desde el
inicio. `terminado` siempre distingue nivel autorizado y nivel alcanzado: el
encargo termina solo cuando el alcanzado iguala al autorizado.

- `terminado` (defecto): revisión independiente aprobada sobre la versión
  vigente + recibos de los efectos pedidos. No se toca GitHub.
- `pr`: además, el espacio es un clon del repo en la rama del encargo y el
  supervisor, fuera de la jaula y sin sesiones LLM, hace commit del producto
  (sin `.interno/`), push, abre el PR con `motor/github.py`, lo marca listo
  (la revisión automática ignora borradores) y espera la revisión de DeepSeek
  para ese SHA. Cada hecho es un evento `entrega_externa`.

Antes de publicar o de cerrar, el supervisor vuelve a calcular la versión
del producto y la compara con la referencia aprobada y con el producto
registrado. Si el árbol cambió tras la aprobación, no hace commit ni abre
PR ni compone el cierre: anota `producto_listo` con la versión vigente y
pide re-revisión al revisor. Si el producto ya tiene un SHA publicado, el
artefacto es ese commit y un cambio local posterior no se convierte en
otro artefacto.
- `integrado`: con DeepSeek leído sin bloqueantes, merge con
  `--match-head-commit` sobre el SHA revisado.
- `desplegado`: solo se marca si ya se despachó con recibo un efecto de
  despliegue declarado en `.interno/pedido-efecto.json` (operación
  `desplegar`); si no, el encargo espera visible. No hay un desplegador
  genérico.

Solo cuentan como revisión de DeepSeek los comentarios de la integración
real: autor `github-actions[bot]`, marca `<!-- ai-review:sticky -->` y SHA
del comentario igual al SHA pedido. El texto de un comentario o que su
autor contenga «deepseek» no acreditan origen.

El parseo de DeepSeek usa primero el bloque oculto
`ai-review:findings=[...]` cuando lo trae: las entradas abiertas con
severidad Critical o High nacen bloqueantes, Medium y Low entran como
observaciones, y las resueltas o descartadas no reabren. Sin bloque
estructurado, bloquean las señales textuales reales por línea
(`CHANGES_REQUESTED`, `REQUEST_CHANGES`, `[P0]`, `[P1]` y las prohibitivas
de siempre); todo lo demás entra como observación visible. Los bloqueantes
de DeepSeek son hallazgos del mismo encargo y usan la misma vuelta de
corrección; sus correcciones solo reabren al revisor interno si tocan rutas
que él había aprobado.

Un repo sin los workflows `ai-review-worker.yml` / `ai-review-publish.yml` o
sin secretos no finge una revisión leída. La espera de DeepSeek no vence:
el encargo sigue esperando ese SHA, con la recuperación por dispatch del
workflow, sin escalar a `necesita_usuario` por tiempo.

El puerto GitHub del supervisor se configura en `github.json` de la base:
`{"real": true}` usa `gh` de verdad; `{"falso": "/ruta"}` usa `FakeGitHub`
para ensayos y repos locales. Sin puerto, los encargos con nivel `pr` o más
esperan visibles en el paso de entrega.

## Ejemplos

```sh
# Sin repo: termina con la revisión independiente aprobada.
python3 -B -m motor encargar --base /tmp/ensayo --clave saludo-1 \
  --texto "Crea saludo.txt con el texto exacto: hola, motor"

# Con repo y PR autorizado.
python3 -B -m motor encargar --base /tmp/ensayo --clave fix-1 \
  --entrega pr --repo gon0801/goncloud-motor --base-rama main \
  --archivo plan.md
```

`ver` muestra producto (archivos y firma), modelos de implementador y revisor,
revisión vigente (veredicto, bloqueantes abiertos, observaciones) y entrega
(nivel autorizado, alcanzado, PR y SHA de DeepSeek leído).

## Compatibilidad

Los eventos históricos (juicios por clase global, `procedimiento`,
`sin_revisor`) se siguen leyendo: el reducer ignora lo retirado y ningún fold
viejo se rompe. La vía `--sin-revisor` se retiró: su aprobación (rc 0 +
cobertura auto-declarada) contradecía la revisión independiente obligatoria.
Queda una sola vía de cierre.
