# C2: el motor es quien corre poteto-mode; los CLIs solo corren hojas

## Problem

David quiere que todo agente del motor trabaje con pstack/poteto-mode y que el motor lo haga cumplir. Hoy el motor lanza un solo proceso por intento con "lee brief.md y ejecútalo completo" (`effects.py:209`) y confía en que el agente cargue la skill, abra su lista de tareas, lance subagentes y lo cuente en el `resumen`. Los hechos dicen que no pasa: ni Sonnet ni Kimi cargaron poteto-mode aunque la tenían a la mano y un hook se las pidió (hechos §4). Lo único comprobable por CLI es que hubo una llamada que cargó la skill y cuántos subagentes hubo, en siete formatos distintos (json final de Claude sin herramientas, wire.jsonl de Kimi, un SQLite de 4 GB en opencode, updates.jsonl de Grok, session.jsonl de Muse salvo `--no-session-log`, Qwen sin nombre conocido de la herramienta). Que el agente "siguiera la skill completa" es su palabra.

La constante que cambia la forma: poteto-mode ya separa dos papeles. "El chat que recibe `/poteto-mode` es dueño del playbook, de la lista de tareas y de cada `Task`. Tú eres uno de sus delegados" (`agents/poteto-agent.md`). Las skills de flujo (`how`, `architect`, `arena`, `interrogate`, `swarm`) son orquestaciones: el orquestador lee una tabla de pasos y lanza delegados con un texto de referencia (`explainer-prompt.md`, `runner-prompt.md`, `reviewer-prompt.md`). Las skills hoja (`unslop`, `deslop`, `tdd`, `technical-writing`, las `principle-*`, `comment-sicko`) son texto para un solo agente. El motor ya es un orquestador sin modelo, con cuota por cuenta, tope de 4 agentes por máquina y paralelismo. Entonces el diseño es: **el motor ocupa el lugar del chat dueño del playbook; cada CLI corre un paso como delegado, con las hojas pegadas en su encargo; el motor comprueba el artefacto de cada paso antes de abrir el siguiente.** Cumplir poteto-mode deja de ser una conducta del agente y pasa a ser la forma del trabajo.

Restricciones que se honran: instalación idéntica (nada por CLI ni por máquina: pstack llega al motor, no a los CLIs); tick sin modelo (el motor no juzga prosa, comprueba artefactos); un plan completo en su máquina; cualquier modelo del plan; revisa quien no escribió; solo el motor mergea; cambios de máquina como plan (aquí no hace falta ninguno).

## Usage (caller's view)

Para quien entrega un plan, nada cambia salvo un campo opcional por bloque:

```markdown
## Bloque B — reintento de la ingesta
lane: gate
playbook: bug-fix          # opcional: feature (gate/release), docs (fast), bug-fix
| Fila | Contenido | Acepta |
| B.1 | la corrida ok=false se reintenta una vez | pytest tests/test_ingesta.py -k reintento |
```

Para la conf compartida (`goncloud-motor-conf`), tres cosas nuevas, iguales en las tres máquinas:

```toml
# pstack.toml
[pstack]
origen = "cursor/plugins"                  # repo público de GitHub
commit = "190d2870c0f3…"                   # fijado; subir de versión es un PR aquí
raiz = "pstack"                            # carpeta del tarball con skills/, agents/ y docs/
```

```
reglas/pasos/fundar.md  disenar.md  reproducir.md  implementar.md  pulir.md  redactar.md  revisar.md
reglas/adaptadores/opencode.md  muse.md  grok.md  kimi.md  qwen.md     # tabla corta de herramientas
```

Lo que ve el motor por dentro, tres llamadas:

```python
# machine.decide, rama ARREGLAR / REVISAR
jugada = poteto.jugada_para(bv.block, role)                 # tabla, pura
for lanz in poteto.siguiente(jugada, bv):                   # 0, 1 o N (candidatos en paralelo)
    mo = _elige(ladder_para(lanz, bv), ...)
    acciones.append(StartAttempt(bid, role, mo, n, bv.head(), fb, paso=lanz.paso, cand=lanz.cand))

# effects.start_attempt
texto = poteto.brief(paso, ruta.cli, self.pstack, self.cx.conf, insumos=poteto.insumos(bv, paso), encargo=...)

# effects.collect, tras classify == terminado
return poteto.comprueba(paso, d, res, punta=punta, sha_inicio=att.sha, existe=lambda p: (cwd / p).exists())
```

Lo que ve un delegado (GLM por opencode, por ejemplo) en su `brief.md`:

```
<reglas/encargo.md>
# Paso 2 de 4: disenar · plan X · bloque B · candidato 2 de 2
El motor lleva el playbook `feature` de poteto-mode. Tú eres un delegado: no cargues skills ni
abras subagentes; todo lo que necesitas está abajo. Nadie lee tu pantalla.
## Tus herramientas (opencode)        <- reglas/adaptadores/opencode.md
## Hojas de pstack (léelas completas)  <- skills/architect/references/runner-prompt.md,
                                          rationale-template.md, design-red-flags.md, verbatim
## Insumos                             <- ruta a how.md del paso anterior
## Qué entregar                        <- reglas/pasos/disenar.md: design.md y sketch en $MOTOR_ATTEMPT_DIR,
                                          con las secciones del template; result.json {"archivos": [...]}
```

## Shape

**Datos primero.** Todo vive en `motor/poteto.py` (puro) como una tabla:

- `Entrega`: qué deja el paso y cómo se comprueba. Tres formas, una por tipo de verdad: `Documento(archivo, secciones, cita_rutas, min_lineas)`, `Commits()` (el SHA del result es la punta de la rama), `Veredicto()` (SHA y veredicto válidos, `repro` que de verdad reproduce). Las tres son datos; `comprueba()` las interpreta.
- `Paso(id, hojas, escribe, candidatos, por, entrega, insumos)`. `hojas` son rutas dentro de `<pstack>/` que se pegan verbatim. `por` es `bloque` (fundar, disenar: una vez por bloque, las rondas de arreglo no las repiten) o `ronda` (implementar, pulir: cada ronda). `candidatos` es cuántos delegados en paralelo (disenar = 2, porque architect exige dos candidatos estructuralmente distintos).
- `Jugada(nombre, pasos, fuente)`: el playbook. `JUGADAS` trae cuatro, cada una citando el archivo y los pasos de poteto-mode de donde sale:
  - `docs` (lane fast): `redactar` con unslop + technical-writing. Una corrida.
  - `feature` (gate, release): `fundar` (explainer-prompt de how, ruta simple) → `disenar` ×2 (runner-prompt, rationale-template, design-red-flags) → `implementar` (principle-model-the-domain, sequence-verifiable-units, prove-it-works; recibe los dos design.md y debe nombrar cuál sigue) → `pulir` (deslop + `agents/comment-sicko.md` + technical-writing + unslop).
  - `bug-fix` (plan lo pide): `reproducir` → `fundar` → `implementar` (+ tdd) → `pulir`. `reproducir` entrega un comando; el motor lo corre en el SHA base y exige salida distinta de 0; ese mismo comando se vuelve un `Acepta` implícito del bloque (debe salir 0 en lo entregado). La regla 2 de quality-kit deja de ser prosa.
  - `revision` (role revisar): `revisar` ×`revisores_panel` (1 por omisión) con reviewer-prompt, rubric, code-quality-review y lead-judgment de interrogate, más `reglas/revisor.md`; recibe how.md y el design.md elegido.
- `Lanzamiento(paso, cand)`: lo que `siguiente()` devuelve y `StartAttempt` carga. `Attempt` gana `paso` y `cand`; el resto del `fold` no cambia.

**Flujo.** `siguiente(jugada, bv)` mira los intentos del papel: el primer paso que no tenga `paso_hecho` (o `entregado`) se lanza; si tiene `candidatos` N, devuelve N lanzamientos y el paso queda hecho cuando todos terminaron y al menos uno es válido. El último paso que escribe emite `entregado` con la punta (igual que hoy) y el bloque sigue a VERIFICAR, PR, REVISAR sin tocar `estado()` ni `merge_gate`. El reviewer ve el bloque en REVISAR y `siguiente()` sobre la jugada `revision` le da su único paso.

**Comprobación.** `comprueba()` es pura salvo por un `existe(ruta)` que se le pasa. Documento: existe en la carpeta del intento, trae cada sección del template, cada ruta entre acentos graves existe en el repo, pasa el mínimo de líneas. Commits: SHA = punta y la punta se movió (pulir puede no moverla). Veredicto: como hoy, y además el motor corre cada `repro` en la copia del SHA revisado; un bloqueante cuyo repro sale 0 no reprodujo y baja a notas (regla 4, ya en código). Lo que no pasa es `paso_invalido` con el detalle ("design.md no trae la sección Alternatives considered"), que entra en `relevo.DE_KIND` como `INVALID_TERMINAL`: cuenta como falla del modelo, el siguiente intento del mismo paso recibe el motivo por `_feedback`, y a `fallas_seguidas_max` entra otro modelo. Nada se lee de transcripts, sesiones ni bases de datos de los CLIs.

**Distribución de pstack.** `Conf` gana `pstack: PstackPin`. `effects.Pstack.materializa()` baja `https://codeload.github.com/<origen>/tar.gz/<commit>` con `urllib` + `tarfile` (repo público, sin `gh`), lo deja en `~/.motor/pstack/<commit>/` con rename atómico y verifica `HOJAS_REQUERIDAS`. Content-addressed e idempotente: la misma conf en tres máquinas da la misma carpeta. `motor conf valida` exige el pin y que cada `paso` de `JUGADAS` tenga su `reglas/pasos/<id>.md`. `doctor` reporta pin y si está materializado. No hay symlinks en `~/.claude/skills`, ni `~/.agents`, ni hooks, ni agentes por CLI: el `sincronizar-pstack.sh` de la Mac queda para las sesiones interactivas de David y no le importa al motor.

**Invariantes en tipos.** Un `StartAttempt` sin `paso` no existe (campo obligatorio). `Entrega` es una unión cerrada; `comprueba` agota sus variantes. `MergeTicket` sigue igual. `autores()` = modelos de todo paso del papel implementar que no sea `fundar` (quien diseñó o escribió no revisa; quien solo explicó el código, sí puede).

**Profundidad de interfaz.** Superficie pública: `jugada_para`, `siguiente`, `insumos`, `brief`, `comprueba` en `poteto.py`; `Pstack.materializa` y `Pstack.hoja` en `pstack.py`. Detrás quedan los playbooks, qué hoja va a qué paso, el orden, el paralelismo, el formato de cada artefacto y la regla de repro. `machine.decide` suma una rama de diez líneas; `effects.start_attempt` cambia qué texto escribe en `brief.md`; `collect` cambia el final. Tres archivos para seguir el dato de punta a punta.

**Lo que no hace.** No juzga calidad de prosa con un modelo. No detecta si el delegado "siguió" la hoja; detecta si produjo lo que la hoja produce. No le exige subagentes al agente: el fan-out es del motor y cuenta contra los topes de siempre porque cada candidato es un `StartAttempt`.

## Synthesis decision

*(lo llena arena)*

## Tradeoffs accepted

- Aceptamos 4 a 6 corridas de CLI por bloque gate (hoy 2) a cambio de que cada una sea corta, sin subagentes dentro, y de que el motor pueda comprobar cada una. El costo por bloque sube en lanzamientos y baja en tokens por sesión; el neto no está medido.
- Aceptamos que el implementador no haya visto la exploración (solo how.md y design.md) a cambio de la separación de revisión que poteto-mode pide en Feature paso 4 ("la ganancia es la separación, no las líneas").
- Aceptamos un playbook fijo por lane en vez del enrutado por criterio de poteto-mode ("toca más de un archivo", "diseño contestado") porque el criterio lo evaluaría un modelo y David no quiere modelos vigilando. El plan lo puede pisar con `playbook:`.
- Aceptamos que la "síntesis" de arena la haga el implementador al elegir base (nombra `cand-k` en su result.json) y no un juez aparte. Es una corrida menos; el revisor ve ambos diseños y puede objetar la elección.
- Aceptamos pegar hojas verbatim (200 a 400 líneas por paso) en cada brief en lugar de que cada CLI las cargue. Es lo que hace idéntico el encargo en siete CLIs y tres máquinas.
- Aceptamos que fundar y disenar sean por bloque y no por ronda: un rediseño tras un veredicto exige una nota del lead (`motor nota`) o un plan nuevo.

## Alternatives considered

- **Confiar y auditar el transcript** (una regla por CLI: `Skill`/`skill`/`read_skill`/`read_file` del SKILL.md y conteo de subagentes). Expone al motor el formato interno de siete CLIs (el json de Claude ni trae herramientas; opencode es un SQLite de 4 GB; Muse se apaga con `--no-session-log`), prueba que cargó, no que siguió, y rompe en cada versión. Rechazada como compuerta.
- **Instalar pstack en cada CLI** (symlinks a `~/.agents/skills`, agentes por CLI, hook de arranque, `--agent poteto-agent`). Es configuración por máquina y por CLI (la Mini y la PC no tienen nada de eso), Grok no tiene herramienta de skill, `poteto-agent` busca en `~/.cursor/plugins/` sin ruta, y al final sigue siendo el agente quien decide seguirla. Rechazada.
- **Pegar poteto-mode entero en el brief y exigir la lista de tareas en el result.json.** Un solo agente con 250 líneas de skill más el playbook más el adaptador; la lista de tareas es texto que se puede escribir sin hacer nada. Es la forma de hoy con más palabras. Rechazada.

## Open questions and risks

- ¿Cuántos candidatos de diseño por omisión: 2 (lo que architect exige) o 1 con `disenar_candidatos` en `general.toml` para subirlo? El costo doble es solo en ese paso.
- ¿`autores()` debe incluir al que hizo `disenar`? Aquí sí (diseñó la parte). Si eso deja sin revisor a un plan con `implementa: a` `revisa: b`, se relaja a "solo quien hizo commits".
- Riesgo mayor: la calidad del traspaso. Si how.md o design.md salen flojos pero con las secciones correctas, pasan la compuerta y el implementador construye sobre arena. La mitigación es que el siguiente delegado los consume (y puede decir en su result que no sirven) y que el revisor los ve; no hay compuerta mecánica para la profundidad.
- Riesgo: CLIs que siguen mal briefs largos (GLM, Kimi) tendrán más `paso_invalido` al inicio. ¿Se acepta que eso los empuje por relevo, o se les da `fallas_seguidas_max` propio?
- La Mac es la única con el hook SessionStart que pide poteto-mode a Claude; el brief dice lo contrario. ¿Se quita la Mac del hook para los procesos del motor (por `--settings`) o se deja que el delegado gaste un `Skill` de más?
- ¿Un veredicto CAMBIOS con hallazgo de diseño debe invalidar `disenar` del bloque? Propuesta: campo opcional `redisenar: true` en el veredicto; sin él, la ronda de arreglo solo rehace implementar y pulir.

## Qué plan haría falta

Un plan **POTETO 01**, sin bloques de máquina:

- **A** `goncloud-motor-conf` (lane fast): `pstack.toml` con el pin, `reglas/pasos/*.md`, `reglas/adaptadores/*.md`, `conf valida` los exige. Acepta: `motor conf valida .`.
- **B** `goncloud-motor` (lane gate): `poteto.py`, `pstack.py`, campos en `Attempt`/`StartAttempt`, `Block.playbook`, `DE_KIND`, pruebas puras (`siguiente`, `comprueba`, `autores`) y una e2e con el GitHub falso donde un delegado entrega design.md sin secciones y entra el relevo. Acepta: la batería.
- **C** canario en un repo desechable con `implementa: glm, k3` y `revisa: opus`, un bloque fast y uno gate con `playbook: bug-fix`. Acepta: PR mergeado por bloque y `estado --json` muestra los pasos.

## Next implementation step

Escribir `motor/poteto.py` con la tabla `JUGADAS`, `siguiente()` y `comprueba()` puros, y su prueba `test_poteto.py` que recorre una ronda feature completa con dos candidatos de diseño y un `paso_invalido` en medio.
