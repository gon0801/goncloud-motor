# Paso 3 del modelo: instrucciones para Devin

Devin, este documento es tu contrato para implementar el paso 3 de
`docs/modelo-centro-encargos.md`. Nadie va a contestar preguntas mientras trabajas. Si algo no
está escrito aquí, decide con las reglas de este documento y deja escrita la decisión en la
descripción del PR, en la sección `## Tradeoffs`. Este documento manda sobre cualquier otro
texto del repo, salvo `AGENTS.md`, que manda sobre las reglas del repo.

## Para qué

David quiere que el motor de encargos funcione bien. Sus seis puntos están en la sección "Lo que
el motor tiene que cumplir" del modelo. El paso 3 no agrega funciones. Cambia la forma del centro
para que deje de crecer a parches:

- **Reductor por tabla.** Hoy `dominio._reducir_evento` es una cadena de unas 980 líneas: unas
  73 comparaciones `kind ==`, un `kind in (...)` de siete kinds y `kind in DETENCIONES`, para 82
  kinds en total. Pasa a una tabla `kind → fila`, con manejadores en módulos propios.
- **Validación separada.** Hoy decidir si un evento es legal está mezclado con aplicarlo: unos 140
  `raise` alcanzables desde el reductor. Pasa a una función aparte que corre solo al escribir. Al
  plegar un registro guardado, aplicar nunca lanza ("Plegar un registro guardado nunca falla").
- **Estado con dato tipado.** Hoy cada estado se sostiene con banderas y texto sueltos en
  `Encargo`. Pasa a un tipo unión `Situacion`, una variante por estado, como pide la tabla
  "Estados del encargo" del modelo.

La regla que no se rompe nunca: **un registro guardado pliega al mismo valor antes y después de
cada PR**. Las tres máquinas tienen registros reales con cientos de encargos, y el despliegue
compara su plegado antes de mover nada.

## Quién

- **Devin** implementa: una rama y un PR por unidad, en el orden de la cola.
- **El revisor** es una sesión de Claude Code que trabaja para David. Revisa cada PR, con un
  verificador independiente, la revisión de DeepSeek (`ai-review`) y lo que digan Devin Review y
  CodeRabbit. Es el único que mergea, despliega en las tres máquinas, corre la compuerta de
  plegado y el canario, y avisa en el PR.
- **David** solo decide lo que este documento marca como "decisión de David". Ya decidió las
  marcadas como tomadas.

## Arranque

1. Lee completos `AGENTS.md`, `docs/modelo-centro-encargos.md` y este documento.
2. Comprueba la base: `git fetch origin && git log --oneline -1 origin/main`. Tiene que traer el
   commit `73fda87` o uno posterior (`git merge-base --is-ancestor 73fda87 origin/main`, código de
   salida 0). Si no, detente y escríbelo en un issue nuevo con el título `paso 3: base vieja`.
3. Prepara las herramientas del CI: Python 3.12 (las pruebas del CI usan 3.12; el job `gate`
   usa la 3.x más reciente) y `python -m pip install pytest pytest-xdist pyright==1.1.414 ruff==0.17.4`.
4. `AGENTS.md` pide aplicar "poteto-mode", una guía de Claude Code que tú no tienes. Para ti
   significa: nombra la forma de los datos antes de escribir código, prueba que falla primero, y el
   cambio más chico que cumpla la evidencia. No tienes que instalar nada.
5. Empieza por el PR 3.0 de la cola.

## Preaprobaciones

| Operación | Alcance | Decisión |
|---|---|---|
| Crear ramas `reconstruccion/paso3-<n>-<tema>` desde `origin/main` | este repo | Aprobado |
| Push normal a tus ramas | tus ramas | Aprobado |
| Abrir PR listo (no borrador) contra `main` | uno por unidad | Aprobado |
| Integrar `main` en tu rama con `git merge origin/main` | tus ramas | Aprobado |
| Contestar hilos de Devin Review, CodeRabbit y comentarios del revisor | tus PR | Aprobado |
| Crear módulos nuevos bajo `motor/encargos/` y herramientas en `tools/` | según la tabla de archivos | Aprobado |
| Mergear cualquier PR | todos | Negado |
| `git rebase` de una rama ya subida, `push --force` o `--force-with-lease`, `--no-verify` | todos | Negado |
| Escribir comentarios que empiecen con `ai-review:` (descartar hallazgos) | todos | Negado: solo David |
| Desplegar, tocar `ops/despliega-encargos.sh`, `ops/compuerta*` o las máquinas | todos | Negado |
| Cambiar la firma de `dominio.reducir` o el valor de `dominio.ESTADOS` | todos | Negado |
| Escribir un kind de evento nuevo | todos | Negado en el paso 3 |

> **Prohibido.** Mergear. Reescribir la historia de una rama subida. Saltarte un hook. Descartar
> hallazgos de DeepSeek. Cambiar lo que el motor escribe en el registro. Cambiar el valor del
> plegado de un registro existente. Agregar funciones o líneas a `motor/encargos/ciclo.py` o
> `motor/encargos/dominio.py`. Subir datos de registros reales o nombres de repos privados (el repo
> es público). Preguntarle algo a David: si dudas, decide con este documento y escríbelo en el PR.

## Reglas de trabajo

1. **Tamaño.** `ciclo.py` y `dominio.py` están congelados (`politica.CONGELAMIENTOS`): no ganan
   funciones, ni métodos, ni propiedades, ni líneas, ni tokens. Lo nuevo va a módulos propios.
   Un helper de más de 100 líneas que mueves a otro archivo cuenta como función nueva y falla:
   no los muevas, apúntalos desde la tabla. Comando: `python tools/calidad.py tamano --base origin/main`.
2. **Antes de abrir o actualizar un PR**, desde la raíz, todos con código de salida 0:
   `python tools/calidad.py tipos`, `lint`, `io`, `marcadores`, `politica`,
   `tamano --base origin/main`, `kinds --base origin/main`; `python tools/candados.py cuerpo` y
   `secretos`; `python tools/pruebas_nuevas.py --base origin/main` (una prueba nueva debe fallar
   con el código de la base, salvo que lleve `# no-roja-en-base: <razón>`).
3. **Commits.** Primero `test(<área>): ...` con la prueba que falla, después el cambio. Títulos en
   español al estilo de `git log --oneline -15`. El cuerpo dice por qué.
4. **Descripción del PR**, en este orden: `## Why` (abre con la meta de David), `## What changed`,
   `## Scope`, `## Tradeoffs`, `## Blast Radius`, `## Verification` con la salida textual de
   cada comando de la evidencia de su unidad.
5. **Sin comentarios que narren el cambio.** Un comentario solo explica un porqué que el código no
   puede mostrar.
6. **La batería completa la corre el CI.** Tú corres las pruebas de lo que tocas y la evidencia de
   tu unidad. El CI usa `python -m unittest` y pytest; Windows corre en su propio job.

## Loop de cada PR

1. Abres el PR con la evidencia de su unidad en `## Verification`.
2. Esperas el CI, la revisión de DeepSeek (comentario con el marcador `ai-review:sticky`), Devin
   Review y CodeRabbit. Atiendes todo hallazgo Critical, High o Medium de DeepSeek con commits
   nuevos en la misma rama, y contestas cada hilo de los bots con lo que hiciste o con la razón
   para no hacerlo.
3. El revisor publica en el PR un comentario `Revisión del revisor, ronda <n>` con lo que falta.
   Lo atiendes en la misma rama y contestas con `Atendido en <sha>`.
4. Cuando el revisor da el visto bueno, él mergea y despliega, y publica `Desplegado <sha> en mac,
   mini y pc: compuerta cambian de valor 0; canario coincide`. Solo entonces empiezas la unidad
   siguiente, desde `origin/main` actualizado. Todas tocan el reductor, así que van en serie.

## Cola de unidades

La evidencia de cada unidad incluye siempre la regla 2. `T=$(mktemp -d)`.

**3.0 Red de seguridad: corpus de plegado.** No toca `motor/`.
- `tools/corpus_plegado.py generar --ref <ref> --salida <dir>` abre un worktree temporal de la
  ref y corre ahí las pruebas del repo que crean un `Registro`. Un plugin de pytest envuelve
  `Registro._escribir` (el único `INSERT` en la tabla `events` de los encargos), con una conexión
  por hilo y por proceso de xdist. Escribe **un archivo SQLite por cada `Registro` que abre cada
  prueba**, con la misma tabla `events`, en `<dir>/registros/`: así no chocan las ukeys `UNIQUE`
  entre pruebas ni se mezclan dos registros con el mismo id de encargo. Cada lote que el registro
  rechaza (`EventoIlegal` o `ClaveOcupada`) se guarda en `<dir>/rechazos.jsonl` con el archivo, el
  lote completo, el índice rechazado y cuántos eventos tenía ese registro al intentarlo. Los
  metadatos van en `<dir>/meta.json`, y el estado de referencia de la base, un `asdict` en JSON por
  encargo, en `<dir>/estado-base/<archivo>.json`. Descarta, contándolos, los encargos que no
  pliegan con la base. Las pruebas que insertan filas a mano sin pasar por `Registro` no entran al
  corpus; está bien así. Una semilla escribe por `append_many` secuencias legales de los kinds que
  ninguna prueba produce, hasta cubrir los 82.
- `tools/corpus_plegado.py verificar <dir> [--raiz <árbol>]` reaplica cada archivo con el reductor
  del árbol (por omisión el actual; `--raiz` deja usar otro, por ejemplo una copia mutada) y
  compara **el estado completo** de cada encargo (`asdict` en JSON, como hace `plegado.py comparar`)
  contra el de la base guardado por `generar`. La `vista` no basta: hay contadores, como
  `fallos_repo` y `fallos_entrega`, que no salen en la vista.
- **Evidencia.** `generar --ref origin/main --salida "$T/c"` imprime
  `K encargos (D descartados), E eventos, R lotes rechazados, 82 de 82 kinds`.
  `verificar "$T/c"` imprime `acepta E de E; rechaza R de R; estado igual en K de K` y sale con 0.
  `python tools/plegado.py comparar --base origin/main --estricto "$T"/c/registros/*.sqlite` da
  `sin diferencias.` y sale con 0.
- **Prueba de la red.** En `tests/`, una prueba arma un corpus chico propio (unos pocos registros
  escritos con `Registro` en un directorio temporal; no llama a `generar`, porque el CI clona
  superficial, no trae `origin/main` y no instala xdist), guarda su estado de referencia con el
  reductor sin mutar en el mismo formato que `generar`, siembra en una copia del árbol una mutación
  de una línea (sacar `plazo` de `_ENTRE_REINTENTOS`, que cambia `fallos_repo` en la secuencia
  `entrega, espera, plazo`) y comprueba que `verificar --raiz <copia>` falla.
- Un prototipo de esto sobre 73fda87 dio 942 encargos, 11,433 eventos, 89 lotes rechazados y 68
  de 82 kinds en unos 2 minutos con `-n 8`. Los kinds que faltaron: `afirmacion`,
  `autorizacion`, `ci_pr_sondeo`, `ci_producto`, `ci_revision_rojo`, `colgada`,
  `contexto_entrega`, `entrada_incorporada`, `entrega_bloqueada`, `entrega_devuelta`,
  `fase_reactivada`, `lectura_fallida`, `proveedor_disponible`, `respuesta_revisor_pedida`.

**3.1 La tabla, sin cambiar nada** (2 o 3 PR, uno por grupo de módulos).
- Cada cuerpo de `if kind ==` pasa a una función `(e, d, hora, nuevo) -> Encargo` en
  `motor/encargos/reductor/<tema>.py`, con sus `raise` dentro. Temas: pedido y órdenes, intento,
  fase y despliegue, revisión, necesidades, esperas y marcadores, retirados. `_reducir_evento`
  queda en `entrega` más la búsqueda en `TABLA`.
- Imports sin ciclo: `dominio.py` importa `detencion` arriba, y `detencion.py` importa `dominio`
  dentro de sus funciones. Haz lo mismo: los módulos de `reductor/` importan `dominio` dentro de sus
  funciones, y `_reducir_evento` importa `TABLA` dentro de su propio cuerpo (no cambies las firmas de
  `reducir`, `_reducir` ni `_reducir_evento`).
- El kind desconocido conserva el texto exacto del `raise` actual (`evento desconocido: <kind>`),
  porque `tools/calidad.py kinds` lo usa. Las ramas que ya delegan en un helper de `dominio.py` de
  firma `(e, d, hora)` (por ejemplo `_orden`, `_resolucion`, `_respuesta`, y los de `DETENCIONES`)
  entran a la tabla con un adaptador de una línea que ignora `nuevo`; no los muevas.
- **Evidencia.** `verificar` y `comparar --estricto` del corpus en 0. Una prueba afirma que
  `set(TABLA) | {"entrega"}` son los 82 kinds y que cada uno tiene eventos en el corpus.
  `dominio.py` baja unas 900 líneas al final de las 2 o 3 partes.

**3.2 Propiedades de fila.** Cada fila gana `alcance` (`encargo`, `fase` o `intento`),
`corta_series` y `marcador`. Las tuplas `_ENTRE_REINTENTOS` y `_EVENTOS_DE_MARCADOR` siguen en
`dominio.py` con el mismo nombre y el mismo tipo (`tuple[str, ...]`, que pide
`registro.ultimo_evento(excluir=...)`), pero se derivan de la tabla; así `ciclo.py` no cambia ni
gana tokens. `_EVENTOS_DE_MARCADOR` son las filas con `marcador=True`; `_ENTRE_REINTENTOS`, las de
`corta_series=False` y `marcador=False`. Para que `dominio.py` pueda importar la tabla arriba sin
ciclo, ningún módulo de `reductor/` importa `dominio` en el nivel del módulo (ya lo pide el 3.1).
**Evidencia:** la del 3.1; una prueba de que las tuplas tienen los mismos elementos que antes, con
`# no-roja-en-base: fija las tuplas que ahora salen de la tabla`; y la prueba de la red del 3.0
ahora muta la fila (`corta_series` de `plazo`) en vez de la tupla, y sigue fallando como debe.

**3.3 Validación separada** (un PR por módulo de la tabla; el último vuelve total el plegado).
- Los `raise` pasan a `Fila.validar`, en módulos nuevos. Donde validar y calcular van juntos, una
  lectura pura `leer_<kind>(d)` da la carga tipada que usan `validar` y `aplicar`. `validar` corre
  solo con `nuevo=True` (al escribir); `aplicar` corre siempre y no lanza.
- El último PR de 3.3 hace tres cosas juntas: un kind desconocido deja de lanzar al plegar (devuelve
  el mismo estado y `plegado.py` lo cuenta); `tools/calidad.py` deja de sondear con un kind
  inventado y lee `TABLA` cuando la ref la trae, si no sondea como hoy; la prueba de
  `tests/test_plegado.py` con la `orden inventada` pasa a un reductor sembrado que lanza.
- **Evidencia.** `verificar` y `comparar --estricto` en 0. Una prueba de tabla con una fila por
  cada `raise` movido (kind, estado previo, datos, mensaje), con
  `# no-roja-en-base: fija la validación que se mueve`. Una prueba "aplicar no lanza" sobre cada
  prefijo del corpus con los 82 kinds y `data={}`, roja en la base. `kinds` verde contra la base
  vieja y la nueva.

**3.4 Situación como lectura, sin cambiar la forma.**
- `motor/encargos/situacion.py` trae los tipos de abajo y `situacion_de(e) -> Situacion`, que
  lanza `EstadoIncoherente`. `tools/plegado.py comprobar` agrega la invariante `situacion_tipada` y
  otra que mide si `escrituras`, `intentos_nuevos` y `evidencia` dependen del estado. Los cuatro
  `raise` de `dominio.vista` por estados incoherentes pasan a `situacion_de`.
- **Evidencia.** `python tools/plegado.py comprobar "$T"/c/registros/*.sqlite` imprime
  `situacion_tipada: sin violaciones`. Antes del 3.5, el revisor lee esa cuenta sobre los registros
  reales de las tres máquinas y publica el resultado en el PR. Si hay violaciones, el 3.5 espera
  a que el revisor diga cómo seguir.

**3.5 Situación guardada.**
- `Encargo` pasa a `motor/encargos/encargo.py` y `dominio` lo reexporta. Guarda `situacion`. Los
  campos `estado`, `orden_parada`, `reanuda_en`, `intento`, `proc`, `reserva`,
  `intento_interrumpido`, `cierre_forzado_fase` y `cierre_forzado_huella` pasan a propiedades de
  solo lectura con el mismo valor (`ciclo.py` no puede cambiar). `dominio.CAMPOS_LEGADOS` los lista,
  y `tools/plegado.py` arma el JSON de `comparar` con `asdict` sin `situacion` más esas
  propiedades, para que `comparar --estricto` contra la base siga en 0.
- **Evidencia.** `verificar` con `estado igual en K de K` (comparando con las propiedades
  legadas) y `comparar --estricto` en 0.

```python
# motor/encargos/situacion.py: todas @dataclass(frozen=True), cada una con estado: Literal[...]
class Recibido: ...
class Ejecutando:          fase: str; intento: str; proc: Proc | None; reservado: bool
class EsperandoReintento:  motivo: str; reanuda_en: str
class Conciliando:         efecto: str
class Deteniendo:          orden: Parar | Cancelar | FalloInterno; interrumpido: bool
class Detenido:            causa: PorOrden | FalloInterno
class Cancelado: ...
class Terminado:           resultado: Completo | Parcial; forzado: CierreForzado | None
class PorOrden:            accion: Literal["parar", "reabrir", "importar"]
class FalloInterno:        kind: str; clase_ukey: str; rechazo: Literal["ajena", "ilegal", ""]
Situacion = Recibido | Ejecutando | EsperandoReintento | Conciliando | Deteniendo | Detenido | Cancelado | Terminado
```

El campo `estado: Literal[...]` va en cada variante, porque sin él `asdict` da `{}` para
`Recibido` y `Cancelado` y `comparar` no las distingue. Las esperas con clase y tope son del paso
5, y la causa "PR mergeado en otra base" es del paso 4: no las agregues.

## Archivos

| Puede tocar | No toca |
|---|---|
| `motor/encargos/reductor/` (nuevo), `motor/encargos/situacion.py`, `motor/encargos/encargo.py` | `motor/encargos/ciclo.py` (solo imports sin ganar tokens, 3.2) |
| `motor/encargos/dominio.py` solo para sacar código y apuntar a la tabla | `motor/encargos/registro.py`, `motor/encargos/lote.py` |
| `motor/encargos/detencion.py` (ya es tabla: súmala a `TABLA`) | `ops/` completo, `.github/workflows/` (salvo un job opcional del corpus en 3.0) |
| `tools/corpus_plegado.py`, `tools/plegado.py`, `tools/calidad.py` (`kinds`, 3.3) | `motor/flota/`, `motor/integraciones/` |
| `tests/` | `docs/modelo-centro-encargos.md` (lo actualiza el revisor) |

## Cuando algo se atora

| Situación | Acción |
|---|---|
| `tamano` rechaza el PR | Saca código de `dominio.py`/`ciclo.py` a módulos propios o borra código muerto. Nunca subas el límite. |
| El corpus da diferencias que no sabes explicar | No sigas. Escribe en el PR qué encargos y qué campos difieren (sin datos reales: el corpus sale de las pruebas) y espera la ronda del revisor. |
| Una prueba vieja falla con tu cambio | Arréglala solo si el arnés era irreal; explícalo en `## Tradeoffs`. Si el motor cambió de conducta, el cambio está mal. |
| DeepSeek insiste en un hallazgo que crees falso | Contesta con la evidencia y deja el PR abierto. Solo David descarta. |
| `main` avanzó y hay conflicto | `git merge origin/main`, resuelve, corre la regla 2, push normal. |
| Una prueba falla también en `main` (por ejemplo `test_un_ajuste_del_propietario_vuelve_a_intentarlo`) | No es tuya: nómbrala en `## Verification` y sigue. |
| Un `raise` movido además evitaba un cálculo posterior | Muévelo junto con ese cálculo a `leer_<kind>`. La prueba de tabla del 3.3 lo debe mostrar. |
| No sabes en qué tema va una rama | El tema del evento que la escribe en `ciclo.py`. Anótalo en el PR. |
| Te quedaste sin contexto o te reiniciaste | Este documento, la rama y el último comentario del revisor en el PR tienen todo el estado. |

## Decisiones ya tomadas

- **Plan importado.** Un plan que importa `motor/encargos/migracion.py` queda `detenido` sin caber
  en las cinco causas del modelo. Va como `PorOrden("importar")`, porque solo el dueño corre la
  importación. Decisión por omisión del revisor; David puede cambiarla.
- **"Falta de avance"** es una causa del modelo, pero hoy nunca detiene (la escalera de
  `sin_progreso` entrega con pendientes). El paso 3 no la agrega.
- **Huecos que el paso 3 conserva** sin arreglar (cambian valores y se miden antes en las
  máquinas): `cancelar` sobre un cancelado vuelve a `deteniendo`; aplicar un efecto saca de
  `conciliando` aunque otro siga incierto; el reductor lee `socket.gethostname()` si el evento no
  trae `host`. Si te topas con ellos, déjalos igual y nómbralos en el PR.

## Cierre

El paso 3 termina cuando el 3.5 está mergeado y el revisor publicó `Desplegado` con la compuerta
en "cambian de valor 0" en las tres máquinas. Son unos 9 o 10 PR. Al terminar cada uno, tu último
comentario en el PR es `LISTO <sha>` o `ATORADO <una línea>`.

Lo que queda fuera: las banderas de `Fase` (despliegue, CI, inspección, DeepSeek), que son de los
pasos 4 y 5; las esperas tipadas, que son del paso 5; y el borrado del modo planes, que es del
paso 6.
