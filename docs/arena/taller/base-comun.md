# Grounding: rediseño del sistema de trabajo con agentes de David (2026-10-19)

## Lo que pidió David (dueño, no técnico), textual
"no me convence ese sistema, diseña y prototipa otras mejores formas de manejar los planes, los agentes trabajan en sus propias computadoras normalmente en la Mac nosotros tomamos decisiones y planeamos pero implementación que cada agente lo haga en su compu diseña la mejor forma de hacerlo para no tener problemas de seguimiento, bloqueantes de fallos o permisos, dejar un sistema como grokbot que usa cursor implementa revisa y avisa cuando entrega y nada lo para hasta terminar y probar"

Roles deseados: David + el lead (Claude Code, en la Mac) planean y deciden. La implementación la hace cada agente en su propia computadora (normalmente la Mac). El sistema implementa, revisa, prueba, avisa cuando entrega, y nada lo detiene hasta terminar y probar.

## Referencia: GrokBot (producto de xAI/Cursor)
- Cada bot tiene su propia computadora persistente (VM en la nube, siempre encendida); usa archivos, navegador y sesiones guardadas.
- Disparadores: mensaje, rutina (horario, evento de Slack/Git) u otro bot.
- Revisor automático (Auto Review): un agente aparte revisa cada acción propuesta y la permite, la bloquea o la escala al humano, según reglas escritas en lenguaje natural; "ask first" gana en conflicto. Por omisión, comandos en la computadora del usuario piden aprobación.
- Handoff: solo pasos que solo un humano puede hacer (login, 2FA, captcha, pago) se pasan al humano y luego vuelve el control.
- Estados de atención visibles: "necesita atención", "resultado nuevo", "trabajando". Notificación por bot al terminar o al esperar; al teléfono a los 3 min si espera input.
- Projects: un bot orquestador coordina a otros, memoria compartida del proyecto (decisiones y convenciones). Para código lanza Cursor Cloud Agents; Bugbot revisa PRs (hoy no se puede disparar desde agentes lanzados por GrokBot: limitación conocida).
- Errores visibles en un área de notificaciones; reset desde snapshot.

## Cómo funciona hoy (medido)
- **claw** = OpenClaw (agente `main`) en una Mac Mini (gateway 2026.10.4 público, remoto por ssh/Tailscale); CLI de la Mac 2026.9.7. Cada loop es una sesión `agent:main:<loop>`; un turno se dispara con `openclaw agent --session-key ... --message ... --timeout 3600` (síncrono). claw ejecuta en la Mac vía `exec host=node` (OpenClaw.app como nodo, PATH reducido).
- Un loop = `~/.local/state/<loop>-loop/` con `PROMPT.md` (~30 KB, el LLM lo relee en cada turno como único "scheduler"), `estado.md` (texto libre, crece a 290 KB), `arranque.txt`, `avisar.sh`. Generados por la skill `prompt-claw` del lead.
- Entrega por tmux: claw escribe `encargo-<id>.md` y lo TECLEA en la ventana TUI del implementador (Muse en `muse-goncloud-Orbit`) con `send-keys`, juzgando el texto con `composer-real.sh` (heurística de colores SGR). Handshake por archivos: `ACK-<id>`, `LISTO-<id>` (o `FRENADO:`), brief al revisor en `/tmp/brief-...`, `ACK-brief-<id>`, `VEREDICTO-<id>` (APROBADO|CAMBIOS).
- Despertares: sin vigía ni crons (David 17 y 19-oct). El último paso del implementador y del revisor es `nohup openclaw agent ... &` para despertar a claw. Respaldo: `nohup sh -c 'sleep 14400; openclaw agent ...'` por encargo (no sobrevive reinicio).
- Revisión: Claude Code en su ventana tmux (`claude-goncloud-Orbit`), mismo mecanismo de tecleo. Rondas CAMBIOS → arreglo → hasta 5 por implementador.
- Merge: claw hace `gh pr ready` (dispara `completa` en CI), espera `rapido`/`completa`/`gate`, lee el comentario del bot ai-review del SHA final, `gh pr merge --squash --match-head-commit`. Deploy: claw corre scripts del PR por `ssh goncloud`.
- Avisos a David por Telegram: `avisar.sh` → `openclaw message send --channel telegram` (hoy depende de `modelos.py` de un worktree archivado `/Users/dn/dev/wt/agent-work-host` @c93dac90; G main lo borró hoy).
- La "ruta de encargos" (managed tasks R: encargo.py, brief.py, host run-per-task de `opencode run`, deadline 3600 s, contrato ready.v1) se RETIRÓ hoy (G #350): el gateway público no la trae.
- Repos: Orbit (`gon0801/goncloud-Orbit`, Python/Postgres, CI GitHub Actions: `rapido` en draft, `completa` al pasar a ready, `gate`; bot ai-review DeepSeek + CodeRabbit). G = `/Users/dn/dev/goncloud-openclaw` (config/skills de claw, scripts mac). Producción en `ssh goncloud`.
- Máquinas: Mac (MacBook Pro, donde corren los agentes y el lead), Mac Mini (gateway de claw), goncloud (servidor Linux de producción), PC Windows `gwpc` (Hermes, otro equipo de 9 perfiles).

## CLIs en la Mac (todos pueden correr sin ventana, medido)
| CLI | headless | sin preguntas | salida | reanudar | hooks | auth |
|---|---|---|---|---|---|---|
| claude 2.1.302 | `claude -p` (y `--bg`) | `--permission-mode bypassPermissions` / `--dangerously-skip-permissions` | `--output-format json/stream-json`, `--json-schema` | `-r`, `--session-id` | completos (Stop, SessionEnd...) | suscripción Max (no usar `--bare`: no lee OAuth) |
| muse 1.4.11 | `muse exec --prompt-file F` | `--yolo` | `exec --json` JSONL, `--output-schema` | `muse resume`, `exec --session-id` | Stop/SessionEnd/Notification (vacíos) | cuenta Meta (cuota semanal) |
| grok 1.0.62 | `grok -p` / `--prompt-file` | `--always-approve` (ya en config) | json / streaming-json, `--json-schema` | `-r`, `-s` | propios + importa los de Claude por omisión | OAuth xAI |
| codex 0.160 | `codex exec` | `--dangerously-bypass-approvals-and-sandbox` | `--json`, `-o`, `--output-schema` | `exec resume` | hooks.json, `notify` | ChatGPT (suscripción) |
| cursor-agent 2026.09.28 | `cursor-agent -p` | `--yolo` + `--trust` + `--approve-mcps` | json / stream-json | `--resume` | — | cuenta Cursor; tiene `worker` (agente cloud self-hosted) |
| opencode 2.0.29 | `opencode run` | `--auto` | `--format json` | `-s` | plugins JS | 9 proveedores (zai-coding-plan suscripción; otros de pago) |
| kimi 2.1.1 | `kimi -p` | `--auto` | stream-json | `-S` | — | suscripción |
| qwen 0.25 | `qwen "<task>"` | `-y` | json | `-r` | sí | API de pago |
| zcode 3.14 (GLM) | `zcode -p` | `--mode yolo` | `--json` parcial | `--resume` | sí | Z.AI |
Todos (salvo opencode/kimi/zcode) soportan worktree propio (`-w`). Un wrapper zsh convierte los comandos interactivos en tmux; con rutas absolutas y sin TTY se evita. Hooks de Claude cargan en corridas headless (PreToolUse `no_bateria_local.py` bloquea la batería local completa; SessionStart lentos).

## Catálogo de fallas (resumen medido)
1. Permisos: un carril 7 h parado en un prompt (lanzado sin flag); `rm -rf` que pidió aprobación y nadie contestó (6 h 54 min); Muse "approval prompts disabled" 3 veces → aprobaciones a mano toda la noche; diálogos en la ventana del revisor.
2. Entrega no confirmada: fantasmas del composer, línea sin enviar 1.5 h, Ctrl-C mató opencode 4 veces, copy-mode tragó send-keys, regla "solo si la ventana está ociosa" atascó 15 veces.
3. LISTO falso o parcial, "cerrado" sin cerrar: fase reportada terminada que nunca arrancó (8 h), LISTO sin SHA de un worker, "construido" y 4 defectos con datos reales (10 h), "al 100" con pendientes.
4. Sesión coordinadora sin herramientas: jev-ads-03 sin `managed_tasks_*` → PR #470 (escribe a Amazon) mergeado SIN veredicto; cron en la sesión equivocada inundó Telegram; subagentes del vigía cortaron herramientas.
5. Costo del sondeo: vigía 63k-360k tokens por despertar, falsas alarmas de "sin avance".
6. Cuota/auth: GLM 5 h y semanal, Muse 429 y semanal, Claude semanal; relevos improvisados; PAUSADO duró 5 h tras el reset porque "solo David quita PAUSADO".
7. Reinicios y estado perdido: gateway congelado (sesión de 101 MB), reinstalación perdió crons 2 días, reinicio de la Mac mató la sesión de Muse, cambio de ruta de opencode ATORADO 2h20.
8. Esperas humanas: PR verde y aprobado retenido 11 h por una compuerta de prueba; 4 h parado por compuerta; "mismo bloqueante dos rondas" pidió decisión de David 3 veces.
9. Merge adelantado: #470 sin veredicto, #428 sin `completa` (master rojo), #412 con High abierto.
10. Avisos: mensajes técnicos que David no entiende.

Patrones: (a) esperar es la falla por omisión; (b) se toman proxies (pantalla, ledger, LISTO) como hechos; (c) el mecanismo de despertar es frágil y caro; (d) un solo proveedor por papel = punto único de falla; (e) las reglas se copian de loop en loop sin re-verificar; (f) el merge corre antes que la revisión bajo presión.

## Órdenes vigentes de David (restricciones de diseño)
- El plan es la autorización completa: nadie le pide permiso ni "go"; incluye merges, deploys, migraciones y escrituras previstas en el plan.
- Nunca esperar por una prueba; prohibido perder tiempo; carriles en paralelo; pruebas de 1-2 días como máximo.
- Sin vigía, sin tablero; solo avisos de eventos por Telegram en lenguaje llano (≤8 líneas, sin SHAs ni rutas).
- El lead (Claude) revisa TODO bloque de código ("dejamos las revisiones"); sin revisión cruzada con otros modelos como paso del lead; los bots del PR y CI sí.
- Datos reales antes de "construido"; "al 100" sin pendientes chicos.
- Si se acaba la cuota de un agente, relevo automático a otro (Muse→GLM, Kimi como relevo).
- "Nadie lee tu pantalla: no preguntes" (los agentes no preguntan).
- Merge: `gh pr merge` permitido; flujo PR draft → APROBADO → ready → `completa` → merge. Nunca `--no-verify`, nunca push a master, nada de secretos en repos públicos.
- Las escrituras directas del lead (Claude) a producción las bloquea el entorno; las ejecuta un agente implementador o el coordinador con scripts del PR aprobado.
