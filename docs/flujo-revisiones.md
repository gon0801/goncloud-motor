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

## Corregir un encargo en curso

`motor corregir` y `motor director corregir` aceptan una corrección en
cualquier estado salvo `cancelado` y `terminado` (o una cancelación en
curso), que la rechazan con su motivo. No interrumpen al agente que corre:
la corrección queda en el registro y la recibe el siguiente intento, del
rol que sea, en su prompt. Para que se aplique de inmediato, se usa `parar`
y `continuar`.

- En `recibido`, `ejecutando` y `conciliando` solo se registra. El reloj del
  último hecho no se toca, así que la detección de una sesión colgada sigue
  igual.
- En `esperando_reintento` solo se registra: la espera sigue su hora, y la
  corrección cuenta como información nueva para las causas que el host no
  puede comprobar.
- En `necesita_usuario` además reabre el trabajo, igual que un ajuste seguido
  de `continuar`. En `detenido` el encargo sigue detenido hasta `continuar`.
- Una corrección que llega durante el intento que iba a cerrar el encargo no
  se pierde. Cada reserva de intento anota cuántas correcciones llevaba su
  prompt (`ajustes`; una reserva de la versión anterior sin ese conteo vio
  todos los ajustes registrados hasta entonces, y la concesión del conductor
  no cambia lo visto). Si hay más registradas que las que vio el último
  intento, el cierre y la entrega del PR no avanzan: el trabajo vuelve al
  implementador, cuyo prompt ya las lleva, y lo aprobado que no cambió se
  conserva. Si ningún intento puede lanzarse (todas las fases ya están
  preparadas), devolver el trabajo bloquearía el cierre para siempre: el
  cierre sigue y deja en las notas de procedimiento qué correcciones nadie
  recibió.
- Solo levanta las causas que el host no puede comprobar. Las demás siguen
  hasta verificarse.
- Una corrección que entra mientras se escribe el cierre tampoco se pierde:
  el cierre anota cuántas correcciones vio y el registro lo rechaza, en la
  misma transacción, si hay más; el trabajo vuelve al implementador. Cuando
  nadie puede recibir una corrección, el aviso de cierre al propietario la
  nombra. En la vía directa se rechaza con su motivo: ningún intento del motor
  la recibiría.
- La misma clave no duplica la corrección. La misma clave con otro texto se
  reporta como `conflicto`.
- `motor director corregir ENCARGO --base B --clave K --texto T --canal C
  [--sesion S]` aplica la misma regla de canal y sesión que
  `director responder`: solo el canal (y la sesión) de origen corrigen el
  encargo.

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
trabajo intacto y sin lanzar más agentes. Un check requerido que estaba en
rojo y pasa a verde al volver a correrse sobre el mismo SHA no espera a una
entrega nueva: el supervisor relee en solo lectura los checks de ese SHA y
anota `checks_verdes`, que cierra el bloqueo, reinicia la cuenta y reabre la
entrega. La lectura se espacia 120 s por SHA (la marca vive en
`checks_lectura.json` del encargo), no corre mientras el agente trabaja y se
fuerza una sola vez por generación y SHA cuando la incorporación del agente
queda pendiente (aunque tarde muchos barridos, por ejemplo mientras corre el
comprobador), para anotar un verde antes de contar la vuelta sin agotar la
cuota de la API de GitHub. El forzado se consume al leer, no al intentar: si
la lectura falla queda pendiente, la causa queda en la marca y se reintenta
a los 15 s. Cerrar un bloqueante o una
aprobación sin nada abierto reinicia la cuenta, también al pasar a la fase
siguiente. Nunca se declara éxito ni se vuelve a crear el encargo.

## Inspecciones y respuestas del propietario

Cuando el revisor no concluye y nombra una inspección, la fase queda ligada a
su solicitud y el revisor no vuelve a correr sin información nueva. La
información nueva es la respuesta del propietario, la resolución de la
solicitud, un ajuste que ningún intento ha visto o un recurso que cambió.

- **La respuesta llega completa.** `director responder` guarda el texto entero.
  Si la solicitud ya tenía resultado (un error, una denegación, una detención),
  la respuesta no lo reescribe: queda en `tardias` y el JSON de
  `.interno/buzon/respuestas/<id>.json` pasa a `estado: respuesta` con la
  respuesta completa, y el estado anterior en `anterior`. El prompt del revisor
  y del responsable lleva cada respuesta de la fase, sin recortar y aunque no
  sea la solicitud ligada.
- **Un ajuste abre el turno una sola vez.** Con la pregunta abierta, con el
  impedimento `inspeccion:<id>` o con el encargo detenido, un ajuste cuenta como
  información nueva. El revisor corre una vez y, al abrirse su intento, el
  ajuste queda visto: si repite el mismo pedido, el gate vuelve a cerrar.
- **`continuar` no deja un `recibido` mudo.** Si la pregunta sigue abierta y
  nada la contestó, el encargo queda en `necesita_usuario` con esa pregunta.
- **Un recurso cambiado no es una repetición.** La identidad de la inspección
  incluye el tamaño, la fecha y el hash de lo que leería el recurso (un
  archivo local o una base sqlite). Pedirlo de nuevo tras subir otro archivo es
  una solicitud nueva.
- **Una solicitud tardía no cuelga la fase.** Una liga a una solicitud que no
  existe no bloquea. Una solicitud detenida antes de despachar queda
  `detenida`, y pedir lo mismo otra vez es una solicitud nueva.

### Revisiones `no_concluyente` repetidas

Una revisión `no_concluyente` que nombra una inspección no cierra ni abre
bloqueantes, así que el freno de `sin_progreso` por bloqueos no la veía. Ahora
el encargo cuenta las revisiones independientes `no_concluyente` seguidas que
piden lo mismo sobre la misma versión: el mismo texto de inspección o el mismo
recurso. La cuenta es propia (`no_concluyentes`) y no toca `sin_progreso`,
la escalera de diagnóstico de los bloqueos. A la tercera
(`TOPE_NO_CONCLUYENTES`) el encargo queda en `necesita_usuario` con la causa
de la última revisión y no gasta otro turno del revisor.

La cuenta empieza de nuevo con otra versión del producto, otro pedido, un
bloqueante cerrado o un check que pasó a verde (aunque ocurra fuera de la revisión), otro veredicto, un ajuste o una respuesta del propietario.
Si el recurso pedido cambió desde la última lectura (otra huella del mismo
archivo), pedirlo de nuevo no cuenta como repetir.

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
severidad Critical, High o Medium nacen bloqueantes (devuelven el trabajo
al implementador con su título, archivo y línea, dentro del mismo encargo),
Low entra como observación visible en `ver` sin abrir otra vuelta, y las
resueltas o descartadas no reabren. Un Medium entra por la misma vía que un
bloqueante de DeepSeek, así que cualquier regla de avance o de vueltas sin
progreso que cubra a estos lo cubre también. Sin bloque
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

### Rama de fase y fallos de git

Un encargo que empieza sin fases trabaja en la rama `encargo/<id>`. Si las
fases se declaran después, la primera (la que no depende de ninguna otra) usa
`encargo/<id>-<fase>`. Cuando el espacio sigue en la rama sin fase del mismo
encargo, el supervisor la renombra con `git branch -m` en vez de crear la rama
de la fase desde la base. Así se conserva el trabajo, con o sin commit, y un
merge que solo vivía en esa rama. Una rama distinta, una fase que depende de
otra o una rama de fase que ya existe siguen el camino de siempre.

Si el repo no queda listo (clonado, rama o remoto), el encargo espera 5
minutos y reintenta. El mismo fallo tres veces seguidas
(`TOPE_FALLOS_REPO`), sin nada distinto entre ellos, deja de reintentar:
queda en `necesita_usuario` con la causa. No abre una solicitud, así que
`director responder` no tiene a qué contestar y `director continuar` solo
vale desde `detenido`. Las salidas son un ajuste (`director corregir`) o
`director parar` y después `director continuar`; la pregunta las nombra. Las
dos devuelven el trabajo y empiezan la cuenta de nuevo.

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
