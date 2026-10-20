# motor: taller crecido a un motor de planes único para las tres máquinas (candidato 1)

## Problema

David quiere un solo motor de planes, idéntico en la MacBook, la Mini y la PC, que corra cada
plan completo en la máquina donde se entrega, retome solo tras un apagón, trabaje en cualquier
repo y lo usen igual el lead, claw (OpenClaw) e Hermi (Hermes). Ya existe un núcleo que cumple
las reglas difíciles (taller: daemon sin modelo, log append-only, `decide` puro, `MergeTicket`,
relevo por clasificación, avisos con plantilla), pero es un prototipo de una sola máquina POSIX,
un solo repo, GitHub falso, despliegue a una carpeta y la configuración en un archivo local.
Lo que hace que la forma no sea obvia:

- Tres sistemas operativos con arranques distintos: la MacBook tiene FileVault (no arranca sin
  contraseña), la Mini tiene auto-login, la PC solo arranca cosas al iniciar sesión y Muse vivirá
  en WSL.
- La cuota es de la **cuenta** (Sonnet y Opus comparten suscripción), no del modelo; la tabla de
  modelos v6 es por carril con 4 posiciones, y taller la modela por CLI.
- La PC ya resolvió el control de procesos en Windows (Job objects, shims `.cmd`, guardián WSL)
  y la Mini el merge de cinco compuertas; ambos están atados a cadenas que se retiran.
- Hay tres ejecutores de planes a medias (taller, Raíces, la cadena de Hermes) y un contrato sin
  consumidor (SUA). Uno debe quedar; los otros, retirarse sin perder lo que aprendieron.

## Uso (vista de quien llama)

Un solo comando, `motor`, igual en las tres máquinas. Toda salida tiene `--json` para las IAs.

```sh
motor valida plans/ingesta-02.md                 # revisa el plan; no encola nada
motor entrega plans/ingesta-02.md                # lo encola EN ESTA máquina; devuelve recibo
motor entrega plans/ingesta-02.md --en pc        # lo mismo, pero en la PC (ssh); corre allá completo
motor estado [plan] [--todas] --json             # una línea por bloque; --todas pregunta a las 3 por ssh
motor hecho <plan> <bloque>                      # David terminó un paso humano
motor nota <plan> <bloque> "usa la función X"    # llega al siguiente encargo del bloque
motor recibo <cmd_id>                            # ¿el motor ya tomó esa orden?
motor doctor --json                              # versión, conf, agentes que corren sin ventana
motor instala --maquina mini                     # instalación idéntica (servicio, carpetas, conf)
```

Respuesta de `motor entrega --json` (la IA solo lee esto):

```json
{"cmd_id": "c-01JB…", "plan": "ingesta-02", "maquina": "pc", "estado": "tomado",
 "bloques": 5, "repos": ["gon0801/goncloud-Orbit"], "pasos_humanos": ["E: login Seller Central"]}
```

`estado` es `tomado` cuando el daemon ya escribió `PlanLoaded`, o `encolado` si el daemon está
caído (la orden no se pierde: queda en `inbox/` y el daemon la toma al volver).

**Las IAs (claw e Hermi) usan la misma skill `motor`** (un `SKILL.md` idéntico en los dos
repos de skills; ambos productos leen ese formato). La skill dice tres cosas: la regla de ruteo,
cómo escribir un plan (plantilla + `motor valida` hasta verde) y los cuatro comandos. Ninguna IA
mergea, despliega ni teclea en ventanas. El lead usa el mismo CLI desde Claude Code.

**Regla de ruteo (la misma en las dos marcas y en el lead):** si el resultado del pedido
necesita escribir *cualquier cosa* en un repo (código, config, docs del repo), desplegar o
escribir en producción, es plan → `motor entrega`. Si solo lee (investigar, contestar, revisar
un PR, consultar datos), la IA lo reparte a sus agentes como hoy. Ante la duda, plan.

### Escenario 1: un plan a Hermes durante un apagón

1. David le escribe a Hermi: "corre el plan de ingesta 02". Hermi ejecuta
   `motor entrega plans/ingesta-02.md --json` en la PC → `tomado`. David recibe por el bot del
   motor: "En la PC · Ingesta 02: empecé, son 5 partes."
2. Bloque B: Kimi implementa. `runwrap` toma una foto del worktree cada 5 min y la empuja a la
   rama `motor-wip/pc/ingesta-02/B` (no toca la rama del agente ni su índice).
3. 14:08 se va la luz. 15:00 vuelve. La BIOS prende la PC sola; la tarea programada "motor"
   (al arrancar, sin sesión, con contraseña guardada) levanta `motor servicio` → daemon.
4. `fold(log)`: intento B/implementar/4 sin final. `collect` ve que el `boot_id` guardado no es
   el actual → resultado `corte` (no cuenta como falla de Kimi). `decide` relanza Kimi, intento 5.
5. `Workspace.prepara` ve que la foto `motor-wip` es hija de la punta de la rama → restaura esos
   archivos sin commitear. El encargo dice: "tu intento anterior se cortó por un apagón; sigue".
6. Si el corte cayó durante el merge: al volver, `Merge` primero lee el PR; si ya está MERGED
   con ese SHA, solo anota `Merged`. Nada se duplica. David no recibe aviso del apagón: no tiene
   nada que hacer.

### Escenario 2: "investiga X" a OpenClaw que termina en plan

1. David a claw: "investiga por qué el harvest de ayer no subió negativos". Es lectura: claw
   manda a `scout` y a `verifier` (solo lectura) y contesta con lo encontrado.
2. La causa es un filtro mal escrito: arreglarlo cambia código → por la regla, claw escribe un
   plan de un bloque (fila con `Acepta`, `prueba-real` contra datos reales), corre
   `motor valida` hasta verde y `motor entrega` **en la Mini**. Le dice a David: "Encontré la
   causa; ya mandé el arreglo al motor, te avisa cuando esté probado."
3. El motor de la Mini corre todo: implementa (cadena `codigo`), revisa (Claude), PR draft →
   ready → checks requeridos → merge con el SHA exacto → despliegue del repo → prueba real →
   aviso "Listo, parte 1 de 1: el harvest ya sube negativos. Lo probé con datos reales."
4. Si David pregunta "¿cómo va?", claw responde con `motor estado ingesta-fix --json`. Claw no
   vigila nada entre medio: cero tokens.

### Escenario 3: se acaba la cuota a media revisión

1. En la Mac, bloque C implementado por Sonnet (`anthropic/claude-sonnet`). Revisa Claude Opus
   (rol `revisar`, primera posición; Opus no es autor del bloque: autores se cuentan por modelo).
2. A media revisión, Claude responde "usage limit … resets 18:00". `classify` → `cuota` con hora;
   el evento `Cooldown` se anota a la **cuenta** `anthropic` (Sonnet y Opus quedan fuera hasta las
   18:00 en esta máquina).
3. `revisores_para(C)` = cadena `revisar` (Opus→K3→GLM→Grok) − autores − cuentas en cuota → K3.
   La revisión empieza de cero sobre el mismo SHA (revisar es de solo lectura; repetirla es barato).
4. K3 da CAMBIOS con un bloqueante con `repro`. El arreglo va a la cadena `codigo` sin Sonnet
   (cuota) → K3 implementa; ahora K3 es autor → la siguiente revisión la hace GLM.
5. A las 18:00 el cooldown vence solo; el siguiente bloque ya lo revisa Claude. Sin aviso a David
   salvo que **todas** las cuentas de la cadena queden fuera más de 6 h.

## Forma

```
 lead (Mac) ─┐   claw (Mini) ─┐   Hermi (PC) ─┐      cada una llama SOLO al motor de su máquina
             ▼                ▼               ▼      (o --en <otra> por ssh)
        motor CLI ──escribe──► inbox/<cmd_id>.json         lee (solo lectura) ◄── estado/recibo
                                    │
          motor servicio (supervisor; launchd / Task Scheduler) ─► motor daemon (sin modelo)
               tick = conf.refresh → ingerir → fold(log) → decide (puro) → ejecutar efectos
                                    │
      log de eventos SQLite append-only (único escritor = daemon)  ← única verdad por máquina
                                    │
   ┌──────────────┬─────────────────┼──────────────────┬───────────────┬──────────────┐
 Workspace       Plataforma        GitHubPort          Deploy          Notifier
 (espejo por     posix | windows   gh real | fake      receta del      bot "Motor",
  repo, worktree, (+wsl): runwrap,  PR draft→ready→     repo o del      plantillas,
  fotos wip)      boot_id, árbol    checks→merge SHA    bloque          lint_plain
```

### Datos primero

- **Tres fuentes, una por tipo de verdad, sin copias:**
  1. `goncloud-motor-conf` (repo privado): `version` del motor, `roles.toml`, `modelos.toml`
     (cadenas por carril, transcritas de la v6), `rutas.toml` (modelo → CLI + cuenta + plantilla
     headless), `reglas/*.md` (preámbulo de todo encargo y reglas del revisor), `avisos.toml`
     (chat de David). Todos los daemons hacen `git fetch` cada 15 min. Cada `AttemptStarted`
     anota el SHA de conf con que se lanzó (auditoría, no copia).
  2. `.motor.toml` en cada repo, **leído de la rama base** al tomar el plan y fijado en
     `RepoPinned{repo, sha}`: un agente no puede cambiar las reglas de merge de su propia rama.
  3. `~/.motor/maquina.toml` + `secretos.env` (600 / ACL de dueño): solo hechos de la máquina
     (nombre, PATH extra, CA de Kaspersky, distro WSL) y tokens. Nada de roles ni modelos aquí.
- **`Route(modelo, cuenta, cli, plantilla)`** reemplaza a `Provider`. Cooldown por `AccountId`;
  autoría y exclusión del revisor por `ModelId`; `max_parallel` por cuenta y máquina.
- **Plan**: el formato de taller + `repo:` en el encabezado y opcional por bloque (un bloque =
  una rama y un PR en su repo; un plan puede tocar varios repos). Ramas
  `motor/<maquina>/<plan>/<bloque>`: dos máquinas con el mismo plan nunca chocan.
- **Eventos nuevos** (mismo log, mismo `ukey` idempotente): `RepoPinned`, `PrOpened{n}`,
  `MarkedReady{sha}`, `ChecksResult{sha, green, fallas}`, `DeployStarted{sha}`,
  `CommandReceived{cmd_id}`. `AttemptStarted` agrega `boot_id`, `ruta`, `cuenta`, `conf_sha`.
- **Estados nuevos en `estado()`**: `VERIFICAR → PR → REVISAR → LISTO_PR → CHECKS → MERGE`.
  `CHECKS` pendiente no produce acción durable: el efecto consulta como máximo cada 60 s y solo
  anota el resultado terminal (no se ensucia el log con sondeos).

### Decisiones que cargan peso

- **El núcleo puro no cambia de dueño** (`machine.fold/decide/estado/merge_gate`): solo crece en
  estados. Los efectos se separan por conocimiento, no por orden de ejecución
  (per *temporal decomposition*): `Workspace` sabe de git local, `GitHubPort` de PRs y checks,
  `Plataforma` de procesos y arranque, `Notifier` de Telegram.
- **`MergeTicket` sigue siendo el único camino al merge** y ahora exige además
  `ChecksResult.green` sobre el mismo SHA y `MarkedReady`. `GhGitHub.merge` hace
  `gh pr merge N --<método> --match-head-commit <sha>` y relee `MERGED` + `mergeCommit`.
- **Checks requeridos = protección de la rama base (reglas + protección clásica) ∪
  `.motor.toml [github].checks_extra` ∪ bot opcional** (autor + marcador por SHA, la compuerta
  pura portada de `merge.py`). Nada de Orbit en el motor: `rapido/completa/gate` y el bot
  ai-review de Orbit viven en el `.motor.toml` de Orbit.
- **Fotos wip sin tocar el índice del agente**: `GIT_INDEX_FILE` temporal → `add -A` →
  `write-tree` → `commit-tree -p HEAD` → push forzado a `motor-wip/…`. Antes de empujar, el
  diff pasa por el filtro de secretos portado de `redaction.py`; si encuentra algo, no se empuja
  esa foto (la siguiente lo vuelve a intentar).
- **El agente puede morir con el daemon y no pasa nada grave.** En POSIX `runwrap` sobrevive
  (sesión propia). En Windows se lanza con `CREATE_BREAKAWAY_FROM_JOB` y su propio Job
  kill-on-close (lo que contiene es al agente, no al daemon); si la tarea programada no permite
  breakaway, el agente muere con el daemon y se relanza desde la última foto. El diseño **no
  depende** de que el agente sobreviva: el apagón lo mata igual.
- **Vivo o muerto por `ProcHandle(pid, boot_id, inicio)`**, no solo por pid: tras reiniciar,
  los pids se reciclan y `kill(pid,0)` mentiría.
- **Un daemon por máquina, dueño solo de sus planes.** No hay estado compartido entre máquinas
  salvo GitHub (ramas con nombre de máquina; el merge serializa con `--match-head-commit`) y el
  repo de conf (se escribe solo por plan con PR; todos lo leen). Cada máquina aprende la cuota
  por su cuenta (per *separate-before-serializing-shared-state*).
- **Cliente por archivos, no por socket.** `motor entrega` escribe `inbox/<cmd_id>.json` con
  rename atómico y espera hasta 10 s a ver `CommandReceived{cmd_id}` leyendo SQLite en modo solo
  lectura (WAL). Funciona con el daemon caído, sin puertos, igual en Windows (patrón
  operationId/recibo de SUA, sin su esquema).
- **Avisos: un bot propio "Motor", solo envío.** Las tres máquinas usan el mismo token para
  `sendMessage` (Telegram permite varios emisores; solo `getUpdates` es exclusivo, y no se usa).
  Cada aviso empieza "En la Mini · <plan>:". Nadie más avisa de planes: las IAs contestan si
  David pregunta. El texto pide "dile «ya quedó» a <quien entregó>"; la sonda del plan, si
  existe, lo detecta sola.
- **Revisor**: rol `revisar` → cadena `planear` (Claude Opus primero, con `reglas/revisor.md`
  que destila las reglas y trampas del lead). Relevo: `revisores_para` = cadena revisar ∪ resto
  de modelos conocidos − autores del bloque (por modelo) − cuentas en cuota.

### Profundidad de la interfaz

La superficie pública son 7 verbos del CLI y el formato del plan. Detrás quedan: espejos por
repo, worktrees, fotos wip, relevo por cuenta, protección de ramas, `ready`, sondeo de checks,
merge por SHA, despliegue con sonda de "ya desplegado", reinicios y avisos. Las IAs no conocen
ramas, SHAs, PRs ni CLIs de agentes. `GitHubPort` tiene 6 métodos y ninguno expone JSON de
GitHub: devuelve `PrView`/`ChecksVerdict` ya parseados (per *boundary-discipline*).

### Instalación y actualización idénticas

- Paquete Python ≥3.11 solo stdlib en `gon0801/goncloud-motor` (el repo de taller renombrado).
  `uv tool install git+ssh://…/goncloud-motor@vX.Y.Z` en las tres (uv ya está en las tres o se
  instala igual; trae su propio Python, así la Mini con 3.9 y la PC con 3.14 corren lo mismo).
- `motor instala --maquina <mac|mini|pc>` crea `~/.motor/`, clona la conf, revisa permisos de
  `secretos.env`, registra el servicio y corre `motor doctor`. Mismo comando, mismo resultado.
- **Actualizar = cambiar `version` en la conf** (por plan, con PR). Cada daemon lo ve en ≤15
  min, corre `uv tool install --force …@<version>`, sale con código 75 y `motor servicio` lo
  relanza. Si la instalación falla, sigue con la versión anterior y avisa una vez en llano.
  Volver atrás = poner la versión anterior. `motor doctor --json` publica
  `{version, conf_sha, rutas_ok}`: "idéntico" se prueba comparando esas huellas.

### Servicio y credenciales por plataforma

| | MacBook (FileVault) | Mini (auto-login) | PC (Windows) |
|---|---|---|---|
| Servicio | LaunchDaemon `UserName=dn`, `RunAtLoad`, `KeepAlive` | igual, `UserName=gon` + `pmset autorestart 1` | Tarea "al arrancar", "aunque nadie inicie sesión", contraseña de ehven guardada, sin límite de tiempo, reiniciar si falla |
| Proceso | `motor servicio` (supervisor de ~40 líneas que relanza el daemon) | igual | igual |
| Credenciales | `secretos.env`: `CLAUDE_CODE_OAUTH_TOKEN`, `CURSOR_API_KEY`, `META_API_KEY`, `GH_TOKEN`; el resto ya son archivos | igual (quita la dependencia del llavero bloqueado) | igual; además CA de Kaspersky en `maquina.toml`; Muse por `wsl.exe` con `WSLENV=META_API_KEY/u` |
| Tras apagón | Nada arranca hasta que David escribe la contraseña de FileVault (eso ya inicia sesión); en cuanto lo hace, el plan sigue solo. La tapa cerrada duerme la Mac: el plan espera. | arranca solo | arranca solo (BIOS: "encender al volver la luz") |

La MacBook no puede ser un host sin sesión. Cómo se cumple el requisito 4 ahí: (a) no hace
falta ninguna acción más allá de la contraseña que David ya escribe para usar su Mac, y (b) el
lead puede mandar un plan largo a una máquina siempre prendida con `--en mini|pc`: el plan
corre completo allá (requisito 2 intacto: corre donde se entrega).

### Despliegue genérico

`.motor.toml [deploy]`: `desplegar`, `checklist`, `rollback`, `ya_desplegado` (opcional) y
`requiere` (p. ej. `["ssh:goncloud"]`). Se corre en una copia limpia del commit de merge, en esta
máquina. `DeployStarted` se anota antes; si el daemon cae a la mitad, al volver corre primero
`checklist`: si pasa, anota `Deployed` sin redesplegar. `motor valida` rechaza un plan con
despliegue en una máquina que no cumple `requiere` (se entera la IA en el momento, no a las 3 h).

## Requisitos de David (1-10)

| # | Requisito | Cómo se cumple |
|---|---|---|
| 1 | Motor único igual en 3 máquinas | Un paquete, un repo de conf, mismo `motor instala`; `doctor` publica la huella |
| 2 | El plan corre completo donde se entrega | Un daemon por máquina, dueño solo de sus planes; `--en` solo cambia DÓNDE se entrega |
| 3 | Instalación idéntica | Versión fijada en la conf; roles, modelos, reglas solo en la conf; máquina = hechos + tokens |
| 4 | Retoma tras apagón, sin sesión, wip cada pocos min | LaunchDaemon / tarea al arrancar; log + `boot_id`; fotos wip cada 5 min; MacBook: ver tabla |
| 5 | Solo hasta terminar y probar | `HECHO` exige `Proved` tras merge (+deploy); sin estados FALLADO/PAUSADO; pasos humanos solo login/2fa/captcha/pago |
| 6 | Multiproyecto | `repo:` por plan/bloque; espejo por repo; todo lo específico en `.motor.toml` y en el plan |
| 7 | Dos marcas, mismas apps; cadena de Hermes fuera | Misma skill `motor` en claw e Hermi; regla de ruteo; `local_orchestrator` y Raíces retirados |
| 8 | Revisor Claude; si no, quien no trabajó | `revisores_para` por modelo, cuota por cuenta; prueba de unidad ya existe en taller |
| 9 | Un canal de avisos, llano | Bot "Motor" solo-envío desde las tres; plantillas + `lint_plain` |
| 10 | Plan = autorización; nunca esperar; sin vigía; no `--no-verify`/push a principal; sin secretos; datos reales | Sin aprobaciones en el código; checks pendientes no bloquean otros bloques; tick sin modelo; `reglas/` en cada encargo + protección de rama; filtro de secretos en wip; `prueba-real` obligatoria en bloques de código |

## Las 10 familias de fallas

| # | Familia | Cómo queda |
|---|---|---|
| 1 | Prompts de permiso | Sin TTY, stdin nulo (`DEVNULL`/`NUL`), `CREATE_NO_WINDOW` en Windows, banderas headless en `rutas.toml`, tope y detector de avance; `doctor` corre cada ruta con un encargo de prueba |
| 2 | Entregas no recibidas | No hay tecleo. La orden es un archivo con `cmd_id` y recibo en el log (`motor recibo`) |
| 3 | LISTO falso | SHA contra origin, `Acepta` en copia limpia, `MERGED` releído en GitHub, `Proved` con datos reales |
| 4 | Coordinador sin herramientas → merge sin revisión | Coordinador = código; las IAs no tienen verbo de merge; `MergeTicket` sobre el mismo SHA |
| 5 | Vigilancia cara | Tick sin modelo; las IAs no sondean; checks consultados como máximo cada 60 s por API |
| 6 | Cuota y pausas largas | Cooldown por cuenta con hora; relevo por la cadena del carril; rate limit = 2 min, no 5 h (de `router.py`) |
| 7 | Reinicios y estado perdido | Servicio al arrancar; log; `boot_id`; fotos wip; efectos idempotentes (PR por rama, merge releído, deploy con checklist primero) |
| 8 | Compuertas humanas de horas | Solo `humano:` del plan; sonda; el resto del plan sigue; un aviso por paso |
| 9 | Merge adelantado | Draft hasta APROBADO; `ready`; checks requeridos de la protección ∪ repo; `--match-head-commit` |
| 10 | Avisos técnicos | Un bot, plantillas cerradas, `lint_plain` con prueba sobre todas |

## Piezas existentes: reusar, portar o retirar

| Pieza | Destino | Por qué |
|---|---|---|
| taller `store`, `machine`, `plan`, `notify`, `classify` | **Reusar** como núcleo | Ya prueban las reglas difíciles con 19 pruebas; solo crecen estados y `Route` |
| taller `runwrap` | **Portar** a `plataforma/` + fotos wip periódicas | Hoy solo POSIX y empuja al final |
| taller `effects` | **Partir** en `Workspace`, `GitHubPort`, `Deploy`, `Notifier` | Hoy mezcla cuatro conocimientos y supone un solo repo |
| Hermes `subscriptions/process.py` | **Portar** Job objects, `_resolve_windows_shim`, guardián WSL, terminación de árbol, env de arranque | Es el único código probado en la PC para procesos de CLIs |
| Hermes `router.py` + `adapters/*` | **Portar el conocimiento** (tipos de falla, rate limit corto, parseo de salida de cada CLI, permisos de Claude por rol); **retirar** arriendos/cuarentena | Con un solo escritor por máquina no hacen falta arriendos |
| `model-routing.source.json` v6 | **Mover** a `goncloud-motor-conf/modelos.toml` como fuente única; Hermes y OpenClaw reciben copias generadas | Una tabla, no tres |
| Hermes `local_orchestrator.py` (cadena de 7) | **Retirar** | Requisito 7 |
| Hermes `entrega_pr.py` | **Retirar**; sus reglas (PR idempotente por rama, cuerpo sin instrucciones en repos públicos, token nunca a disco) pasan a `GhGitHub` | El motor publica fuera de cualquier sandbox |
| Hermes `avisos_sesion.py` | **Retirar** para planes | Un solo canal: el bot del motor |
| Raíces (SUA-07) y PR #37 | **Retirar** (cerrar #37 sin mergear) | Segundo ejecutor de planes; atado a kanban y cron de Hermes |
| Contrato SUA | **Retirar** como contrato; tomar `cmd_id` + recibo durable | Sin consumidores; el log del motor es el contrato |
| OpenClaw `merge.py` | **Portar** las compuertas puras (deduplicar check-runs, marca de bot con autor, SHA exacto, releer MERGED); la receta de Orbit va a su `.motor.toml` | Lo genérico sirve a todo repo; lo de Orbit no va en el motor |
| OpenClaw agentes | **Reusar** main/scout/verifier/reviewer/adversary/operaciones/negocio para lectura; `implementer`/`ingenieria` dejan de escribir código | Lo que cambia código va al motor |
| Loops de claw (PROMPT.md, tmux, ACK/LISTO, `nohup` wakeups), skill `prompt-claw` | **Retirar** | Son las familias 2, 5 y 7 |

## Tradeoffs aceptados

- Aceptamos hasta un tick (~5 s) de demora para el recibo a cambio de que una orden nunca se
  pierda con el daemon caído y de no abrir puertos.
- Aceptamos que en Windows un agente pueda morir si se reinicia el daemon a cambio de no
  depender de un detalle no verificado del Task Scheduler; se pierden ≤5 min de trabajo.
- Aceptamos que cada máquina descubra la cuota por su cuenta (un intento fallido por máquina)
  a cambio de cero estado compartido entre máquinas.
- Aceptamos re-revisar cuando la rama se actualiza con la base (SHA nuevo) a cambio de no
  romper la regla de "todo sobre el mismo SHA"; el revisor recibe solo el delta.
- Aceptamos que la regla de ruteo de las IAs sea instrucción (skill) y no barrera dura: claw e
  Hermi tienen `gh` con la misma cuenta. La barrera dura sigue siendo la protección de rama.
- Aceptamos que un mal commit de conf llegue a las tres máquinas en 15 min a cambio de una sola
  fuente; por eso la conf solo cambia por plan con `motor conf valida` como `Acepta` y CI.
- Aceptamos `gh` como dependencia (está en las tres) en vez de hablar HTTP a mano.

## Alternativas consideradas

- **API local por socket/HTTP en el daemon.** Expone menos al principio pero obliga a que el
  daemon esté vivo para aceptar, a autenticar en localhost y a lidiar con el firewall de
  Windows. Archivos + SQLite de solo lectura dan lo mismo con menos piezas.
- **Motor dentro de los gateways (plugin de OpenClaw/Hermes).** Un reinicio del gateway corta lo
  que corre (ya pasó) y cada marca tendría su propia versión: rompe "idéntico".
- **Raíces como motor.** 6k líneas atadas al kanban y al cron de Hermes, sin camino a macOS.
- **Coordinador central (goncloud) que reparte a las máquinas.** Viola el requisito 2.
- **Daemon dentro de WSL en la PC.** Los CLIs y sus credenciales viven en Windows; WSL en
  sesión 0 no está verificado. Solo Muse cruza a WSL, por su ruta.

## Preguntas para David

1. ¿Los avisos de los planes los manda un bot nuevo llamado "Motor" (neutral entre claw y
   Hermi) o prefieres que salgan por el bot de claw?
2. En la MacBook, ¿quieres que no se duerma con la tapa cerrada mientras corre un plan? Si no,
   el plan espera hasta que la abras (o el lead lo manda a la Mini o la PC).
3. Un plan que nace de un "investiga" (claw o Hermi lo escriben) ¿arranca solo, como cualquier
   plan, o quieres un "sí" tuyo solo en ese caso?
4. Para que la PC arranque el motor sin que nadie inicie sesión, la tarea guarda la contraseña de
   ehven en Windows. ¿Está bien?
5. ¿Activamos "encender al volver la luz" en la BIOS de la PC y en la Mini?
6. ¿Usamos tokens de larga duración guardados en un archivo protegido en cada máquina (Claude,
   Cursor, Muse, GitHub) en vez del llavero?
7. ¿claw e Hermi también toman sus modelos de la misma tabla que el motor?
8. En repos públicos, el trabajo a medias se sube cada 5 min a una rama visible (con filtro de
   secretos). ¿Lo dejamos así o en repos públicos solo se sube al terminar cada intento?

## Alcance del prototipo (sobre ~/dev/goncloud-taller, en esta Mac, pocas horas)

Todo con repos desechables bajo `$TMPDIR`; sin red salvo una prueba opcional. Nada toca Orbit,
OpenClaw, Hermes ni producción.

1. **Conf y rutas**: un repo git local de conf con `modelos.toml` de dos cadenas y `rutas.toml`
   apuntando a `proto/fake_agent.py` con distintas "cuentas". Prueba: cuota de la cuenta
   `anthropic` a media revisión → revisa K3 falso, que no fue autor; a la hora, vuelve Claude.
2. **`GitHubPort` + `FakeGitHub`**: bare repo + `prs.json` (draft, ready, head, merged, checks por
   SHA) + CI falso que se dispara en push y en ready; protección con checks requeridos;
   `merge(match_head)` rechaza si la cabeza se movió. **Suite de contrato** que corre contra el
   falso siempre y contra `gh` real solo con `MOTOR_GH_SANDBOX=<owner/repo>` (repo desechable que
   crea David si quiere).
3. **Multirepo y `.motor.toml`**: plan de dos bloques en dos repos; `.motor.toml` leído de la
   base; una rama del agente que modifica `.motor.toml` no cambia nada.
4. **Plataforma POSIX + `boot_id` + fotos wip**: `runwrap` empuja fotos a `motor-wip/…` cada 2 s
   (en prueba). Prueba de "apagón": `kill -9` al daemon **y** al runwrap, cambiar el `boot_id`
   falso, relanzar → `corte` (no falla), el siguiente intento ve los archivos de la foto, el plan
   termina, un solo PR y un solo merge.
5. **Windows sin Windows**: `plataforma/windows.py` portado de `process.py`; en la Mac solo se
   prueban las partes puras (`resolve_shim` con un PATH falso, armado de `creationflags`, XML de
   la tarea). Se marca "sin probar en la PC".
6. **Cliente**: `motor entrega/estado/hecho/nota/recibo --json`; prueba con un "IA falsa" (script)
   que entrega con el daemon apagado (`encolado`), lo prende y ve `tomado`.
7. **Avisos**: `Notifier` con transporte inyectable; servidor HTTP local que imita
   `sendMessage`; se verifica prefijo de máquina, ≤8 líneas, un aviso por clave aunque el daemon
   muera tras enviar y antes de anotar (al-menos-una-vez, documentado).
8. **Servicio**: `motor instala --imprime` genera el plist y el XML; prueba manual opcional con
   un LaunchAgent de etiqueta `com.goncloud.motor.prueba` y home en `$TMPDIR`, que se descarga al
   final.

Fuera del prototipo: correr en la PC/WSL, Telegram real, despliegue real, reinicio real.

## Despliegue a las tres máquinas: un solo plan ("MOTOR 01")

Lo corre el prototipo en la Mac (es lo único que existe antes); sus bloques tocan varios repos.

- **A** `goncloud-motor-conf`: tabla v6, rutas, reglas, `motor conf valida`, CI.
- **B** motor: `GhGitHub` real, estados PR/ready/checks, `Deploy`, `Notifier` real, versión 1.0.
- **C** motor: `plataforma/windows.py` + WSL; prueba real = `doctor` por `ssh gwpc` (lectura).
- **D** `.motor.toml` en Orbit (checks `rapido/completa/gate`, bot ai-review, deploy goncloud).
- **E** humano `login`: David crea el bot "Motor", corre `claude setup-token` y llena
  `secretos.env` en las tres (sonda: `motor doctor` remoto ve rutas ok).
- **F** instalar en Mac, Mini y PC (`desplegar` = `motor instala` por ssh en cada una;
  `checklist` = huellas iguales; `rollback` = descargar el servicio). Incluye `pmset
  autorestart 1` en la Mini.
- **G** `goncloud-hermes`: skill `motor`, tabla generada, apagar `local_orchestrator`, Raíces,
  `avisos_sesion` y el cron `despertar-raices`; cerrar PR #37.
- **H** `goncloud-openclaw`: skill `motor`, tabla generada, `implementer` sin escritura, retirar
  `merge.py` y los loops; skill `prompt-claw` fuera.
- **I** prueba real: un plan canario sobre un repo desechable entregado por claw en la Mini y
  por Hermi en la PC; a media corrida se reinicia cada máquina (`shutdown -r`); los dos
  terminan, hay un PR y un merge por bloque, llegan los avisos del bot. Hasta que I pase, nadie
  usa el motor nuevo para trabajo real; al pasar, lo viejo ya está apagado por G y H.

## Synthesis decision

## Siguiente paso de implementación

Escribir `GitHubPort` y `FakeGitHub` con su suite de contrato, y mover `effects.run_ci`/`merge`
a ese puerto, porque es la pieza que más reglas de seguridad carga y la que destraba el resto.
