# `taller`: supervisor determinista local para planes de Orbit

## Problem

David y el lead planean; hoy un LLM (claw) hace de scheduler releyendo un
`PROMPT.md` de 30 KB y un `estado.md` de texto libre, entrega trabajo
tecleando en ventanas tmux, y depende de que cada agente "lo despierte" con
`nohup openclaw agent`. Las diez familias del catálogo salen de ahí: el
estado vive en la memoria de un LLM y en pantallas, el despertar es frágil,
los proxies (LISTO, ledger, composer) se toman como hechos, y cada papel tiene
un solo proveedor. Restricciones que el diseño debe honrar tal cual: el plan
es la autorización completa (merge, deploy, migraciones previstas); nunca
esperar por una prueba; sin vigía ni tablero, solo avisos por Telegram en
lenguaje llano (≤8 líneas, sin SHAs ni rutas); el lead (Claude) revisa todo
bloque de código y no hay revisión cruzada con otros modelos; flujo PR draft →
APROBADO → ready → `completa` → `gh pr merge`; nunca `--no-verify`, nunca push
a master; las escrituras a producción las corre el coordinador con scripts del
PR aprobado; los agentes nunca preguntan; relevo automático por cuota (Muse →
GLM, Kimi de relevo). Hechos medidos que condicionan la forma: todos los CLIs
corren sin TTY con un flag de "sin preguntas" y salida JSON; un wrapper zsh
convierte `muse`/`claude` en tmux salvo que se llamen por ruta absoluta;
`completa` solo corre en `push` a master o `workflow_dispatch` (no en PR); los
planes de Orbit ya son tablas `Task | Contenido | DoD | Depends | Status` con
etiquetas `[lane:fast|gate|release]`.

## Usage (caller's view)

Dos callers: el **lead** (Claude Code en la Mac, con David en el chat) y
**David** (solo lee Telegram; nunca escribe comandos). Nadie más habla con
`taller`. Los agentes no conocen `taller`: reciben un brief por archivo y
dejan efectos en git más un `result.json`.

### Quickstart del lead

```sh
# 1. Escribir el plan como hoy (plans/jev-ads-04.md) con una línea de cabecera:
#      `autopiloto: taller` y, si hay pasos de David, filas con [humano:<tipo>]
taller valida plans/jev-ads-04.md     # parser puro: DAG, bloques→PR, autorización derivada
#   ok  3 bloques (B: 4 filas gate, D: 2 release, C: 1 fast)
#   autoriza: merge×3, deploy D.1 (ejecucion/D.1/desplegar.sh + checklist.sh + rollback.sh),
#             migración 0075, config_version jev.bloqueo2
#   error D.2: fila release sin checklist.sh en el DoD → corrige antes de arrancar
# 2. El plan entra a master por su PR fast (como hoy). El daemon lo toma solo
#    al verlo en origin/master con la cabecera; o explícito:
taller arranca jev-ads-04
# 3. Cuando David pregunta algo en el chat, el lead consulta:
taller estado jev-ads-04              # tabla corta: bloque, estado, agente, desde cuándo, próximo hito
taller historia jev-ads-04 B          # eventos del bloque (append-only), para diagnóstico
taller hecho jev-ads-04 E.0a          # David terminó un paso humano → la fila sigue
taller nota jev-ads-04 B "usar _identidad de apply_harvest"   # llega al próximo brief del bloque
taller doctor                          # canario de cada CLI sin TTY; cuotas; gh; ssh lectura; Telegram
```

### Escenario 1: plan normal, de punta a punta

El daemon (launchd, `KeepAlive`) ve el plan en master, crea tres bloques
(B, D, C) y arranca B porque no depende de nada pendiente. Crea
`/Users/dn/dev/wt/taller/jev-ads-04-B` (nunca el checkout compartido), corre el
bootstrap del repo (`uv sync`, symlink del venv), y lanza
`/Users/dn/.local/bin/muse exec --yolo --json --output-schema result.schema.json
--prompt-file brief-B-1.md` con stdin en `/dev/null`. Muse hace B.1..B.4 en
commits, empuja y termina. El supervisor **no lee el texto**: comprueba que la
rama remota tiene commits nuevos sobre la base, que `pre-commit` y las pruebas
focalizadas que el plan nombra pasan en ese SHA, y abre el PR draft. Lanza la
revisión: `claude -p --permission-mode bypassPermissions --json-schema
verdict.schema.json` en otro worktree en el SHA exacto. Veredicto CAMBIOS con un
BLOQUEANTE con su comando de reproducción → nuevo intento del implementador con
el hallazgo; nuevo SHA → nueva revisión. APROBADO sobre el SHA H → `gh workflow
run quality.yml --ref <rama>` (la `completa` en la rama) + `gh pr ready`. Con
`completa`, `gate` y el comentario de ai-review del SHA H en verde y sin High
abierto, el merge gate emite un `MergeTicket` y se corre `gh pr merge --squash
--match-head-commit H`. D arranca al ver B en master. Tras el merge de D, el
supervisor corre `desplegar.sh` y `checklist.sh` **del SHA de master** por
`ssh goncloud`; checklist verde = DoD con datos reales. David recibe:

> Listo JEV ADS 04, parte 2 de 3: ya puedes bloquear búsquedas desde Cortes.
> Lo probé con los datos reales: 50 propuestas en México y 24 en Estados Unidos.
> Sigue el cierre; te aviso cuando quede.

### Escenario 2: cuota agotada a las 3 a.m.

03:07, Muse termina con `429 weekly limit... resets Thu 09:00`. El clasificador
(regex por proveedor, fuera del LLM) produce `QuotaExhausted(reset_at=jue 09:00)`.
El supervisor escribe `ProviderCooldown(muse, hasta jue 09:00)` y, en el mismo
tick, crea el intento 3 de B con el siguiente de la escalera de la política
(`opencode run --auto -m zai-coding-plan/glm-5.3`), **en el mismo worktree y la
misma rama**: el brief dice "continúa; ya hay 2 commits y cambios sin
commitear de un agente anterior". Nadie recibe aviso: la entrega no cambia de
fecha. El jueves 09:00 Muse vuelve a ser elegible sin que nadie quite un
"PAUSADO" (no existe ese estado). Si **todos** los implementadores están en
cooldown y la fecha de entrega se mueve más de 6 h, David recibe un aviso:
"Se acabaron los créditos de los agentes que programan; sigo el jueves 9:00
solo." `taller estado` muestra `B  implementando  glm-5.3 (relevo de muse)  03:07`.

### Escenario 3: paso que pide permiso

a) **Un agente intenta algo que no le toca** (`gh pr merge`, `git push origin
master`, `--no-verify`, `ssh goncloud psql -c "UPDATE ..."`). En su `PATH` va
primero `~/.local/share/taller/shims/`; el shim evalúa la política y responde
**sin preguntar a nadie**: niega con exit 77 y un texto que dice qué hacer
("las escrituras a producción las corre el supervisor tras el merge: escribe el
script en ejecucion/<fila>/ y sigue"). El agente se adapta. Nada queda colgado:
no hay TTY, y un `read` de stdin recibe EOF.

b) **Un paso que solo David puede hacer** (login de Seller Central, 2FA,
pago). Va en el plan como fila `[humano:login]`, o lo declara el agente en
`result.json` con `needs_human.kind ∈ {login, 2fa, captcha, pago}` (cualquier
otro tipo se rechaza como "no preguntes" y se re-encarga). La fila pasa a
`HUMAN_HANDOFF`; el resto del plan sigue. David recibe una sola vez:

> Necesito tu mano 2 minutos: entra a Seller Central con tu cuenta y acepta
> el permiso de anuncios para Orbit. Cuando acabes, dile a Claude
> "ya quedó lo de Seller Central". Lo demás sigue avanzando.

El lead corre `taller hecho jev-ads-04 E.0a` y la fila continúa.

## Shape

### Datos primero

- **`PlanSpec`** (inmutable, fijado al SHA de master del plan): `Block`s, cada
  uno = una rama y un PR, con `Row`s ordenadas (un commit por fila), `lane`,
  `depends`, `dod`, `release: ReleaseSpec | None` (rutas de `desplegar.sh`,
  `checklist.sh`, `rollback.sh`), `human: HumanKind | None`. `Authorization`
  se **deriva** del plan (qué merges, qué scripts de deploy, qué migraciones);
  no hay otra fuente de permisos, per encode-lessons-in-structure.
- **Log de eventos append-only en SQLite** (`~/.local/state/taller/taller.db`,
  WAL). La única verdad. El estado de cada bloque es `fold(eventos)`; las
  tablas `block_state`, `attempt`, `provider_cooldown` y `outbox` son
  proyecciones reconstruibles. Un solo escritor: el daemon. El CLI del lead
  **no escribe la DB**: deja un archivo en `inbox/` que el daemon ingiere
  (per separate-before-serializing-shared-state). Los agentes no escriben la
  DB: dejan efectos en git y `result.json` en su worktree.
- **Máquina de estados por bloque** (enum cerrado, transiciones en una función
  pura `step(state, fact) -> (state, [Action])`): `WAITING_DEPS → IMPLEMENTING
  → VERIFYING → REVIEWING → (FIXING → VERIFYING → REVIEWING)* → CI → MERGING →
  DEPLOYING → PROVING → DONE`, más `HUMAN_HANDOFF` y `REPLANNING`. **No existe
  `FAILED` ni `PAUSED`**: un fallo es un `Fact` que produce otra `Action`
  (reintento, relevo, re-encargo, replan), per never-block-on-the-human.
- **`Attempt`** (lease): bloque, papel, proveedor, pid/pgid, worktree,
  `deadline`, `last_progress_at`, `session_id` para reanudar.
- **`Policy`** (`~/.config/taller/policy.toml` + `.taller/repo.toml` en cada
  repo, versionado con el código): escaleras de proveedores por papel,
  concurrencia por proveedor, reglas de shims (allow/deny/handoff), umbrales.
  La regla vive en un archivo del repo, no copiada de loop en loop (patrón e).

### Flujo

```
daemon tick (cada 10 s, sin LLM, cero tokens)
  ingest(inbox, procesos terminados, hechos de gh/ssh)  → Facts → append
  snapshot = fold(log)
  actions  = decide(snapshot, policy, now)               # pura, idempotente
  for a in actions: execute(a)                           # shell delgado
```

`decide` es pura y testeable con tablas; `execute` hace un solo efecto y
primero **comprueba si el efecto ya existe** (PR abierto para la rama, merge ya
hecho, deploy ya corrido en ese SHA según el `checklist`), así correr dos veces
o caerse a la mitad no duplica nada (per make-operations-idempotent). Cada
`Action` lleva una clave determinista `(bloque, estado, n_intento, tipo)`.

### Módulos (ningún trazo cruza más de tres archivos)

| Módulo | Sabe | Interfaz pública |
| --- | --- | --- |
| `plan.py` | Formato de planes de Orbit | `parse_plan(md, sha) -> PlanSpec \| PlanErrors` |
| `machine.py` | Estados, transiciones, gates | `decide(snapshot, policy, now) -> list[Action]`; `merge_gate(facts) -> MergeTicket \| Denied` |
| `agents.py` | Cada CLI: flags, salida, errores | `run(role, brief, worktree, policy) -> AttemptHandle`; `collect(handle) -> AttemptOutcome` |
| `effects.py` | git, gh, ssh, Telegram | `execute(action) -> list[Fact]` |
| `store.py` | SQLite | `append(fact)`, `fold() -> Snapshot` |
| `notify.py` | Plantillas en español llano | `render(event) -> PlainSpanish` |

Profundidad: el lead ve 6 verbos (`valida`, `arranca`, `estado`, `historia`,
`hecho`, `nota`) y David ninguno. Detrás quedan relevo, reintentos, leases,
gates, CI, deploy y reconciliación tras reinicio.

### Decisiones que cargan peso

1. **Verificar efectos, no texto.** `VERIFYING` solo pasa si: la rama remota
   existe con commits nuevos sobre la base (`git ls-remote`), los hooks de
   pre-commit pasan en ese SHA, las pruebas focalizadas que nombra la fila
   pasan en ese SHA, y `result.json` valida contra su esquema y cita ese SHA.
   Un `LISTO` sin SHA es imposible: el tipo `Delivered` exige `sha: Sha` y el
   parser lo construye solo si `git cat-file -e` lo encuentra.
2. **El merge gate es el único camino al merge.** `MergeTicket` solo lo
   construye `merge_gate`, que exige sobre **el mismo** SHA de cabeza:
   veredicto APROBADO del revisor, `completa` y `gate` verdes, comentario de
   ai-review leído sin High abierto, PR no draft. `execute(Merge)` solo acepta
   un `MergeTicket` y pasa `--match-head-commit`. Un push posterior invalida
   el ticket por construcción (otro SHA).
3. **El revisor es Claude, headless, en un worktree de solo lectura del SHA**,
   con un esquema de veredicto que codifica la regla 4: `CAMBIOS` sin al menos
   un `BLOQUEANTE` con `repro_cmd` se reinterpreta como `APROBADO` + hallazgos
   no bloqueantes; los no bloqueantes de una línea se corrigen en la misma
   ronda ("al 100" sin pendientes chicos), el resto se vuelve fila del plan y
   se nombra en el PR. El mismo bloqueante en dos rondas seguidas no va a
   David: va a `ARBITRATE` (Claude con el modelo de "strongest judgment" lee
   ambas rondas y decide), porque el operador del loop es el lead.
4. **Relevo por clasificación, no por juicio.** `classify(exit, stderr,
   stream) -> Outcome` con regex por proveedor (`QuotaExhausted(reset_at)`,
   `AuthExpired`, `Transient`, `NoProgress`, `AgentFault`). Cuota →
   cooldown con fecha y siguiente proveedor en el mismo worktree. La escalera
   del **revisor** solo contiene Claude (Max → `opencode/claude-opus-5-5` por
   API): mismo modelo, no es revisión cruzada.
5. **Sin TTY, siempre.** Binarios por ruta absoluta (el wrapper zsh los vuelve
   tmux), `stdin=/dev/null`, grupo de procesos propio, timeout duro y
   detección de no-avance (sin eventos JSONL ni cambios en el worktree en N
   min → kill del grupo → `NoProgress`). Un prompt de permiso no puede
   colgar nada: o el flag lo evita, o lee EOF y falla, o el timeout lo mata.
6. **Producción solo desde master.** `DEPLOYING` corre por `ssh goncloud` los
   scripts del `ReleaseSpec` tomados del SHA de master del merge (md5 en el
   evento). Si `checklist.sh` falla: `rollback.sh` y el bloque vuelve a
   `FIXING` con la salida del checklist como hallazgo. Los agentes nunca
   tienen camino de escritura a producción (shim de `ssh` con lista de
   comandos de lectura).
7. **Probar no espera.** `PROVING` corre el DoD con datos reales una vez,
   inmediatamente. Las filas `[stage:medicion]` se cumplen al **programar** la
   medición (`at` en goncloud) y no frenan el cierre. "Entregado" solo se
   avisa con `PROVING` verde; el aviso nombra lo que no se probó contra prod.
8. **claw sale del camino crítico.** El scheduler es código; el despertar es
   el tick del daemon; los avisos salen directo. Ver Alternativas.

Lo que deliberadamente **no** hace: no interpreta el plan con un LLM, no
reescribe el plan (el lead lo hace y `taller` versiona), no corre la batería
completa local (eso es CI), no tiene tablero ni vigía, no le pide nada a David
salvo pasos `HumanKind`.

## Cada familia del catálogo

| # | Familia | Cómo queda imposible o se auto-repara |
| --- | --- | --- |
| 1 | Permisos (carril 7 h en un prompt, `rm -rf` sin respuesta, Muse "approval prompts disabled") | Sin TTY + `stdin=/dev/null` + flags de "sin preguntas" fijados en `AgentSpec`; `taller doctor` corre un canario por CLI y **retira de la escalera** al que no termina sin stdin en 60 s. Shims responden allow/deny al instante, nunca "ask". Timeout duro + `NoProgress` matan cualquier cuelgue. |
| 2 | Entrega no confirmada (composer, send-keys, Ctrl-C, copy-mode) | No hay tmux ni tecleo: el brief es un archivo y el proceso se lanza con `subprocess`. "Entregado" = pid vivo con lease en la DB. |
| 3 | LISTO falso, "cerrado" sin cerrar, "al 100" con pendientes | `Delivered` exige SHA existente en origin; `VERIFYING` corre hooks y pruebas sobre ese SHA. `DONE` exige `PROVING` verde. El cierre de plan exige cero hallazgos de una línea abiertos. |
| 4 | Coordinador sin herramientas (#470 sin veredicto, cron en sesión equivocada) | El coordinador es un proceso con su propio código; no hay sesiones LLM que pierdan herramientas. El merge sin veredicto es irrepresentable (`MergeTicket`). |
| 5 | Costo del sondeo (63k-360k tokens por despertar) | El tick no usa LLM: lee procesos y `gh` con ETag. Los tokens solo se gastan en implementar y revisar. |
| 6 | Cuota/auth y PAUSADO de 5 h | `classify` → cooldown con `reset_at` → siguiente proveedor en el mismo worktree. El cooldown vence solo. `AuthExpired` → siguiente proveedor + un aviso a David solo si nadie del papel queda. |
| 7 | Reinicios y estado perdido | launchd `KeepAlive`+`RunAtLoad`; estado = log SQLite WAL; al arrancar, `reconcile()` marca `AttemptLost` los pids muertos y relanza con `--resume <session_id>` si el CLI lo soporta, o brief de continuación. `caffeinate -i` mientras haya leases. |
| 8 | Esperas humanas (PR verde retenido 11 h, "mismo bloqueante dos rondas" a David) | No hay compuertas humanas fuera de `HumanKind`. Medición no bloquea. Doble bloqueante → `ARBITRATE` del lead headless. Merge en cuanto el gate pasa. |
| 9 | Merge adelantado (#470, #428, #412) | `merge_gate` sobre el mismo SHA: veredicto, `completa` (disparada por `workflow_dispatch` en la rama), `gate`, ai-review sin High. Única ruta al merge. |
| 10 | Avisos técnicos | `notify.render` desde una tabla cerrada de eventos → plantillas en español; `lint_plain` rechaza SHAs, rutas, nombres de función, términos de una lista negra y >8 líneas; una prueba unitaria recorre todas las plantillas. |

## Tradeoffs accepted

- Aceptamos que la Mac sea el único host (dormir, reiniciar, tapa cerrada) a
  cambio de usar las suscripciones, el `ssh goncloud` y los worktrees que ya
  existen; launchd + `caffeinate` + reconciliación cubren reinicio, no un
  apagón largo.
- Aceptamos regex por proveedor para cuota/auth, que se rompen cuando cambia
  un mensaje, a cambio de cero tokens; un error no clasificado cae en
  `AgentFault` (reintento acotado y luego relevo), nunca en espera.
- Aceptamos que los shims sean barandal y no frontera: un agente puede llamar
  `/opt/homebrew/bin/gh` por ruta absoluta. La frontera real es el gate
  (ninguna escritura a prod ni merge sale de un agente sin que el daemon lo
  detecte: `master` movido sin `MergeTicket` → evento `Intrusion` y aviso).
- Aceptamos que el plan se fija a un SHA: un cambio del lead a mitad de corrida
  pasa por `taller replan` (diff por id de fila), no por edición en caliente.
- Aceptamos un esquema de plan un poco más estricto (`autopiloto: taller`,
  `[humano:*]`, `ReleaseSpec` en el DoD) a cambio de que `valida` atrape el
  error al escribir el plan y no a las 3 a.m.

## Alternatives considered

- **claw con mejores herramientas (lo de hoy, reparado).** Mantiene a un LLM
  como scheduler: cada despertar cuesta tokens, el estado vive en texto que el
  LLM interpreta, y la sesión puede perder herramientas (#470). La interfaz
  "un PROMPT.md por loop" expone al lead todo el procedimiento en cada plan.
  Pierde en profundidad: el lead reescribe la orquestación en cada loop.
- **GitHub como scheduler** (issues/labels como estados, runner self-hosted en
  la Mac, Actions dispara agentes). Estado durable fuera de la Mac, pero el
  estado se reparte en labels (fuga: cada transición es una convención), el
  relevo por cuota y los leases quedan en YAML, y cada tick cuesta minutos de
  Actions. Útil luego como canal de eventos, no como cerebro.
- **GrokBot / Cursor cloud agents tal cual.** Computadora en la nube y Auto
  Review listos, pero no usa las suscripciones de la Mac, no tiene `ssh
  goncloud`, Bugbot no se dispara desde sus agentes, y el revisor no sería el
  lead. Se toma de ahí: handoff solo para login/2FA/captcha/pago, estados de
  atención, Auto Review como política.
- **Motor de workflows (Temporal/Prefect).** Durabilidad de sobra, pero un
  servidor más que operar para ~5 bloques por plan; SQLite + log + fold cubre
  lo mismo a esta escala.

## Open questions and risks

1. ¿Acepta David que "el lead revisa" sea Claude headless con las reglas de
   revisión del lead (skill + `revision-trampas`), en vez de la ventana
   abierta? Es la única forma de quitar el tecleo (familia 2).
2. ¿Se autoriza el relevo del revisor a Claude por API (`opencode/claude-opus-5-5`,
   de pago) cuando se acabe la cuota Max? Si no, la revisión espera el reset
   (los implementadores siguen con otros bloques) y David recibe un aviso.
3. ¿El token del bot de Telegram puede copiarse al Keychain de la Mac? Si no,
   el aviso sale por `ssh goncloud python3 envia_recordatorio.py` (más frágil).
4. ¿Protección de rama en master que exija `gate` y prohíba merge salvo
   desde el token del daemon? Haría el shim de `gh pr merge` una frontera real.
5. ¿claw se queda como interfaz de chat para David (solo `taller estado` y
   `taller nota`) o se retira? Propuesta: retirarlo del loop ya; decidir lo
   del chat después de un plan real.
6. Riesgo: un agente relevado hereda cambios sin commitear de otro modelo y
   los malinterpreta. Mitigación: el brief de relevo incluye `git diff --stat`
   y la instrucción de commitear o descartar primero.
7. ¿Cuántos `claude -p` paralelos aguanta la cuota Max sin chocar con la
   sesión interactiva del lead? Hace falta medirlo antes de subir la
   concurrencia del revisor de 1 a 2.

## Prototype scope (≤1 h de implementación)

Un solo archivo `taller_proto.py` (≈400 líneas) + `demo.sh`, en
`$SCRATCH/taller-proto/`, sin tocar Orbit ni la red:

- **Repo desechable**: `git init --bare origin.git` + clon de trabajo; plan
  `plans/demo.md` con 3 bloques: `A` (gate, 2 filas), `B` (gate, depende de
  A), `D` (release, `desplegar.sh` copia un archivo a `./prod/`, `checklist.sh`
  lo verifica, `rollback.sh` lo borra).
- **Agentes falsos**: `fake-agent.sh <proveedor>` lee el brief, edita un
  archivo, commitea, empuja a `origin.git` y escribe `result.json`. Modos por
  variable: `quota` (imprime `429 ... resets at <iso>` y sale 1 la primera
  vez), `stdin` (hace `read` y debería colgarse; con `/dev/null` falla al
  instante), `liar` (escribe `result.json` con un SHA inexistente).
- **Revisor falso**: devuelve `CAMBIOS` con un BLOQUEANTE + `repro_cmd` en la
  primera ronda de `A`, `APROBADO` después; una variante devuelve `CAMBIOS`
  sin repro (debe tratarse como APROBADO + no bloqueante).
- **CI falso**: el supervisor corre `./ci.sh` en un worktree limpio del SHA de
  cabeza y registra `CiResult(sha, green)`. **Merge**: `git merge --ff-only`
  en `origin.git` solo con `MergeTicket`. **Notificador**: escribe al
  `outbox/` y pasa `lint_plain`.
- **Lo que prueba `demo.sh`** (aserciones, no lectura de logs): (1) los tres
  bloques en `DONE` y `prod/` con el archivo; (2) el evento de relevo de
  `fake-muse` a `fake-glm` con cooldown; (3) el `liar` nunca pasa de
  `VERIFYING`; (4) ningún merge antes de APROBADO + CI en el mismo SHA
  (se recorre el log); (5) `kill -9` al daemon en `IMPLEMENTING` y relanzarlo
  termina igual sin duplicar PR/merge/deploy; (6) el modo `stdin` no tarda
  más de 5 s; (7) todos los avisos pasan el lint.

Fuera del prototipo: launchd, `gh`, Telegram real, `ssh`, shims, `doctor`.

## Migration path for Orbit

1. **Sombra (1 plan fast).** Daemon con agentes reales (Muse → GLM → Kimi) y
   revisor `claude -p`, sobre un plan de docs (`[lane:fast]`), merge
   habilitado (es fast). claw sigue existiendo pero sin loop.
2. **Primer plan gate.** Siguiente plan de código; `taller valida` en el PR
   del plan; merge gate completo con `completa` por `workflow_dispatch`.
   El lead lee la primera revisión headless antes del merge (una vez) para
   calibrar el brief del revisor; después, nada.
3. **Primer plan release.** Fila D con `ReleaseSpec`; deploy por `ssh
   goncloud` desde el SHA de master; `PROVING` con datos reales.
4. **Retiro.** Se archivan `prompt-claw`, los `PROMPT.md`/`estado.md` de
   loops, `avisar.sh`, `composer-real.sh` y la dependencia del worktree
   `agent-work-host`. La skill del lead pasa a "escribir un plan que
   `taller valida` acepte". Se actualizan las memorias que citan claw como
   coordinador.

## Synthesis decision

## Next implementation step

Escribir `plan.py::parse_plan` contra `plans/jev-ads-03.md` real (prueba con
tabla: 4 bloques, dependencias, `ReleaseSpec` de D.1) y `machine.py::decide`
con pruebas de tabla de cada transición, antes de tocar procesos.
