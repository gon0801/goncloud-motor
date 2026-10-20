# Diseño C3: "un bot por computadora" con buzón durable (GrokBot-shaped)

## Problem

El sistema actual hace que un LLM (claw) sea el único scheduler: relee un PROMPT.md de 30 KB, teclea encargos en ventanas tmux, juzga pantallas con heurísticas y se despierta con `nohup openclaw agent`. De ahí salen los 10 temas del catálogo: permisos que paran carriles (1), entrega por pantalla (2), LISTO falso (3), coordinador sin herramientas (4), sondeo caro (5), cuota (6), reinicios (7), esperas humanas (8), merge adelantado (9), avisos técnicos (10).

Restricciones que el diseño respeta: el plan es autorización completa; nunca esperar por una prueba; sin vigía ni tablero; el lead revisa todo código; sin revisión cruzada como paso del lead (bots del PR y CI sí); relevo automático por cuota; avisos de ≤8 líneas en español llano; producción solo por scripts del PR aprobado ejecutados por un agente (el entorno bloquea al lead); nunca `--no-verify`, nunca push a master.

La causa raíz común: **el estado vive en pantallas y en la memoria de un LLM, y el avance depende de que alguien lo vigile**. C3 mueve el estado a un buzón durable en disco y el avance a un programa determinista (no un LLM) en cada computadora. Los LLM solo hacen trabajo de juicio.

## Usage (caller's view)

David no teclea comandos. El lead (Claude Code en la Mac) usa tres verbos:

```
orbitbot plan add plans/jev-ads-04.md     # valida el plan, lo registra como Proyecto, lo encola
orbitbot status                           # una tabla: bloque | estado | quién | desde
orbitbot say "usa Kimi para el bloque B"  # decisión del lead/David -> mensaje al orquestador
```

Todo lo demás es automático. David solo ve Telegram.

**Escenario 1: entrega normal.** El lead hace `plan add`. El plan trae bloques con `acepta:` (comandos verificables), `autoriza:` (merge, deploy, migración) y `humano:` (pasos solo humanos). La Mac implementa en un worktree, abre PR draft, el lead revisa, CI corre, se mergea y se despliega. David recibe:
> "Listo: ya funciona el bloqueo de anuncios nuevos en producción. Lo probé con datos reales: 3 de 3 casos bien. Nada pendiente."

**Escenario 2: cuota agotada a las 3 a.m.** El implementador Muse responde 429 en el bloque B. El host de la Mac (programa, no LLM) clasifica el error como `QUOTA`, marca el intento `abandonado:cuota`, y en el mismo segundo encola el bloque con el siguiente de la lista de relevo (GLM, luego Kimi), conservando la rama y el worktree (commits ya hechos). Muse queda "enfriándose" con `retry_at` leído del encabezado o 5 h por omisión; un temporizador del host lo reincorpora solo, sin que nadie quite un PAUSADO. David no recibe nada (no es un evento de entrega). Si TODOS los proveedores del rol están en cuota, el host duerme hasta el `retry_at` más cercano y sigue; solo entonces, si pasan 2 h, manda un aviso: "Voy más lento: se acabó el cupo de mis ayudantes; sigo solo cuando se renueve (≈ 05:10)."

**Escenario 3: paso que pide permiso.** El implementador quiere correr `rm -rf build/` y luego `git push --force-with-lease` a su rama. Nunca hay prompt interactivo: cada CLI corre con su flag sin preguntas, pero **cada acción cruza antes un Auto-Review** (hook PreToolUse o wrapper) con reglas en español: "rm dentro del worktree: permitir; rm fuera: bloquear; push a master: bloquear; escribir a Amazon/prod: solo si el plan lo lista en `autoriza:` y el SHA está APROBADO". El `rm` dentro del worktree pasa en 2 s; el push a master se bloquea con un mensaje al agente ("no permitido, usa PR") y el agente continúa por otra vía. Nada espera a un humano. Solo `humano:` (p. ej. un 2FA de Seller Central) genera un Handoff: el bloque se aparca, el resto sigue en paralelo, y David recibe un aviso con una sola acción clara.

## Shape

### Estructuras de datos (el centro)

1. **Mensaje tipado, inmutable, con id idempotente** (`Msg`): `Assign`, `Ack`, `Progress`, `Delivered`, `Verdict`, `QuotaHit`, `Handoff`, `HandoffDone`, `Decision`, `Notify`. Un mensaje se identifica por `(block_id, kind, attempt)`; reprocesarlo es no-op.
2. **Buzón por máquina** (`Mailbox`): directorio `~/.orbit/mail/<host>/{in,out,done,dead}/` con un archivo JSON por mensaje, escrito con `rename` atómico. Entre máquinas, sincronización por **git de un repo privado `orbit-bus`** (rama por host, cada escritor solo escribe en su rama: sin conflicto, per separate-before-serializing-shared-state) o, para latencia baja, HTTP sobre Tailscale con el mismo formato. Un mensaje entregado se confirma con `Ack`; sin Ack en N s el host remitente lo reenvía (at-least-once + idempotencia = efecto exactamente una vez).
3. **Máquina de estados por bloque** (`Block.state`): `PENDING -> ASSIGNED -> BUILDING -> PR_DRAFT -> IN_REVIEW -> CHANGES|APPROVED -> READY -> CI_GREEN -> MERGED -> DEPLOYED -> VERIFIED -> NOTIFIED`, más lateral `PARKED_HUMAN`. Transiciones son una función pura `step(state, msg) -> (state, [effects])`. No existe un estado "LISTO" que un agente pueda declarar: `VERIFIED` solo lo escribe el host al ejecutar los comandos `acepta:` y ver exit 0 y la salida esperada (per prove-it-works; mata el tema 3).
4. **Memoria de proyecto** (`Project`): un repo/archivo versionado `project/<id>/{plan.yaml, decisions.md, conventions.md, ledger.jsonl}`. `ledger.jsonl` es append-only y derivado del buzón; `decisions.md` ≤ 80 líneas, escrito solo por el lead/David vía `say`. Se pasa a cada agente como contexto fijo y pequeño (nada de `estado.md` de 290 KB; guard-the-context-window).

### Componentes

- **Host** (uno por computadora, ~300 líneas, launchd/systemd, KeepAlive): bucle determinista. Lee `in/`, aplica `step`, ejecuta efectos (lanzar CLI headless, correr verificación, publicar a `out/`). No llama a ningún LLM para decidir si avanzar. Reinicio = relee `in/` y el ledger y sigue (tema 7). Lanza el agente como subproceso `claude -p` / `muse exec` / `codex exec` / `grok -p` / `cursor-agent -p` con su flag sin preguntas, en un worktree propio, con timeout duro y salida JSON; captura el código de salida y los 429/401 por parser de errores por CLI (`QuotaDetector`).
- **Orquestador** (un solo bot; propuesta: proceso Python en la Mac Mini junto a claw, o directamente en la Mac si la Mini cae): dueño de la Project memory, descompone el plan en bloques con dependencias, asigna según `Roster` (capacidad, cuota, disponibilidad), reasigna en relevos. Es **programa con un LLM como asesor opcional** (p. ej. para redactar el brief); la asignación y el relevo son tabla de reglas. Si el orquestador muere, los hosts siguen terminando lo que tienen asignado; al volver, reconstruye del ledger.
- **Auto-Review** (política de acciones): lista de reglas en lenguaje natural + un pre-filtro determinista de patrones obvios (rm fuera del worktree, push a master, `--no-verify`, secretos en repos públicos, `ssh goncloud` con escritura). Para lo ambiguo llama a un modelo rápido barato con (regla, acción, plan) y recibe `allow|block|escalate`. Regla de GrokBot: "ask first" gana en conflicto. `escalate` NO espera a David: se convierte en `block` + el agente replanifica; solo si el bloque no puede avanzar sin esa acción, pasa a Handoff. Se instala como hook PreToolUse (Claude, Codex, Grok tienen hooks) o, en CLIs sin hooks (Muse, Cursor, Kimi), por un **shim de PATH** que envuelve `git`, `rm`, `ssh`, `gh`, `curl` y consulta al Auto-Review antes de ejecutar.
- **Revisor (lead)**: es un papel, no una ventana. El host de la Mac lanza `claude -p --json-schema veredicto.json` sobre el diff del PR con el brief fijo; la salida validada contra el esquema es el `Verdict`. Sin veredicto estructurado y ligado al SHA, la transición `-> READY` no existe (tema 9). Los bots del PR y CI son otra condición de la guarda, leída con `gh pr checks` y `gh api`, no del lead.
- **Merger/Deployer**: efectos del host. `gh pr merge --squash --match-head-commit <SHA_APROBADO>`; si CI no está verde sobre ese SHA o el bot dejó un High abierto, la guarda falla y vuelve a `CHANGES` automático (tema 9). Deploy: ejecuta el script del PR por `ssh goncloud` solo si el bloque lista `autoriza: deploy` y el SHA mergeado es el aprobado; los scripts salen del PR, no se improvisan (compatible con "el lead no escribe a prod").
- **Handoff**: `Handoff{block, que_hacer_en_una_frase, como_se_reanuda}`; reanuda con `HandoffDone` (David responde por Telegram con una palabra) o con una sonda automática (p. ej. el login ya existe). Solo para pasos que el plan marcó `humano:` o que Auto-Review no puede resolver nunca.
- **Notifier**: función pura `render(event) -> str` de ≤8 líneas en español, a partir de plantillas por evento (no un LLM): `Entregado`, `Atorado-necesita-David`, `Más lento por cuota`. Las plantillas no admiten SHAs ni rutas en el tipo (campo `str` sin patrón de hex; test lo verifica). Envío por `openclaw message send` o Bot API de Telegram directa con reintentos y cola de salida en `out/`.
- **Estados de atención** (a lo GrokBot) = vista derivada del ledger: `trabajando | necesita atención | resultado nuevo`; `orbitbot status` la imprime; sin tablero, sin vigía: es una lectura puntual cuando el lead la pide.

### Profundidad de interfaz

La superficie pública son tres comandos y un archivo de plan. Escondidos: reintentos, relevos, clasificación de errores, worktrees, guardas de merge, plantillas de aviso. El agente implementador ve un solo contrato: "haz el bloque en este worktree, deja commits, escribe `delivered.json` con `{sha, comandos_ejecutados}`"; no ve buzones ni estados.

### Qué cada pieza evita (despertar sin vigía)

El avance lo da el host (bucle con `sleep 5`, KeepAlive), no un LLM ni un `nohup` encadenado. Cada máquina se auto-despierta: costo de sondeo = leer un directorio local (cero tokens; tema 5). Un temporizador interno por bloque (deadline 4 h) lo reasigna si no hay latido del proceso hijo.

## Cómo se maneja cada tema del catálogo

| # | Tema | Mecanismo estructural | Por qué ya no puede pasar |
|---|---|---|---|
| 1 | Permisos | Cada CLI con su flag sin preguntas fijado en el `Roster` (tabla, no memoria); Auto-Review decide allow/block/escalate; `escalate` = bloquear y replanificar, nunca esperar | No hay prompt interactivo posible: el lanzador falla al arrancar (`launch_check`) si el flag falta |
| 2 | Entrega no confirmada | No se teclea en ventanas: el host lanza subproceso headless con prompt-file; entrega = `Assign`+`Ack` en el buzón con reenvío | Sin pantalla, sin composer, sin Ctrl-C ni copy-mode |
| 3 | LISTO falso/parcial | `VERIFIED` solo lo escribe el host tras correr `acepta:` y exigir exit 0 + datos reales listados en el plan; `Delivered` sin SHA existente en git se rechaza | El agente no puede declarar el estado final; "al 100" = todos los `acepta:` pasan, sin cola de "luego": un hallazgo no bloqueante chico se corrige en la misma ronda (guarda `pendientes==0`) |
| 4 | Coordinador sin herramientas | El orquestador es un programa con capacidades fijas; el contrato de arranque (`preflight`) comprueba `gh`, `ssh goncloud` (lectura), CLI del roster y falla ruidoso antes de aceptar un plan | No hay "sesión equivocada": no hay sesiones de chat que ejerzan de scheduler |
| 5 | Costo del sondeo | Cero LLM en el sondeo; los LLM solo corren cuando hay trabajo de juicio (implementar, revisar) | El costo base de vigilar es 0 tokens |
| 6 | Cuota/auth | `QuotaDetector` por CLI + `Roster` con lista de relevo y `retry_at`; rama y worktree se conservan; reincorporación automática por reloj | Sin PAUSADO manual; sin proveedor único por papel (mínimo 2 por rol exigido por `plan add`) |
| 7 | Reinicios/estado | Estado en archivos con rename atómico + ledger append-only; host con KeepAlive; las sesiones de agente son desechables (se reanudan con `--resume` o se relanzan desde la rama) | Reiniciar la Mac solo pausa; al volver el host relee y continúa; reinstalar no pierde "crons" porque no hay crons |
| 8 | Esperas humanas | Prohibida la transición `-> WAIT_HUMAN` salvo `humano:` declarado; compuertas de prueba corren en paralelo y no bloquean merge/deploy si no están en `acepta:`; "mismo bloqueante dos rondas" -> el orquestador cambia de implementador o descompone el bloque (decide la tabla, no David) | La única espera permitida está tipada y tiene aviso de una acción |
| 9 | Merge adelantado | Guarda de merge pura: `verdict.sha == pr.head && verdict==APROBADO && completa==verde(sha) && bot_high==0` | Sin veredicto estructurado el efecto `merge` no se genera; `--match-head-commit` cubre la carrera |
| 10 | Avisos | `Notifier` por plantilla, ≤8 líneas, tipo sin SHAs/rutas, test de aprobación | Un LLM no redacta el aviso al usuario final |

## Tradeoffs accepted

- Aceptamos construir y mantener un host propio (~300 líneas por máquina) a cambio de quitar al LLM del papel de scheduler; es código nuevo con sus propios bugs, pero se prueba con tests deterministas.
- Aceptamos at-least-once con idempotencia a cambio de no necesitar un broker; el buzón en git es lento (segundos) pero auditable con `git log`.
- Aceptamos que `escalate` degrade a `block`: un agente a veces rodeará una acción útil, a cambio de que jamás se pare esperando. Los falsos bloqueos se ven en el ledger y se afinan las reglas.
- Aceptamos un modelo barato de Auto-Review con riesgo de falso permitir; por eso lo irreversible (push a master, escritura a prod, secretos) está en el pre-filtro determinista y en el hook del agente, no solo en el LLM.
- Aceptamos que la revisión del lead corra como `claude -p` en el host (no una ventana viva): pierde el diálogo interactivo, gana reproducibilidad. Para casos difíciles el lead puede reabrir a mano.
- Aceptamos de uno a dos proveedores extra de pago ocasional para que "mínimo 2 por rol" sea cierto.

## Alternatives considered

1. **Cursor Cloud Agents / `cursor-agent worker` como columna vertebral.** El `worker` (verificado con `--help`) registra la Mac como "My Machines" personal sin Enterprise; Cursor orquesta el agente en nuestra máquina y expone `/healthz`. Ventaja: es lo más cercano a GrokBot. Pierde por: (a) lock-in total en cuenta Cursor y sus modelos/cuota (otro punto único de falla; viola el patrón d), (b) Bugbot no se puede disparar desde agentes lanzados así, lo que justo es el hueco que necesitamos para revisión de PRs (aquí lo reemplazan ai-review/CodeRabbit/CI que ya existen y el lead), (c) el pool de equipo exige Enterprise y llave de cuenta de servicio, (d) la política de aprobaciones y los estados viven en su nube, no en nuestro ledger. Decisión: `cursor-agent -p --yolo --trust` entra como UN implementador más del `Roster` (útil por su cuota independiente); `worker` queda como opción para ejecutar tareas disparadas desde la web, no como núcleo. Interfaz: oculta mucha complejidad, pero la expone en cuota, privacidad y cambios de producto.
2. **Seguir con claw pero con un scheduler determinista a un lado (cron + script).** Menos código nuevo; pero mantiene tmux/send-keys y el estado en texto libre; los fallos 2, 3 y 7 sobreviven. Interfaz grande hacia el agente (pantalla).
3. **Un solo servidor central (Postgres/Temporal) en goncloud.** Una fuente de verdad, workflows durables. Pierde: la Mac depende de la red/servidor para trabajar, y goncloud es producción (no se mezcla); sobra un broker para una sola persona. Es el camino si el sistema crece a >5 máquinas.
4. **Claude Code `--bg` / Agent SDK como único motor.** Muy simple, buen manejo de permisos por hooks; pero un solo proveedor y cuota semanal Max (tema 6).

## Open questions and risks

- ¿El orquestador vive en la Mini (junto a claw, con riesgo de gateway congelado) o en la Mac (se apaga con la Mac)? Propuesta: Mac por omisión, Mini como respaldo; ¿aceptas dos instancias con elección por latido?
- ¿Muse/Kimi/Cursor tienen hooks PreToolUse reales? Muse tiene Stop/Notification vacíos; si no, el shim de PATH es la única barrera y un agente podría llamar un binario con ruta absoluta. ¿Basta con eso o se corre cada agente como un usuario/sandbox de macOS distinto?
- ¿Qué modelo corre el Auto-Review y con qué cuota? Si cae, la regla por omisión es `block` para lo no listado en el plan; confirma que es aceptable.
- ¿El bus en git (repo privado `orbit-bus`) o HTTP sobre Tailscale? Git es auditable; HTTP es instantáneo.
- Riesgo: `acepta:` mal escrito por el lead da un VERIFIED vacío. ¿Se exige que cada bloque de código tenga al menos un `acepta:` con datos reales, rechazado por `plan add` si falta?
- Riesgo: el Auto-Review como cuello de botella de latencia (2 s por acción). Mitigar con caché de decisiones por patrón.
- ¿Windows (`gwpc`, Hermes) entra en la primera versión? Propuesta: no; el host es portable pero se despliega después.

## Prototype scope (< 1 h de implementación)

Repo desechable local `/tmp/proto-repo` con un `hello.py` y un test que falla. Todo en **una sola máquina**, sin red, sin git bus, sin Telegram real.

1. `Msg` + `Mailbox` sobre directorios (rename atómico) y `step()` puro con la máquina de estados reducida: `PENDING -> BUILDING -> IN_REVIEW -> APPROVED -> MERGED -> VERIFIED -> NOTIFIED`.
2. `Host.run_once()` que lanza `claude -p` (o `grok -p`) con flag sin preguntas en un worktree, y valida `delivered.json`.
3. `AutoReview` con 4 reglas (rm fuera del worktree, push a master, `--no-verify`, escritura remota) como pre-filtro determinista; sin LLM todavía; una acción bloqueada devuelve mensaje y el agente continúa.
4. Revisión: `claude -p --json-schema` sobre el diff -> `Verdict` ligado al SHA.
5. Merge local a una rama `main` del repo desechable con guarda del SHA; verificación ejecuta `pytest` (el `acepta:`); `Notifier` imprime el aviso (plantilla) a stdout.
6. Dos pruebas de falla: (a) simular 429 con un CLI falso que sale con código y mensaje de cuota -> relevo al segundo CLI falso; (b) matar el host a mitad y reiniciarlo -> termina sin duplicar el merge.

Criterio de éxito: `orbitbot plan add demo.yaml` termina en `NOTIFIED` con el test en verde, sobrevive (a) y (b), y no hay ni un prompt.

## Migration path for Orbit

1. **Sombra (día 1-2):** el host corre en la Mac sobre un bloque ya mergeado de jev-ads-03 en un clon, comparando su `Verdict` con el real. No toca claw.
2. **Un plan real chico con `autoriza:` limitado a merge** (sin deploy): bloque de docs/chore (carril fast). Se reutilizan CI `rapido/completa/gate`, bot ai-review y CodeRabbit sin cambios; la guarda de merge los lee.
3. **Código con deploy:** el script de despliegue ya vive en el PR; el efecto `deploy` hace `ssh goncloud` con ese script solo con `autoriza: deploy`. Las lecturas ssh de prod (sondas de `acepta:`) ya están permitidas; las escrituras las ejecuta el host/agente por el script aprobado, no el lead.
4. **Apagado de claw loops** uno a uno: `avisar.sh` se reemplaza por el `Notifier`; los PROMPT.md/estado.md se archivan; la skill `prompt-claw` se sustituye por `plan add`.
5. **Segunda máquina:** Mini o goncloud-runner con su host y el bus; después Windows.
6. Cada paso se prueba con datos reales antes de decir "listo" (regla de David) y sin esperar a pruebas de más de 1-2 días.

## Synthesis decision

(vacío; lo llena arena)

## Next implementation step

Escribir `step()` y los tipos `Msg` con tests de idempotencia (reprocesar un mensaje no cambia el estado), antes de cualquier proceso o CLI.
