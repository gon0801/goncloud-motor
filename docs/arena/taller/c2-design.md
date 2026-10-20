# Candidato C2: GitHub como máquina de estados y como bus

## Problem

Hoy el "scheduler" es un LLM (claw) que relee un PROMPT.md de 30 KB, teclea encargos en ventanas tmux, infiere el estado de la pantalla y se despierta con `nohup sleep`. Cada falla del catálogo es una consecuencia de eso: estado en archivos locales, entrega por teclado, proxies (pantalla, LISTO) tomados como hechos, despertares frágiles, un coordinador que puede quedarse sin herramientas y mergear sin veredicto. David pide lo contrario: él y el lead planean; cada agente implementa en su computadora sin que nadie lo mire; el sistema implementa, revisa, prueba, mergea, despliega y avisa en llano; nada lo frena (ni prompts, ni cuota, ni pruebas pendientes, ni "pide go"). Restricciones duras: Orbit es un repo PÚBLICO (`gh repo view` → PUBLIC, 0 runners), así que un runner self-hosted registrado ahí es un agujero de seguridad; la CI de Orbit ya existe (`rapido` en PR, `completa` solo en push a master o `workflow_dispatch`, `gate` fail-closed, ai-review y CodeRabbit); las CLIs de la Mac ya corren sin ventana y sin preguntas (tabla del grounding); las escrituras a producción las hace un script de un PR aprobado por `ssh goncloud`.

La forma que propongo: **el estado vive en GitHub (issues + labels + checks), el bus son los eventos de GitHub Actions, y la computadora de cada agente es un runner self-hosted registrado en un repo de control PRIVADO que despacha trabajo sobre el repo público.** Ningún LLM coordina; coordinar es un script determinista de 200 líneas. Los LLM solo implementan, revisan y re-planean.

## Usage (caller's view)

### Lo que ve David
Nada que operar. Aprueba el plan en la conversación con el lead y luego recibe Telegram solo en cuatro eventos, ≤8 líneas, sin SHAs ni rutas:

```
✅ Entregado: "Bloqueo de keywords en reconciliación" quedó en producción y probado
   con datos reales de hoy (3 campañas, 41 keywords). Siguen 2 piezas del plan.
🖐 Te toca a ti: la pieza "Alta en Seller Central" necesita tu login. Cuando la hagas,
   cierra la tarjeta "Alta en Seller Central" en la app de GitHub y el plan sigue solo.
⛔ Atorado: "Repricing nocturno" falló 2 veces con dos ayudantes distintos (las pruebas
   con datos reales no cuadran). El resto del plan sigue; el lead ya propuso otro camino.
🏁 Plan "jev-ads-04" terminado: 6 piezas en producción, todas probadas.
```

### Lo que teclea el lead
```
# 1. Con David, escribe el plan como hoy, pero cada fila trae un bloque de datos (ver Shape).
# 2. Compila el plan a issues del repo de control (idempotente: re-correrlo no duplica):
agentctl plan compile plans/jev-ads-04.md        # → 6 issues, labels state:pending/ready
# 3. Ya. El despachador los va tomando. Lectura opcional (no es tablero, es un `gh` ):
agentctl plan status jev-ads-04                   # → fila, estado, PR, ronda, agente
```
El lead no revisa en su ventana: su revisión corre como job `review` con `claude -p` en el runner `mac-lead`, con la misma skill y las mismas trampas (memoria `revision-trampas-que-se-me-pasaron`) como prompt fijo. Si una fila se atora, el job `replan` (también Claude headless) escribe un brief nuevo en la issue y la fila vuelve a `ready`.

### Escenario 1: la fila normal
Issue #12 "D.1 bloqueo-reconcilia" pasa a `state:ready` (sus dependencias cerradas). El despachador lanza `row.yml` (inputs: issue=12, round=1). Job `implement` en runner `mac-impl` (usuario macOS `agente`): crea worktree `~/work/orbit/12`, corre `muse exec --yolo --json` con el brief, el launcher confirma commits + push a `agente/12-d1-bloqueo`, abre PR draft con cuerpo generado desde hechos (diff, salida de `acceptance`). Job `verify` corre los comandos `acceptance` de la fila sobre ese SHA (no sobre lo que Muse dice). Job `review` en `mac-lead`: `claude -p --json-schema review.v1` → publica review en el PR y un check run `lead-review` sobre el SHA. Si `CAMBIOS`: el run termina re-despachándose con round=2 (mismo branch, mismo agente, hallazgos en el PR). Si `APROBADO`: `land.yml`: `gh pr ready`, dispara `Quality` por `workflow_dispatch` sobre el branch (eso es `completa`), espera `gate` del SHA, lee ai-review y CodeRabbit del SHA, `gh pr merge --squash --match-head-commit`. Job `deploy` (runner `mac-impl`): corre los scripts `deploy` de la fila por `ssh goncloud`, luego `real_data_probe`; adjunta la salida; cierra la issue; `notify` manda el Telegram de entrega. El cierre de #12 dispara al despachador, que libera #13 y #14 en paralelo (dos runners, dos worktrees).

### Escenario 2: cuota a las 3 a.m.
Muse devuelve 429 semanal a mitad de `implement`. El launcher clasifica la salida (`quota`), hace `git add -A && git commit -m "wip: relevo por cuota"` y push, y termina el job con `outcome=quota`. El workflow escribe la variable de repo `COOLDOWN_MUSE=2026-10-26T09:00Z` (lee la hora de reset si la CLI la imprime; si no, +7 d para semanal, +5 h para horaria) y se re-despacha con el mismo round y `agent=glm`, el siguiente de la lista `agents:` de la fila. GLM arranca del branch con el brief y `git log`. Nadie despierta a nadie; no existe un estado PAUSADO que "solo David quita": el cooldown expira solo y el despachador lo lee. Si todos los agentes de la lista están en cooldown, la fila queda `state:ready` y el despachador la relanza en el tick siguiente al vencimiento; David recibe un único Telegram si la espera supera 2 h ("sin cupo hasta las 9; sigue solo").

### Escenario 3: paso que pide permiso
Fila con `deploy: [scripts/jev/aplicar_config_42.sh]` que escribe en Postgres de producción, y un `rm -rf` de un directorio viejo dentro del script. No hay prompt que atender: (a) el implementador corre con bypass dentro de un worktree del usuario `agente`, que no tiene acceso a los archivos de David ni a llaves que no sean las de la fila; (b) la escritura a prod no la hace el implementador sino el job `deploy`, que ejecuta el script tal como quedó mergeado (revisado por el lead y por los bots) con la llave ssh de `agente`; (c) el plan aprobado ES la autorización (orden de David), así que el job no consulta a nadie. Un paso que solo un humano puede hacer (login, 2FA) se declara en el plan como fila `kind: handoff`: no se despacha a ningún runner, manda el Telegram "te toca a ti" y se cierra cuando David cierra la tarjeta desde el celular; las filas hermanas que no dependen de ella siguen.

## Shape

### Datos (fuente única por invariante)
- **Fila** = una issue del repo de control `gon0801/agent-control` (privado). Cuerpo = bloque YAML `row.v1` (`RowSpec` en sketch.py): `repo`, `plan`, `id`, `kind` (code|docs|handoff), `depends_on`, `brief`, `acceptance` (comandos que prueban el comportamiento, corren sobre el SHA), `real_data_probe` (comando de solo lectura contra prod; obligatorio en `kind: code` que toca datos), `deploy` (scripts idempotentes del propio PR), `agents` (lista ordenada de relevo), `max_rounds`.
- **Estado de la fila** = labels, un solo valor por eje: `state:{pending,ready,running,handoff,atorado,done}`, `round:N`, `agent:<id>`, `pr:<n>`. Derivado, nunca copiado: dependencias satisfechas se calculan de `depends_on` + issues cerradas; "aprobado" se calcula de los checks del SHA, no de un label.
- **Salud de proveedores** = variables de repo `COOLDOWN_<AGENT>=<iso>`; escritura idempotente (max de fechas).
- **Veredicto** = check run `lead-review` sobre el SHA de Orbit (éxito/fallo) + review de GitHub con JSON `review.v1` (`Review` en sketch.py: verdict, bloqueantes con comando reproducible, no bloqueantes). Lo que no tenga comando reproducible no es bloqueante (regla 4 de quality-kit, encodificada en el schema).
- **Evidencia** = artifacts del run (salida de acceptance, de la sonda real, del deploy) enlazados en el PR; el cuerpo del PR lo escribe el sistema a partir de ellos, no el agente.

### Topología
- **Repo de control privado** `agent-control`: workflows (`dispatch.yml`, `row.yml`, `land.yml`, `replan.yml`), `agentctl/` (Python), secretos (`ORBIT_APP_ID`, `ORBIT_APP_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Los runners self-hosted se registran SOLO aquí. Orbit (público) no cambia salvo una regla de branch protection.
- **GitHub App "agent-control"** instalada en Orbit con `contents:write`, `pull_requests:write`, `checks:write`, `actions:write`. Cada job acuña un token de instalación de 1 h; no hay PAT de larga vida. Los commits salen como `agent-control[bot]`.
- **Runners** en la Mac bajo el usuario macOS `agente` (LaunchAgent, login automático, `pmset` sin sueño): `mac-impl-1`, `mac-impl-2` (labels `impl`), `mac-lead` (label `lead`). Un runner = un job a la vez, así la ocupación única es estructural; dos filas en paralelo = dos runners. El Mac Mini y `gwpc` se suman con el mismo instalador y otras labels. Minutos self-hosted = gratis; la CI de Orbit sigue en hosted (gratis por público).
- **Seguridad del repo público**: nada en `agent-control` escucha eventos de PR de Orbit; solo despacha sobre branches que el propio bot empujó o sobre `master`. Un PR de fork jamás ejecuta código en la Mac. `pull_request_target` prohibido en el repo de control (test en `agentctl/tests`).

### Flujo (tres archivos para rastrearlo)
`dispatch.yml` (cron 10 min + `issues` + `workflow_run completed`) → `agentctl.dispatch.tick()` puro: lee issues, variables y runs activos, devuelve `[Dispatch(row.yml, {issue, round, agent})]`; nunca despacha una fila con run activo (concurrency group `row-<issue>`, `cancel-in-progress: false`). `row.yml` = una ronda: `implement` → `verify` → `review` → `outcome` (re-despacha round+1, relevo, `land`, `atorado`, o `replan`). `land.yml` = ready → `completa` por dispatch → espera `gate` → bots → merge → deploy → sonda real → cerrar issue → notificar.

### Invariantes encodificadas
- `Launcher.run()` es el módulo profundo: recibe `(agent, prompt, cwd, timeout)` y oculta flags sin preguntas por CLI, `stdin=DEVNULL` y sin TTY (un prompt no puede ni dibujarse), parse de la salida JSON, clasificación `quota|auth|timeout|error|ok`, commit `wip:` + push al salir por cualquier causa, y heartbeat de push cada 10 min. Un agente desconocido o sin flag de no-preguntar no compila (`AgentId` es Enum con sus flags).
- Merge solo por `land.yml`, y branch protection en `master` exige checks `gate`, `completa-rama`, `lead-review`, `ai-review` (sin High) sobre el SHA actual (`strict`). `--match-head-commit` queda redundante; se mantiene.
- El estado de una fila lo escribe un solo actor a la vez (grupo de concurrencia por issue); el despachador solo toca `pending→ready`. Per separate-before-serializing-shared-state.
- Toda transición es idempotente: branch existe → se reutiliza; PR existe → se reutiliza; review del mismo SHA → se salta; ya mergeado → `land` sale 0; `deploy` graba label `deployed:<sha>` antes de notificar.
- Validación en el borde: `RowSpec.parse()` rechaza filas `code` sin `acceptance` o sin `real_data_probe` cuando `touches_data: true`; `Review.parse()` rechaza bloqueantes sin `reproduce`. Adentro, tipos. Per boundary-discipline.
- Lo que el sistema NO hace: no tiene tablero ni vigía; no lee pantallas; no pregunta; no guarda estado en la Mac; no corre la batería completa local (sigue el hook `no_bateria_local.py`).

### Catálogo de fallas → mecanismo
| # | Tema | Cómo queda imposible o se autocura |
|---|---|---|
| 1 | Prompts de permiso | Sin TTY ni stdin; flags de no-preguntar son parte del tipo `AgentId`; `timeout-minutes` en cada job; un cuelgue termina en relevo, no en espera. Bypass acotado al usuario `agente` y su worktree. |
| 2 | Entrega no confirmada | No hay tecleo ni composer: el agente es un subproceso; "entregó" = exit code + JSON válido + commits en el branch. |
| 3 | LISTO falso / cerrado sin cerrar | La palabra del agente no se consume: `verify` corre `acceptance` sobre el SHA; `deploy` corre `real_data_probe`; la issue la cierra `land` tras evidencia; el PR lo redacta el sistema. |
| 4 | Coordinador sin herramientas | No hay coordinador LLM; `dispatch.tick()` es Python puro y testeado; el merge es un job con checks requeridos: un veredicto ausente es un check ausente y GitHub rechaza el merge. |
| 5 | Costo del sondeo | Cero tokens de vigilancia: eventos de GitHub + cron de 10 s en runner propio. |
| 6 | Cuota / auth | Clasificación en el launcher → `COOLDOWN_<AGENT>` con vencimiento → relevo al siguiente de `agents:` con el trabajo en el branch; sin PAUSADO humano. |
| 7 | Reinicios / estado perdido | Estado en GitHub, no en la Mac; runner como LaunchAgent al login; GitHub encola hasta 24 h; `wip:` cada 10 min; un run muerto se re-despacha en el tick siguiente. |
| 8 | Esperas humanas | Solo `kind: handoff` espera a David y no bloquea hermanas; rondas agotadas → relevo → `replan` del lead, nunca "decide David"; `land` no espera pruebas futuras: la sonda real corre tras el deploy y si falla abre una fila de corrección. |
| 9 | Merge adelantado | Branch protection + `land.yml` único camino + token del bot sin `administration`; `completa` corre sobre el branch antes del merge (dispatch de `Quality`), así master no queda rojo. |
| 10 | Avisos técnicos | `notify.render()` con cuatro plantillas fijas en llano, ≤8 líneas, sin SHA ni rutas; test que falla si el texto contiene `/` de ruta o 7+ hex. |

Patrones del grounding: (a) esperar deja de ser la omisión porque todo estado tiene una transición de tiempo (cooldown, timeout, cron); (b) no hay proxies: solo exit codes, checks y salidas de comandos; (c) el despertar es GitHub; (d) la lista `agents:` por fila elimina el proveedor único; (e) las reglas viven en código testeado del repo de control, no se copian en PROMPT.md; (f) el merge no puede correr antes que la revisión.

## Synthesis decision

## Tradeoffs accepted
- Aceptamos un repo privado extra y una GitHub App en exchange por no registrar jamás un runner en el repo público y por tokens de 1 h sin PATs.
- Aceptamos que una ronda sea un run completo (sin bucles dentro del workflow) en exchange por reintentos gratis (`re-run`) y visibilidad por run.
- Aceptamos un usuario macOS `agente` con login automático y la Mac sin dormir en exchange por que `bypassPermissions` no toque los archivos de David y por sobrevivir reinicios.
- Aceptamos que el lead revise headless (misma skill, menos contexto conversacional) en exchange por que la revisión nunca dependa de una ventana abierta ni de un tecleo.
- Aceptamos un cron de 10 min como respaldo (latencia máxima 10 min si se pierde un evento) en exchange por no tener ningún proceso propio vivo.
- Aceptamos que `completa` corra una vez más sobre el branch (≈3-4 min hosted) en exchange por que master no vuelva a quedar rojo.
- Aceptamos branch protection con "include administrators" (David ya no mergea a mano antes de CI) en exchange por el tema 9 cerrado de raíz.

## Alternatives considered
- **Demonio propio en la Mac (cola SQLite + launchd)**: interfaz igual de chica, pero esconde menos: hay que escribir cola, reintentos, concurrencia, visibilidad y sobrevivir reinicios; y vuelve a ser un proceso que puede morir como el gateway congelado. Perdió por reinventar lo que Actions ya da.
- **Runners self-hosted directamente en Orbit + `workflow_dispatch` desde el lead**: menos piezas, pero un repo público con runners propios expone la Mac a cualquier fork PR y mezcla secretos del bot con un repo público. Inaceptable por seguridad.
- **Cursor Cloud Agents / `cursor-agent worker`** (lo que hace GrokBot): computadora persistente en la nube y revisor incluido, pero la implementación saldría de la Mac (David pidió "cada agente en su compu"), no cubre Muse/GLM/Kimi ni el ssh a goncloud, y el revisor seguiría sin ser el lead.
- **Mantener claw como coordinador pero con herramientas garantizadas**: no quita el proxy de pantalla ni el PROMPT.md como scheduler; cada regla seguiría copiándose de loop en loop.

## Open questions and risks
- ¿David acepta que `master` quede protegido también para administradores (hoy él mergea antes de CI)? Sin eso el tema 9 queda cerrado para los agentes pero no para él.
- ¿Crear el usuario macOS `agente` con sesiones iniciadas de Claude Max, Muse, GLM, Kimi, Codex? Es una tarde de logins una sola vez; las OAuth de Claude viven en el Keychain de ese usuario, por eso el runner debe ser LaunchAgent (no Daemon).
- ¿Los hooks de usuario de Claude (SessionStart lentos, PreToolUse) deben cargar en el job `review`? Propongo sí (misma skill y candados que hoy) con un `--settings review.json` que solo añade el schema; a confirmar que `-p` no pierde la OAuth con ese flag.
- ¿Qué relevo se activa cuando el agente de la fila es Claude (lead `replan`/`review`) y la cuota semanal de Claude se agota? Propongo `review` sin relevo (orden: el lead revisa todo) y cooldown con aviso único; `replan` sí releva a Codex.
- Riesgo: la MacBook cerrada o fuera de casa; mitigación: Mac Mini con runners `impl` idénticos (labels), el despachador no distingue máquina.
- Riesgo: el límite de 24 h de cola de GitHub si todos los runners están offline; el despachador lo cubre re-despachando, pero una ausencia de días acumula filas; es aceptable (nada se pierde).

## Prototype scope (menos de una hora)
Repos descartables: `gon0801/agent-control-proto` (privado) y `gon0801/agent-target-proto` (público, un paquete Python con un test rojo y un `scripts/deploy_echo.sh`). Pasos: (1) registrar un runner `mac-impl` en el repo privado bajo el usuario actual (sin crear `agente` aún); (2) subir `row.yml` + `dispatch.yml` + `agentctl/` mínimo (RowSpec, Launcher con `codex` y `claude`, Review schema, notify); (3) crear a mano una issue `row.v1` ("hacer pasar el test y añadir uno nuevo") con `agents: [codex, claude]`; (4) dispatch → implement → PR draft en el target → verify (`pytest`) → review con `claude -p --json-schema` → land (ready, `completa` dispatch, merge, `deploy_echo.sh` por ssh a goncloud en modo `echo`) → Telegram. (5) Repetir con `AGENT_FORCE_QUOTA=1` en codex para ver el relevo a claude con el `wip:` en el branch. Criterio de éxito: cero intervención desde la issue hasta el Telegram; el PR se mergea solo con los checks presentes; el relevo deja `COOLDOWN_CODEX` y termina con claude.

## Migration path for Orbit
1. Instalar la App en Orbit; branch protection en `master` con checks `gate`, `completa-rama`, `lead-review`; crear `agente` en la Mac y registrar `mac-impl-1/2` y `mac-lead`.
2. Portar `avisar.sh` a `notify.py` con el Bot API (quita la dependencia del worktree borrado).
3. Añadir el bloque `row.v1` a las filas de un plan chico real (candidato: el residual R.1 de jev-ads-03 o el lote de inertes D.3) y compilar; correrlo entero con los loops actuales apagados para esa fase.
4. Tras una fase limpia, retirar `prompt-claw` como generador de loops (queda como generador de briefs `row.v1`), y los loops de `~/.local/state`.
5. Sumar Mac Mini y `gwpc` como runners con labels; el plan no cambia.

## Next implementation step
Escribir `agentctl/launcher.py` con `AgentId` (flags por CLI) y `Launcher.run()` con su test de clasificación de cuota usando salidas reales capturadas de Muse 429 y GLM 5 h.
