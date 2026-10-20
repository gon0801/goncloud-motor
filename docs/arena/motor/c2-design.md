# motor — candidato C2: extraer de Hermes la capa probada, injertar el núcleo de taller

Fecha 2026-10-20. Hechos leídos en `origin/feat/hermes-team` (no en el checkout viejo), en `goncloud-taller` y en `goncloud-openclaw`.

## Problem

David quiere un solo programa, idéntico en tres máquinas, que tome un plan y lo lleve solo hasta "probado con datos reales", sin ventanas, sin vigía, sobreviviendo apagones, y que OpenClaw y Hermes lo usen igual. Hoy hay tres cosas a medias: **taller** (1,650 líneas, 19 pruebas; el único con la forma correcta de la máquina de estados —sin PAUSADO, MergeTicket, SHA verificado— pero POSIX-only, sin adaptadores por proveedor, sin redacción de secretos, sin GitHub real); **Hermes** (su `subscriptions/` tiene ~4.5k líneas y ~300 pruebas de relevo entre 4 posiciones, salud por cuenta, cuarentena con horizonte, continuación tras mutación, control de procesos en Windows, prompt en archivo privado y redacción; más `entrega_pr.py` idempotente); y dos ejecutores de planes en Hermes que **no** sirven: la cadena fija (`local_orchestrator.py`, 2,250 líneas, se retira por orden) y Raíces (~6k líneas, atada al kanban de Hermes y al contrato SUA cuyo host R ya no existe; su ejecutor #37 sin mergear). La restricción que define la forma: el router de Hermes es **sincrónico** (`router.run` bloquea mientras corre el proveedor y mata al proveedor si el router muere), mientras que el requisito 4 exige que un reinicio del programa —incluida su propia actualización— no pierda el trabajo del agente. Esa diferencia es el costo real de la extracción.

## Usage (caller's view)

Instalación idéntica (las tres máquinas, un solo comando; `uv` ya está en Mac y Mini, en la PC lo instala el mismo script):

```sh
uv tool install git+https://github.com/gon0801/goncloud-motor@estable
motor instala            # crea ~/.motor (host.toml, secretos.env 600, inbox/, wt/), pide lo que falte
motor doctor             # qué CLI corre sin ventana aquí, qué credencial falta, qué servicio hay
motor servicio instala   # launchd (Mac/Mini) o tarea programada al arranque (PC); KeepAlive
```

Un repo que quiera planes trae `.motor.toml` en su raíz:

```toml
[repo]        principal = "master"
[github]      checks = ["rapido", "completa", "gate"]   merge = "squash"   draft_primero = true
              bots_del_sha = ["github-actions"]          # comentario del bot ai-review sobre el SHA final
[despliegue]  desplegar = "ops/desplegar.sh"  checklist = "ops/checklist.sh"  rollback = "ops/rollback.sh"
[pruebas]     entorno = { ORBIT_PG_HOST = "..." }        # env para prueba-real; sin secretos (van en secretos.env)
```

El plan es el de taller (bloques con `Acepta` por fila y `prueba-real`), más una línea `repo:` con el remoto. Lo que hace cada actor:

```sh
# David: nada. Recibe avisos en Telegram. Si un bloque dice humano: login, hace eso y le dice a claw/Hermi "ya quedó".

# Lead (Mac):
motor valida plans/ingesta-01.md && motor entrega plans/ingesta-01.md     # corre en la Mac
motor estado --json | jq                                                  # sin modelo, cero tokens

# claw (Mini) y Hermi (PC), desde su skill `motor` (idéntica en los dos):
#   pedido de solo lectura  -> sus propios agentes (scout/reviewer/verifier), herramientas sin escritura
#   pedido que cambia código -> escribe plan.md con la plantilla, `motor valida`, `motor entrega`, contesta el id
motor plantilla > /tmp/plan.md ; motor valida /tmp/plan.md ; motor entrega /tmp/plan.md --json
motor hecho ingesta-01 E          # David dijo "ya quedó" en la conversación
motor nota ingesta-01 B "usa app.db.connect"
```

Escenario 1 — plan a Hermes durante un apagón. David por Telegram: "Hermi, en goncloud-Orbit agrega el reporte X". Hermi lanza `scout` (lectura) para ubicar archivos, escribe el plan (3 bloques con Acepta y prueba-real), `motor entrega` → id `orbit-reporte-01`. El motor en la PC abre worktree `motor/orbit-reporte-01/A`, elige carril `codigo` posición 0 (Sonnet por claude), lanza `runwrap` separado. A los 40 min se va la luz. La PC vuelve sola (AutoAdminLogon ya está; la tarea del motor es "al arrancar, aunque nadie inicie sesión"). El daemon pliega el log: intento A-implementar-1 "corriendo", pid muerto, sin `outcome.json` → `perdido`. El `wip-push` periódico empujó la rama hace ≤5 min. Siguiente tick: intento 2, mismo carril, `reset --hard origin/<rama>`, brief: "el intento anterior terminó por: perdido; sigue desde los commits de la rama". Nadie avisa a David; no hace falta.

Escenario 2 — "investiga X" a OpenClaw que termina en plan. claw lanza `scout` con `openclaw agent exec` (sin herramientas de escritura). El hallazgo dice "el bug está en `jev/bloqueo.py`; el arreglo son 12 líneas". claw NO lo arregla: la regla de la skill es *cambia código → plan*. Rellena la plantilla; `motor valida` rechaza: "bloque A: falta prueba-real". claw agrega `prueba-real: ssh goncloud ...consulta solo lectura...`, `motor entrega --json` → id. Contesta a David en Telegram: "Ya lo investigué: ... Lo convertí en plan y está corriendo; te aviso cuando esté probado." El resto son avisos del motor.

Escenario 3 — cuota a media revisión. Bloque B entregado y verificado (SHA en origin, `Acepta` en verde en copia limpia). Revisor por omisión: carril `revisar` posición 0 = Claude opus (lead sin ventana, solo lectura). A los 20 min el CLI sale con "usage limit… resets at 3pm". `clasificar` → `cuota` con hora → `Cooldown(claude, 15:00)`. `revisores_para(B)` = posiciones del carril menos los adaptadores que implementaron B ({muse}) → siguiente: K3 por kimi. Revisión nueva sobre la misma copia detached del SHA (una revisión no tiene estado parcial que perder). Veredicto APROBADO → `gh pr ready` → espera checks del SHA → MergeTicket → merge. A las 15:00 el reloj devuelve a Claude; ningún aviso porque nadie esperó más de 6 h.

## Shape

```
 lead / claw / Hermi ── motor CLI ──► inbox/ ──► daemon (servicio del SO, 1 instancia, sin modelo)
                                                   tick = ingerir → fold(log) → decide → ejecutar
                                                   log append-only SQLite (único escritor)      ← taller
   ┌───────────────┬────────────────┬───────────────┬───────────────┬──────────────┐
 relevo           proceso          github          despliegue      avisos
 (tabla v6 4 pos, (runwrap: POSIX   (push+PR idem-  (receta de       (plantillas +
  salud, cooldown  killpg / Win Job  potente, draft→  .motor.toml,     lint_plain,
  por clase,       object / WSL      ready, checks    checklist,       Telegram Bot API
  cuarentena,      guardián, wip     del SHA exacto,  rollback,        directo, buzón
  continuación     cada 5 min,       compuertas →     prueba-real)     reintentable,
  tras mutación)   prompt privado)   MergeTicket)                       espejo opcional)
   ← Hermes        ← Hermes+taller   ← Hermes entrega_pr + openclaw merge.py   ← taller + Hermes
```

Datos primero. Tres tipos cargan todo:

- `Plan` (taller, + `repo`): bloques → filas con `Acepta`; `prueba_real`; `release`; `human`. Validado entero en la frontera (`plan.parse`); después nadie lo revalida.
- `Event` (taller): `(ts, plan, bloque, kind, data, ukey)`. El estado del plan es `fold(eventos)`; no hay tabla de estado. `ukey` hace idempotente cada hecho: ejecutar dos veces un efecto deja un evento. Aquí entran también los hechos del relevo que en Hermes vivían en `subscription_attempts`/`subscription_health`: `AttemptStarted/Ended`, `Cooldown(adaptador, hasta, clase)`, `Salud(adaptador, listo, cuarentena_desde)`. Una sola base, un solo escritor; las tres tablas de leases de Hermes desaparecen porque el servicio del SO garantiza una instancia (per separate-before-serializing-shared-state: antes había N hilos de gateway escribiendo; ahora uno).
- `Ruta` (Hermes `RouteSpec`): `(posicion, etiqueta, adaptador, modelo, esfuerzo)`, compilada de `config/modelos.json` por carril. `Papel → carril` vive en `config/papeles.toml`.

Flujo por las firmas: `decide(snapshot, politica, ahora)` (puro, taller) produce acciones; para `StartAttempt` pide a `relevo.siguiente(carril_filtrado, snapshot, ahora)` (puro, extraído del `while pending` de `router._run_owned`: cooldown por clase, cuarentena con horizonte, `_ADVANCING_FAILURES`, `_BLOCK_AFTER_MUTATION`, mutación medida con `tree_fingerprint`). El shell (`efectos`) pide al adaptador `comando(encargo, ruta) -> Lanzamiento(argv, env, prompt)` y lanza `runwrap` **separado del daemon** (taller). Al terminar, `adaptador.leer(evidencia) -> Resultado` clasifica (Hermes: parsers por proveedor, `retry_after`, ventana gastada vs throttle). Nada bloquea el tick.

Decisiones que cargan peso:

1. **Extraer de Hermes la capa de proveedores, procesos, redacción y PR; no sus ejecutores.** `subscriptions/` tiene una sola referencia a Hermes (la ruta de la DB bajo `LOCALAPPDATA/hermes`) y `catalog.load_routing_source` busca el JSON junto al paquete: se mueve sin tocar lógica. `entrega_pr.py` ya es genérico (owner/repo desde la URL del remoto, token por `git credential fill`, PR idempotente). Raíces y `local_orchestrator` dependen de `hermes_cli`/kanban en 8 módulos y tienen la forma equivocada para el requisito 7.
2. **Partir el router en política pura + proceso separado.** `router.run` → `relevo.siguiente` (puro) + `proceso.runwrap` (detached). Se pierde el latido de lease (innecesario con un escritor); se gana que `motor actualiza` reinicie el daemon sin matar agentes. Los adaptadores pasan de `invoke()` a `comando()` + `leer()`; `probe()` se queda igual y lo usa `doctor`.
3. **MergeTicket como único camino al merge** (taller), construido por `github.compuertas(vista_pr)` que porta las cinco compuertas de `merge.py` de openclaw (checks del SHA exacto, hilos resueltos, hallazgo bloqueante del bot sin adjudicar, CodeRabbit "rate limited" pasa con aviso) y mergea con `gh pr merge --squash --match-head-commit`. Sin ticket el tipo no existe (per encode-lessons-in-structure).
4. **Una fuente de verdad versionada**: `config/` dentro del paquete (`modelos.json` v6, `papeles.toml`, `reglas.md`, `skills/motor/SKILL.md`). Cambiar la tabla = PR al repo del motor = mover la etiqueta `estable`; las tres máquinas convergen solas (abajo). Lo de cada repo en `.motor.toml`; lo de cada máquina en `~/.motor/host.toml` (nombre, rutas de CLIs, env como las CA de Kaspersky, `wsl = ["muse"]`) y `secretos.env` (600).
5. **Credenciales por archivo, no por llavero**: `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `META_API_KEY` en `secretos.env`; codex/grok/opencode/kimi ya usan archivos. `cursor-agent` queda solo donde haya sesión gráfica (Mini con auto-login, PC) y `doctor` lo marca; el relevo lo salta por salud, no por configuración a mano.
6. **Servicio por plataforma**: Mini = LaunchAgent `gui/<uid>` con KeepAlive (auto-login ya existe, el llavero está abierto) + `pmset autorestart 1`. PC = tarea programada `ONSTART`, "ejecutar aunque el usuario no haya iniciado sesión", contraseña guardada (sesión 0; todos los CLIs de Windows guardan en archivos). Mac = LaunchAgent: con FileVault la máquina **no arranca sin la contraseña**, así que el requisito 4 se cumple en su forma aceptada ("si la máquina queda apagada, su plan espera"): nada se pierde (log + wip-push), retoma solo cuando David entra; además `motor servicio instala` deja `caffeinate -s` mientras haya intentos vivos y pide una vez un `sudoers` para `pmset disablesleep` con tapa cerrada. No se pide apagar FileVault.
7. **Avisos**: un bot, un chat, Telegram Bot API directo desde las tres máquinas (sin pasar por los gateways, que se reinician). Encabezado = marca ("Hermes:", "OpenClaw:", "Mac:") para que David compare; cuerpo por plantilla y `lint_plain` (≤8 líneas, sin SHA/rutas/jerga). Buzón `avisos/pendientes` con reintento. `espejo` opcional en `host.toml`: un comando que además despierta a la sesión de la marca (`openclaw system event …` / cola del gateway de Hermes) para que claw/Hermi puedan contestar en la conversación; si falla, el aviso ya salió.
8. **wip-push periódico**: `runwrap --wip-cada 300` commitea `wip:` y empuja a la rama del bloque (nunca a la principal) cada 5 min y al salir por cualquier razón.
9. **Regla pequeño vs plan, mecánica y textual**: textual en la skill ("¿el pedido termina con un cambio en un repo, config o despliegue? → plan; si no → agente de lectura"); mecánica porque los agentes de reparto corren con herramientas de solo lectura (`allowed_tools_for` de Hermes, agentes sin escritura en OpenClaw) y sin credencial de push: aunque quieran, no pueden cambiar código fuera del motor.
10. **Revisor**: `revisores_para(bloque)` = carril `revisar` (Claude opus primero) menos los adaptadores que aparecen en `autores(bloque)`; vacío → aviso "atorado" único, el resto del plan sigue.

Profundidad de la interfaz: la superficie pública son siete subcomandos y un formato de plan; detrás quedan relevo entre 4 posiciones, salud/cuarentena, procesos en 3 SO, verificación por SHA, compuertas de GitHub, despliegue, Telegram. No se exponen `RouteSpec`, `InvocationResult` ni el esquema de eventos (per boundary-discipline: parse en `plan.parse`, `.motor.toml` y `host.toml`; dentro, tipos). Lo que el motor deliberadamente no hace: no escribe planes (eso es de la IA que reparte o del lead), no reparte entre máquinas, no replanifica, no amplía lo autorizado.

## Requisitos 1-10

| # | Requisito | Cómo queda |
|---|---|---|
| 1 | Motor único en 3 máquinas | Un paquete `goncloud-motor` (stdlib, 3.11+) por `uv tool install @estable`; `host.toml` solo nombra rutas y env locales |
| 2 | El plan corre donde se entrega | `motor entrega` escribe en el inbox local; el log es local; no hay buzón entre máquinas |
| 3 | Instalación idéntica | `config/` dentro del paquete (modelos, papeles, reglas, skill); el daemon vigila la etiqueta `estable` cada 30 min, `uv tool upgrade` y sale 0 → KeepAlive lo relanza; los agentes en vuelo (detached) no se enteran |
| 4 | Retoma solo tras apagón; arranca sin sesión; push cada pocos minutos | Log append-only + efectos idempotentes (`ukey`) + agentes fuera del daemon; servicio por SO (6); `wip-push` cada 5 min (8). Mac: forma aceptada "espera" por FileVault |
| 5 | Corre solo hasta probado | `decide` nunca espera salvo `humano:`; `prueba-real` obligatoria en bloques de código; HECHO exige merge + prueba verde |
| 6 | Multiproyecto | Nada de Orbit en el código: `.motor.toml` por repo + `repo:` en el plan; `github.compuertas` lee los checks del toml |
| 7 | Dos marcas, mismas apps; cadena de 7 pasos se retira | Misma `skills/motor/SKILL.md` en ambas; agentes de reparto solo lectura; Hermes pierde `goncloud_local_*` y gana `motor_entrega`/`motor_estado` (shell al CLI) |
| 8 | Revisor Claude; relevo a quien no trabajó | `revisores_para` + carril `revisar`; cuota de Claude → `Cooldown` con hora, siguiente posición no autora |
| 9 | Un canal, español llano | Telegram directo, plantillas, `lint_plain`, prueba sobre todas las plantillas; marca como encabezado |
| 10 | Plan = autorización; nunca esperar; sin vigía; nunca `--no-verify`/push a principal; sin secretos; datos reales | Sin estado PAUSADO; tick sin modelo; brief con `reglas.md`; shims de `git push` a principal bloqueados en PATH del agente + protección de rama; redacción de Hermes en toda evidencia; `Proved` antes de HECHO |

## Diez familias de fallas

| # | Familia | Mecanismo | Prueba que lo atrapa (existente → portada) |
|---|---|---|---|
| 1 | Prompts de permiso | stdin `/dev/null`, sesión/Job propio, banderas headless por adaptador, `doctor`, tope + sin-avance | taller `test_stdin_no_cuelga`, `test_agente_colgado_se_detiene_y_releva`; Hermes `test_subscription_process` (Job kill-on-close, shim .cmd) |
| 2 | Entrega no recibida | Sin tecleo: proceso + `result.json` + SHA en origin | taller `test_agente_que_miente…` |
| 3 | LISTO falso | `sha_en_origin` + `Acepta` en copia limpia + `Proved` | ídem; nueva: `test_resultado_sin_sha_no_cuenta` |
| 4 | Coordinador sin herramientas / merge sin revisión | Coordinador es código; `MergeTicket` solo por `compuertas` | taller `test_no_hay_merge_sin_veredicto_y_ci_sobre_el_mismo_sha`; openclaw `test-agent-work-*` (compuertas) portadas |
| 5 | Vigilancia cara | Tick sin modelo; `estado --json` para claw/Hermi | — (estructural) |
| 6 | Cuota y pausas largas | Cooldown por clase (throttle 2 min / cuota ventana / hora declarada), cuarentena con horizonte, 4 posiciones | Hermes `test_subscription_router` (93: advance, bench, quarantine lift); taller `test_cuota_a_media_noche…` |
| 7 | Reinicios | Log + `ukey` + agentes detached + `wip-push` periódico | taller `test_matar_el_daemon_a_la_mitad_no_duplica_nada`; nueva `test_wip_push_periodico_sobrevive_kill` |
| 8 | Compuertas humanas | Solo `humano: login|2fa|captcha|pago`; sonda; regla 4 en código | taller `test_cambios_sin_repro_cuenta_como_aprobado_regla_4` |
| 9 | Merge adelantado | Draft → APROBADO → ready → checks del SHA exacto → `--match-head-commit` | nueva `test_merge_exige_checks_del_mismo_sha_en_github_falso` |
| 10 | Avisos técnicos | Plantillas + `lint_plain` + redacción | taller `test_todas_las_plantillas_pasan_el_filtro`, `test_rechaza_sha_rutas_y_jerga` |

## Reutilizar / portar / retirar

| Pieza | Destino | Qué cambia |
|---|---|---|
| taller `machine.py`, `plan.py`, `store.py`, `notify.py`, `daemon.py` | `motor.nucleo` | Reutilizar. `Plan` gana `repo`; `Policy.implementadores/revisores` se derivan de carriles; eventos de salud entran al mismo log |
| taller `runwrap.py` + Hermes `process.py` | `motor.proceso` | Fusionar: runwrap detached (taller) con Job object, `_resolve_windows_shim`, guardián WSL, prompt privado, redacción y tope de salida (Hermes); `--wip-cada` |
| Hermes `router.py` | `motor.relevo.politica` | **Portar**: el bucle sincrónico se vuelve `siguiente()` puro; se retiran leases/heartbeat (`claim/renew/release_stage`) y `pre_invoke` (autorización poteto) |
| Hermes `state.py` | `motor.nucleo.store` (eventos) | Retirar tablas; conservar semántica de `health`, `cooldown_until`, `stage_attempts` como vistas de `fold` |
| Hermes `types.py`, `catalog.py`, `redaction.py`, `private_files.py`, `adapters/*` | `motor.relevo` | Reutilizar casi verbatim; `invoke` → `comando`+`leer`; `load_routing_source` → `config/modelos.json` del paquete |
| Hermes `model-routing.source.json` v6 | `config/modelos.json` | Mover. `configure_team.py` (perfiles nativos de Hermes) y claw la leen del motor instalado: una tabla |
| Hermes `entrega_pr.py` | `motor.github.publicar` | Reutilizar; añadir draft, `ready`, lectura de checks por nombre, merge con `--match-head-commit` |
| openclaw `scripts/agent-work/merge.py` | `motor.github.compuertas` | Portar la lógica pura de las cinco compuertas; retirar el script |
| Hermes `avisos_sesion.py` | `motor.avisos` (patrón) + `espejo` | Retirar el módulo; reutilizar el reclamo por rename del buzón; el gateway ya no es el canal |
| Hermes `local_orchestrator.py`, `workflow.py`, tools `goncloud_local_*`, `goncloud_engineering_*` | — | **Retirar** (orden de David). El plugin conserva JEV, director, respaldo |
| Hermes Raíces (`raices/*`, `instalar_raices.py`, cron ticker) y PR #37 | — | **Retirar**; cerrar #37 sin mergear. Ideas que sobreviven: criterio con verificador = fila con `Acepta`; autoridad = `.motor.toml` + plan |
| SUA `sistema-agentes.v1.md` (7 esquemas) | docs | Retirar como contrato de runtime (ningún código lo consume; su host R se retiró). El plan en Markdown + `result.json`/veredicto JSON es el contrato |
| openclaw `spool.py`, `proyeccion.py`, `contexto.py`, `reporte.py`, `avisar.py` | — | Retirar (eran para el fork R y los loops por tmux) |
| OpenClaw agentes `main`, `scout`, `reviewer`, `verifier`, `adversary`, `ingenieria`, negocio | se quedan | Reparto de lectura; `implementer` deja de recibir código directo |
| Hermes perfiles (claw "Hermi", scout…, negocio) | se quedan | Igual que arriba; `entrega` e `implementer` se usan solo como adaptadores del motor si acaso |
| Hermes `scripts/hermes.ps1`, tarea `Hermes_Gateway` | se quedan | El gateway sigue "Interactive only"; el **motor** tiene su propia tarea ONSTART sin sesión |

Costo honesto de extraer vs evolucionar taller: extraer `subscriptions/` + `entrega_pr` son ~5k líneas y ~300 pruebas ya curtidas en la PC (opencode 2.x a medias, grok bypass, shims .cmd, Job objects, throttle vs ventana gastada: cada una fue un incidente). El trabajo real es (a) partir `_run_owned` (~400 líneas) en función pura y (b) reescribir `invoke` en `comando`/`leer` en 8 adaptadores; estimo 2-3 días y portar ~150 de las 300 pruebas (las de leases se tiran). Evolucionar taller hasta ahí sería reescribir esas 5k líneas desde cero sin los incidentes aprendidos. Lo que **no** se extrae pesa más que lo que sí: Raíces + orquestador + workflow ≈ 10k líneas se retiran; el motor termina en ~7k.

## Synthesis decision

(vacío; lo llena arena)

## Tradeoffs accepted

- Aceptamos perder el lease con latido de Hermes a cambio de un solo escritor: la exclusividad la da el servicio del SO (una instancia); si alguien lanza dos daemons a mano, `store` lo detecta por un lock de archivo y el segundo sale.
- Aceptamos que un agente huérfano siga corriendo tras una actualización del daemon (gasta cupo) a cambio de no perder trabajo; el daemon nuevo lo readopta por `outcome.json`.
- Aceptamos que la Mac con FileVault "espere" tras un apagón; la alternativa (apagar FileVault o auto-login) baja la seguridad del portátil de David.
- Aceptamos un bot de Telegram directo (token en las tres máquinas) en vez del gateway de cada marca: menos partes, pero el token vive en tres `secretos.env`.
- Aceptamos que la tabla de modelos cambie por PR + etiqueta y tarde hasta 30 min en converger; a cambio no hay dos tablas.
- Aceptamos que una revisión interrumpida por cuota se repita desde cero con otro agente (una revisión no tiene estado que valga guardar).
- Aceptamos que el plan lo escriba la IA que reparte (opus) y no el lead; el validador del plan (Acepta, prueba-real, ≥2 implementadores, sin ciclos) es la red.

## Alternatives considered

- **Evolucionar taller y copiar a mano lo que falte de Hermes.** Interfaz igual de pequeña, pero oculta menos: re-derivar clasificación por proveedor, Job objects y redacción sin las ~300 pruebas. Perdió por costo de redescubrir incidentes ya pagados.
- **Hacer el motor un plugin de Hermes y correr Hermes en las tres máquinas.** Esconde la instalación (ya existe `configure_team.py`), pero expone Hermes entero a la Mini y la Mac, choca con "dos marcas con las mismas apps" y ata el motor al gateway (reinicio del gateway = cadena muerta, hoy medido). Perdió.
- **Mergear Raíces (#37) y hacerlo el motor.** Ya es "ejecutor determinista por contrato con ticker"; pero exige kanban de Hermes, el contrato SUA de un host retirado, tiene estados `paused` y 13 tipos de excepción que preguntan a David (viola el requisito 5 y la orden "sin PAUSADO"). Perdió por forma, no por calidad.
- **GitHub Actions como motor (runners en cada máquina).** Esconde el servicio del SO, pero pone el estado en GitHub, exige runners self-hosted con credenciales de CLIs y no sirve para `humano:` ni para prueba-real por ssh. Perdió (ya se rechazó en la arena de taller).

## Open questions and risks (para David, en llano)

1. La Mac tiene FileVault: tras un apagón no arranca hasta que escribes tu contraseña. ¿Aceptas que un plan en la Mac espere a eso, o prefieres que lo largo se lo des a la Mini?
2. ¿Te sirve un solo chat de Telegram con el encabezado "Hermes:", "OpenClaw:" o "Mac:" para comparar las dos marcas, o quieres dos bots distintos?
3. Para que la PC arranque el motor sin que nadie entre, la tarea programada guarda tu contraseña de Windows. ¿De acuerdo?
4. ¿Cerramos el PR #37 de Raíces sin mergear? Propongo cerrarlo; sus ideas ya viven en el formato del plan.
5. ¿La tabla de modelos se edita en un solo lugar (el repo del motor) y Hermes y OpenClaw la copian de ahí? Hoy Hermes tiene la suya.
6. ¿Muse dentro de WSL es para la primera tanda o puede esperar? WSL no está instalado y cada CLI dentro de WSL necesita su propio login.
7. Cuando llegue un pedido de código a claw o a Hermi, ¿quién escribe el plan: esa misma IA (propuesta) o siempre el lead?
8. Riesgo: `claude setup-token` da un token de larga duración por suscripción; si Anthropic cambia esa regla, el revisor por omisión depende del llavero y en la Mini solo funciona con sesión gráfica (ya la hay). Lo vigila `doctor`.

## Prototype scope (esta Mac, unas horas, sin tocar prod/Orbit/OpenClaw/Hermes)

1. Repo nuevo local `goncloud-motor`: copiar taller → `motor/nucleo`; copiar de `origin/feat/hermes-team` `subscriptions/{types,catalog,redaction,private_files,process}.py`, `adapters/*`, `entrega_pr.py` → `motor/relevo`, `motor/proceso`, `motor/github`; `config/modelos.json` = v6. Objetivo: `python -m motor doctor` corre en la Mac con los 7 CLIs reales (solo `probe`, sin inferencia) y las 19 pruebas de taller siguen verdes.
2. `relevo.siguiente()` puro + portar ~40 pruebas de `test_subscription_router` (avance por clase, bench, cuarentena, bloqueo tras mutación). Adaptadores `claude`, `opencode_zai`, `muse` con `comando`/`leer`; los demás quedan en `NotImplementedError` con prueba de que `doctor` los reporta.
3. `runwrap` fusionado con `--wip-cada`; prueba `kill -9` al runwrap a los 7 s con un agente falso que escribe cada 2 s → la rama tiene el `wip:`.
4. GitHub real inofensivo: repo privado desechable `gon0801/motor-sandbox` con un workflow `rapido` trivial y `.motor.toml`; `plan_demo.md` con un agente real barato (GLM por opencode) y Claude revisor sin ventana: draft PR → verificación → APROBADO → ready → checks → `--match-head-commit` → despliegue a carpeta local → prueba-real. Telegram al chat de David con encabezado "Mac (prueba):".
5. `motor servicio instala` en la Mac (LaunchAgent) y matar el daemon a media corrida → retoma. Fuera del prototipo: Windows, WSL, Mini, skills de las marcas, autoactualización.

## Rollout a las 3 máquinas como UN plan (`motor-01`, lo corre el motor en la Mac)

| Bloque | Entrega | Acepta / prueba-real | humano |
|---|---|---|---|
| A | Paquete publicado en `gon0801/goncloud-motor`, etiqueta `estable`, pruebas en CI | `uv tool install …@estable && motor --version` | — |
| B (dep A) | Prototipo del punto 4 verde en la Mac contra `motor-sandbox` | `motor estado --json` muestra HECHO con prueba real | `login` claude setup-token en la Mac |
| C (dep A) | Mini: instalación, `secretos.env`, LaunchAgent gui, `pmset autorestart 1`, doctor verde para ≥4 CLIs | `ssh gon@mini motor doctor --json` | `login` claude setup-token en la Mini |
| D (dep A) | PC: uv, instalación, tarea ONSTART sin sesión con contraseña, env Kaspersky en `host.toml`, doctor verde | `ssh gwpc motor doctor --json`; prueba-real: reiniciar la PC y ver `motor estado` sin iniciar sesión | `login` contraseña de la tarea; setup-token |
| E (dep C, D) | Skill `motor` instalada en claw y en Hermi; Hermes: PR que retira `goncloud_local_*`/cadena y añade `motor_entrega`/`motor_estado`; OpenClaw: `implementer` fuera del reparto directo | pruebas del plugin; `openclaw agent exec` con la skill devuelve el id de un plan | — |
| F (dep E) | Mismo plan chico en `motor-sandbox` entregado a claw y a Hermi; comparación en un aviso | los dos llegan a HECHO; prueba-real: `gh pr list --state merged` muestra 2 | — |
| G (dep F) | Retiros: cerrar #37, PR en Hermes que borra Raíces/orquestador, PR en openclaw que borra `agent-work/*`; mover la tabla v6 | pruebas de los tres repos verdes | — |
| H (dep F, release) | Primer plan real: INGESTA 01 de Orbit entregado a la marca que David elija | `motor estado` HECHO; prueba-real de Orbit | — |

Fuera del motor: Muse en WSL (segunda tanda), `cursor-agent` en la Mini (sesión gráfica ya la tiene; se activa solo si `doctor` lo ve).

## Next implementation step

Crear `motor/relevo/politica.py` con `siguiente()` extraído de `router._run_owned` y portar las primeras 10 pruebas de `test_subscription_router.py` (avance por cuota, throttle con `retry_after`, bloqueo tras mutación) antes de tocar ningún adaptador.
