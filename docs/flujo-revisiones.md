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
   por nombre: el espacio fuera de `.interno/` es solo producto. En un
   repositorio git tambien excluye lo que git ignora, con el mismo criterio con
   el que se decide que entra en un commit; una cache local no cambia la firma.
4. Al morir el proceso, el supervisor anota `producto_listo` con la firma y el
   manifiesto que él calcula. Si hay comprobaciones declaradas, las ejecuta
   y las anota como `observacion_supervisor`. Declarar ninguna es
   válido: para cambiar una cifra basta leer el valor final y dejarlo en
   `evidencia.txt`. No hay `procedimiento.json` obligatorio ni cobertura
   forzada.
   Las comprobaciones se lanzan como cualquier trabajador: directo y sin jaula
   (decisión del 2-nov). Cada una corre con el `PATH` antepuesto al intérprete
   que usa el propio supervisor (`aislamiento.path_con_interprete` en POSIX;
   en Windows, la carpeta del ejecutable de Python): el primer `python3` del
   PATH puede ser el del sistema (el 3.9 en la Mini, sin `tomllib`) y el
   comprobador necesita el de la instalación del supervisor.
   Cada comprobación puede declarar `tope` en segundos enteros; sin declararlo
   vale 120 s y el máximo admitido es 900 s. Un tope mayor no valida. Al pasarse,
   el fallo del instrumento nombra el comando, su tope, la salida parcial
   recortada y la alternativa de partir la comprobación o declarar un tope
   mayor dentro del máximo. El cierre del árbol y la clasificación
   como fallo del instrumento no cambian.
   Cada corrida del comprobador y cada sesión de agente reciben un `TMPDIR`
   propio, vacío, dentro de su runtime; se borra al terminar. Las pruebas de
   shell de `ops/tests` corren en el job `ops-tests`; una que no pueda correr
   en Linux se omite con su motivo en `ops/tests/omitidas.txt`.
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
`codex exec resume`. Como el runtime de cada intento es nuevo y efímero, el
supervisor guarda el estado de sesión del CLI (los rollouts de `.codex`, el
equivalente de `.grok`) en un almacén por encargo y rol en la base, fuera del
runtime del intento, y
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

Al empezar otra fase, el responsable y el revisor estrenan sesión: se olvidan
los ids guardados, las dos épocas cambian (el id de sesión sale de la época) y
el lanzamiento no reanuda la conversación anterior. El prompt se arma desde el
fold con el pedido, los ajustes, las fases cerradas con su resultado y los
hallazgos abiertos.

## El CI corre en el PR real

Con entrega `pr`, el encargo no pide CI antes del PR. Cuando el revisor
independiente aprueba (y DeepSeek, donde revisa el PR, deja de pedir cambios),
el supervisor hace commit y push, abre o actualiza el PR real y termina. El
aviso de cierre dice «PR abierto; el CI corre en el PR».

El CI corre con los workflows normales del repo, los mismos que verá quien
decida el merge. El supervisor lee su resultado cuando termina; no lo espera
para cerrar y no reintenta en bucle. Un pendiente o un verde no detienen nada.
Un rojo deja un aviso en palabras llanas para el propietario: qué falló, si es
del código del cambio o de la tubería, y qué conviene hacer. Si es una prueba
del producto, además devuelve el log al mismo responsable una sola vez, para
corregir en la misma rama y el mismo PR, y el aviso recomienda no hacer nada.
Si es un fallo de la tubería (secretos o Gitleaks, cancelación, tope de tiempo,
GitHub caído o falta de minutos), el motor relanza ese CI una sola vez con
`gh run rerun --failed`. Si la segunda corrida pasa, la falla era pasajera y no
hay aviso; si vuelve a fallar igual, es un problema fijo y el aviso recomienda
mergear y abrir un encargo para arreglar la tubería. Un fallo que no se puede
clasificar con certeza lo dice y recomienda pedirle a Claude que lo revise.
Nunca hay más de un reintento.

El reparto de la batería en cuatro partes y el filtro por rutas del workflow
siguen igual: cada job declara de qué rutas depende y el resto se salta, con
el filtro en el `if` de cada job y no en `paths` del disparador.

## DeepSeek es una compuerta aparte

DeepSeek no va antes de Opus: revisa el PR como una compuerta previa al merge.
Después de que Opus aprobó, si DeepSeek pide cambios (High o Medium), el
responsable corrige y el motor espera su nueva lectura sobre el SHA nuevo. Ese
ciclo es solo entre el responsable y DeepSeek: Opus no vuelve a revisar cada
vuelta, y no se integra ni se cierra nada mientras DeepSeek tenga un
bloqueante abierto.

Cuando DeepSeek ya no tiene High ni Medium abiertos, Opus hace una sola
revisión final de lo que cambió desde su última aprobación. Es una revisión de
la corrección acumulada, con el alcance `correccion` y la versión aprobada como
base; si el producto no cambió, el tramo está vacío y no hay revisión final.
Ninguna corrección se integra sin que Opus haya visto ese tramo final.

Si el responsable sostiene con evidencia que un bloqueante de DeepSeek no
aplica, lo deja en `.interno/disputa.json`
(`{"hallazgo": "<id>", "evidencia": "<por qué no aplica>"}`). La disputa no
cuenta como vuelta sin progreso: pasa al revisor independiente, que la decide
con esa evidencia. Si la acepta, el hallazgo se descarta y la razón queda en el
PR; si la rechaza, vuelve al responsable como cualquier hallazgo.

Un hallazgo externo que el revisor declara fuera de alcance, o que señala
archivos que una restricción expresa del encargo deja fuera, no reabre la
revisión ni bloquea. Queda en el cuerpo del PR, en la sección «Hallazgos fuera
de alcance», con su id y la razón. Un Critical, High o Medium dentro del
alcance sigue bloqueando hasta corregirse o hasta que el revisor lo descarte
con evidencia.

La espera de DeepSeek tiene un tope de 30 minutos. Si el revisor independiente
ya aprobó y el tope vence sin comentario, el motor deja la nota «sin revisión
de DeepSeek tras N min» en el PR y sigue la fase como si no hubiera hallazgos:
integra o despliega según el nivel autorizado y pasa a la fase siguiente. No
fuerza el cierre del encargo. Si después llega un Critical, High o Medium
dentro del alcance para un SHA ya integrado, se lee como hallazgo tardío y no
se pierde, aunque el encargo ya haya terminado. El cierre mira los PR de todas
las fases con entrega externa, no solo el de la fase activa, y sigue
releyendo el mismo SHA: una revisión limpia puede cambiar después y traer un
hallazgo, y una pregunta ya emitida en una fase no detiene la lectura de las
siguientes. Durante los primeros 7 días se relee cada minuto y después cada
30 minutos; los errores también se espacian. Una lectura limpia, una pregunta
ya emitida o una ventana vacía no cierran el SHA. La ventana total es de 30
días: al vencer se hace una última lectura y se deja de consultar ese SHA para
no hacerlo de por vida. Si esa última lectura falla, no se da por final sin
leer: se reintenta y, tras tres fallos seguidos, sí se deja. Los bloqueantes de
una lectura anterior no impiden que el SHA omitido avance.

Un ajuste del propietario es la respuesta a una decisión tardía: consume la
pregunta conservada y el cierre reconoce la misma clave para no volver a
preguntarla.

El sticky de `ai-review` puede declarar el timeout antes de esos 30 minutos:
si `ai-review:completion` dice `timeout` con `retry: false` (o lo dice la frase
de 900 s y «no se reintenta»), el motor no espera ni finge una aprobación. Lo
trata en la misma pasada como revisión ausente: deja la nota y sigue la fase.
Si el mismo comentario trae un Critical, High o Medium dentro del alcance,
manda el hallazgo y sigue bloqueando.

## Corregir un encargo en curso

`motor corregir` y `motor director corregir` aceptan una corrección en
cualquier estado salvo `cancelado` (o una cancelación en curso), que la
rechaza con su motivo. Un encargo `terminado` con su PR abierto se reabre en
la misma rama y el mismo PR, después de comprobar en GitHub que el PR sigue
abierto; integrado, desplegado, sin PR, o con el PR mergeado, cerrado o
ilegible se rechaza. No interrumpen al agente que corre:
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
- Un ajuste puede declarar cerrados hallazgos con `--cierra ID` (repetible) y
  `--evidencia TEXTO`. También se leen detrás de `cierro`/`cierra` en el
  texto, por id, ancla o título. El cierre aparece en el prompt del revisor y
  un hallazgo cerrado por el propietario no se reabre con la misma
  reproducción: solo una evidencia nueva lo vuelve a abrir.

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
cierra al menos un bloqueante: del revisor independiente o de DeepSeek.
Cambiar archivos no es avance, y la firma del producto no entra en la cuenta. Que aparezca un bloqueante nuevo junto con
uno cerrado sí es avance. Uno nuevo sin cerrar ninguno no es avance, pero
tampoco es «el mismo bloqueo»: es información nueva (ver más abajo). Volver a
cerrar algo que ya se cerró y se reabrió no es avance ni información nueva. El
primer bloqueante que se ve no tiene con qué compararse y cuenta como avance.

Una vuelta sin avance incluye al revisor que no concluye (`no_concluyente`
sin inspección faltante), al implementador que cambia archivos con el mismo
bloqueante abierto y a DeepSeek que mantiene el mismo bloqueante tras
republicar el PR. La primera vuelta sin
avance no repite: el responsable diagnostica por qué la corrección no lo
resolvió. La segunda seguida releva el mismo rol a otro modelo autorizado; si
el relevo tampoco avanza, el encargo entrega el PR con los pendientes abiertos
y termina. Si ya no queda otro modelo autorizado para el rol, hace lo mismo:
entrega con los pendientes escritos en el cuerpo o, si todavía no hay producto,
termina con el motivo, sin pedir intervención. Cerrar un bloqueante o una
aprobación sin nada abierto reinicia la cuenta, también al pasar a la fase
siguiente. Nunca se declara éxito ni se vuelve a crear el encargo.

### El freno no cuenta de más

Tres reglas evitan que el freno pare un encargo por error (casos del 1-nov):

- **Una vuelta cuenta una sola vez.** La vuelta es la generación del agente. Si en
  el mismo barrido entran varias fuentes (la observación del supervisor, el
  revisor, DeepSeek), la primera que no es información nueva la
  resuelve y las demás no suman ni escalan (`Encargo.vuelta_contada`). Antes dos
  fuentes del mismo barrido llevaban el encargo de «diagnosticar» a
  `necesita_usuario` sin dar el turno de diagnóstico.
- **No cuenta con una revisión externa pendiente.** Mientras DeepSeek no haya
  leído el commit vigente, una vuelta del responsable sin cambios no se cuenta:
  el encargo espera 60 s en vez de contar un bloqueante que DeepSeek puede
  haber cerrado ya. Un bloqueante que DeepSeek cerró (resuelto o descartado)
  deja de estar abierto con su relectura y tampoco cuenta.
- **Un bloqueante nuevo es información nueva.** `Encargo.devuelto_con` guarda los
  bloqueos abiertos la última vez que el trabajo volvió al responsable. Si una
  vuelta sin avance trae uno que no estaba ahí (ni se cerró antes), el trabajo
  vuelve al responsable para corregir con todo lo abierto, la escalera de
  diagnóstico empieza de cero y no se escala como «el mismo bloqueo». Escalar
  sigue siendo la salida cuando los mismos bloqueantes siguen abiertos tras el
  diagnóstico.

**Sin tope para hallazgos nuevos.** Una vuelta que trae un bloqueante que el
responsable no había visto es información nueva y vuelve a él para corregir.
No hay tope para esa serie: mientras cada vuelta aporte información nueva, el
encargo sigue hasta cerrar los bloqueantes o hasta que el propietario decida.
El freno por el mismo bloqueo sin información nueva (`_sin_avance`) se mantiene.

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
- **`continuar` no deja un `recibido` mudo.** Se acepta desde `detenido`,
  `necesita_usuario` y desde un `recibido` sin proceso ni reserva, sin obligar
  a `parar` primero. Limpia la pregunta vieja del relevo agotado, los
  contadores de la vuelta y los bloqueantes externos que una restricción
  expresa ya deja fuera. No borra una decisión vigente del propietario, como
  la de un hallazgo tardío de DeepSeek sobre un PR ya fusionado. Si la
  pregunta sigue realmente abierta y nada la contestó, el encargo queda en
  `necesita_usuario` con esa pregunta.
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
bloqueante cerrado, otro veredicto, un ajuste o una respuesta del propietario.
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
  supervisor, sin sesiones LLM, hace commit del producto
  (sin `.interno/`), push, abre o actualiza el PR con `motor/github.py` y lo
  marca listo (la revisión automática ignora borradores). Con la aprobación
  del revisor y, donde revisa, de DeepSeek, el encargo termina. El CI corre
  después en el PR real y su resultado se lee una vez, como se describe
  arriba. Cada hecho es un evento `entrega_externa`.

Antes de publicar o de cerrar, el supervisor vuelve a calcular la versión
del producto y la compara con la referencia aprobada y con el producto
registrado. Si el árbol cambió tras la aprobación, no hace commit ni abre
PR ni compone el cierre: anota `producto_listo` con la versión vigente y
pide re-revisión al revisor. Si el producto ya tiene un SHA publicado, el
artefacto es ese commit y un cambio local posterior no se convierte en
otro artefacto. Un producto que perdió su SHA publicado y cuyo árbol coincide
con la versión aprobada no abre otra revisión: la diferencia era de firma, no
de producto.

Con comprobaciones declaradas, el commit y el push esperan a que el comprobador
registre su observación sobre la versión vigente. Si la observación trae una
comprobación en rojo, la versión vuelve al responsable y no se publica; si el
instrumento no pudo observar, vuelve como instrumento reparable. La ukey del
paso de commit incluye la firma del producto, así que un producto nuevo sobre
el mismo HEAD vuelve a ligar su `sha_git`.
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

El comentario de DeepSeek sobre un SHA puede cambiar sin que cambie el SHA:
un hallazgo descartado (`ai-review: descartar Fn`) o resuelto, una generación
nueva o una re-revisión. Cada barrido, con un espaciado de 60 s por SHA
(`_RELECTURA_DEEPSEEK_S`), el supervisor compara la huella del comentario con
la de la última lectura (`Fase.deepseek_huella`). Si cambió, anota la lectura
(`revision` de DeepSeek y `entrega_externa` `deepseek_leido`, con la huella y
el número de lectura del SHA en la clave): lo que el comentario ya no trae
queda resuelto y llega al estado del encargo en ese barrido. Si no cambió, no
anota nada. Una lectura con un bloqueante abierto baja el nivel alcanzado de
`pr` a `pr_abierto`: la fase no se da por cumplida con un High o un Medium
abierto. Con un agente en curso solo se anota; devolver el trabajo es cosa de
quien incorpora su vuelta. Si GitHub falla
al releer, el espaciado no se consume (se reintenta a los 15 s) y el fallo queda
en el evento `lectura_fallida` (uno por tramo de fallos, más otro cada 10 minutos
si dura) y se ve en `ver` (`entrega.deepseek.error`) hasta que una lectura sale
bien. No toca el motivo ni el siguiente trabajo del encargo, que lee el próximo
turno del agente: siguen siendo los suyos.

Antes de terminar una fase con entrega `pr` (con `integrado` o `desplegado`, antes
del merge), el supervisor vuelve a leer, sin espaciado, el comentario de
DeepSeek sobre el SHA final de su PR. Si cambió y
trae un High o un Medium abierto, la fase vuelve al responsable. Si DeepSeek
todavía no revisó ese SHA, espera como siempre. Al cerrar la última fase
(lo que termina el encargo) lee también el PR de cada fase anterior, porque
DeepSeek puede publicar un hallazgo después de que esa fase cerró. Si hay un
High o un Medium abierto, el evento `fase_reabierta` devuelve esa fase al
responsable en su misma rama y su mismo PR; al prepararla de nuevo, el
encargo sigue con la fase que quedaba. Si el registro no aplica la lectura o
la reapertura, el encargo no cierra: anota el fallo y reintenta a los 2 minutos. Sin puerto de
GitHub tampoco cierra una fase con PR y nivel `pr` alcanzado: no hay veredicto
que comprobar, y el encargo espera visible en el paso de entrega. Con entrega
`integrado` o `desplegado`, las fases anteriores ya se fusionaron y no se
reabren: si el comentario de DeepSeek sobre su PR trae un High o un Medium
abierto al cerrar la última fase, el encargo no termina en silencio, sino que
pregunta una vez al propietario (`necesita_usuario`) si abre un encargo aparte.

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
(`TOPE_FALLOS_REPO`), sin nada distinto entre ellos, deja de reintentar. Es el
mismo fallo cuando falla la misma operación (el texto antes del primer `: `
del mensaje); el detalle que cambia entre reintentos, como rutas temporales,
pids u otra salida de git, no reinicia la cuenta. Queda en `necesita_usuario`
con el último error. No abre una solicitud, así que
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
