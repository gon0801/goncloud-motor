# Base común: rediseño del motor de planes de David (2026-10-20)

Léase completo. Hechos verificados por exploradores de solo lectura el 2026-10-19/20, salvo donde dice "no verificado".

## Lo que David decidió (requisitos; no se discuten)

1. Un **motor de planes** único, igual en tres computadoras: la Mac (MacBook de David, donde trabaja el lead = Claude Code), la Mac Mini (donde vive OpenClaw) y la PC Windows (donde vive Hermes; Muse se usará dentro de WSL Linux).
2. **Un plan corre completo en la máquina donde se entrega.** No se reparte entre máquinas. Si se lo da a Hermes, corre en la PC; a OpenClaw, en la Mini; si el lead lo arranca en la Mac, en la Mac.
3. **Instalación idéntica** en las tres (mismo programa, mismos papeles de agentes, misma tabla de modelos, mismas reglas).
4. **Retoma solo tras apagón o reinicio inesperado**; arranca al prender la máquina **sin que nadie inicie sesión**; el trabajo de los agentes se empuja a GitHub cada pocos minutos. Si la máquina queda apagada, su plan espera (aceptado).
5. **Corre solo hasta terminar y probar**: implementa, revisa (otro agente), corrige, mergea, despliega si el plan lo dice, prueba con datos reales y avisa. David no aprueba nada ni vigila. Solo pasos humanos: login, 2fa, captcha, pago.
6. **Multiproyecto**: cualquier repo de David (Orbit es solo uno de muchos). Nada específico de un repo en el motor; lo específico va en el plan o en un archivo del repo.
7. **OpenClaw y Hermes quedan como "dos marcas distintas con las mismas apps"**: los dos usan el estilo de OpenClaw, una IA que reparte tareas chicas de solo lectura (investigar, contestar, revisar) a sus agentes; todo lo que cambie código, aunque sea una línea, se convierte en plan y se pasa al motor. **La cadena fija de 7 pasos de Hermes se retira.** David quiere comparar estabilidad entre las dos marcas para elegir su agente principal; por eso todo lo demás debe ser idéntico.
8. **Revisor**: por omisión Claude (el lead, sin ventana); si no está disponible, cualquier agente que NO haya trabajado en ese bloque.
9. **Un solo canal de avisos** por Telegram, en español llano (≤8 líneas, sin SHAs ni rutas).
10. Órdenes vigentes: el plan es la autorización completa; nunca esperar por una prueba; sin vigía ni tablero; nunca `--no-verify` ni push a la rama principal; nada de secretos en repos públicos; datos reales antes de decir "listo".

## Prototipo existente: taller (~/dev/goncloud-taller)
Python 3.11+ solo stdlib, ~1,650 líneas, 19 pruebas en verde (11 puras + 8 de punta a punta del catálogo de fallas sobre repos desechables con agentes simulados). Léelo: README.md, docs/diseno.md, taller/*.py, tests/.
- Daemon sin modelo: tick = ingerir (inbox + intentos terminados) → fold(log) → decide → ejecutar. Log de eventos append-only en SQLite (único escritor = daemon). CLI escribe en inbox/.
- `runwrap`: agente sin ventana (stdin /dev/null, sesión propia, tope, detector de falta de avance, wip-push al terminar). POSIX (os.killpg, start_new_session).
- Verifica SHA contra origin, corre `Acepta` de cada fila en copia limpia; merge solo con `MergeTicket` (entrega verificada + APROBADO + CI verde sobre el mismo SHA); relevo por clasificación (cuota con hora, auth, transitorio, falla, sin avance, mintió, sin cambios); cooldown que vence solo; sin estado FALLADO/PAUSADO; aviso "atorado" único si nadie puede; revisores_para excluye a los autores del bloque.
- Prototipo-only: `effects.run_ci` (lanza NotImplementedError para "github"), `merge` (squash local a repo bare), `deploy` (carpeta local), Telegram por Bot API con token en env. Formato de plan y ejemplos con sabor a Orbit (tablas Fila|Contenido|Acepta, `prueba-real`, `despliegue`).

## OpenClaw (Mac Mini, gon@100.73.187.5)
- Gateway OpenClaw 2026.10.4 público (launchd), sin el fork "R" (las tareas gestionadas/encargos por CLI se retiraron el 2026-10-19). CLI de la Mac 2026.9.7 (desfasado).
- Agentes: `main` = claw (opus; único con Telegram; puede `sessions_spawn` a 19 agentes), `ingenieria`, `implementer` (escribe código), `verifier`, `reviewer`, `adversary`, `scout`, `operaciones`, de negocio (amazon, meli, odoo...), y agentes ACP (runtime acp, oneshot) claude, codex, opencode, kimi, qwen, cursor, grok. Exec en la Mini (`host: gateway`), repos en `/Users/gon/dev` (remoto `git@github-claw:`), gh autenticado como gon0801.
- Hoy un plan grande lo corrían loops de claw en la Mac: PROMPT.md enorme, claw teclea en tmux, ACK/LISTO/VEREDICTO por archivo, despertares por `nohup openclaw agent`. Frágil: 41 respuestas perdidas en un día (sessions_send), resultados recortados, sesiones que se borran diario, reinicio del gateway corta lo que corre.
- Comandos útiles: `openclaw agent exec --cwd <dir> --message-file <f> --json` (un turno headless aislado; verificado solo en el cliente 2026.9.7), `openclaw message send --channel telegram --target <chat>` (canal actual de avisos), `openclaw system event --mode now --session-key ...` (despertar a una sesión).
- CLIs en la Mini: claude 2.1.304, muse 1.4.11, cursor-agent (falla: "login keychain is locked"), opencode 2.0.32, codex 0.163, kimi 2.2.0, grok 1.0.62, qwen, gh, git, uv. Sin Homebrew ni tmux. Python del sistema 3.9 (uv resuelve 3.11+).
- Contrato existente sin uso: `docs/spec/sistema-agentes.v1.md` en /Users/dn/dev/goncloud-openclaw (SUA, 7 esquemas: meta/raíz, unidad, resultado, evidencia, control, flota, excepción; patrón operationId/commandId con recibo durable). Atado conceptualmente al fork R (ya retirado).

## Hermes (PC Windows "Gon-PC", ssh gwpc, usuario ehven)
- Hermes Agent v0.22 (open source de Nous Research) + plugin propio `goncloud-team` (repo gon0801/goncloud-hermes; checkout de la Mac desfasado 61 commits; lo vigente en origin/feat/hermes-team).
- 9 perfiles con los mismos papeles que OpenClaw (claw "Hermi" director y único que habla con David, operaciones, ingenieria, scout, implementer, verifier, adversary, reviewer, entrega) + especialistas de negocio. Kanban nativo, cron, gateway Telegram.
- Cadena fija (a retirar): scout→ingenieria→implementer→verifier→adversary→reviewer→entrega, driver en `local_orchestrator.py` (2,250 líneas), worktree `cadena/<id>` por cadena, una cadena viva por repo, sin tope de tiempo (#43), árbol sucio bloquea relevo, máx. 2 rondas.
- **Piezas que valen la pena**: `subscriptions/router.py` + `adapters/*` (relevo entre 4 posiciones por carril, arriendos, cuarentena, "continuación" tras mutación), `subscriptions/process.py` (control de procesos en Windows: Job objects, `_resolve_windows_shim` para shims .cmd de npm, terminación de árbol), `entrega_pr.py` (push y PR por API fuera del sandbox, idempotente), `avisos_sesion.py` (buzón durable de avisos inyectados a la sesión de Telegram), tabla `model-routing.source.json` v6 (8 carriles × 4 posiciones: codigo Sonnet→K3→GLM→Grok; planear Opus→K3→GLM→Grok; verificar GLM→Grok→Sonnet→K3; investigar Grok→K3→GLM→Sonnet).
- "Raíces" (SUA-07, ~6k líneas): segundo ejecutor de planes por contrato con ticker cada minuto; su ejecutor de tarjetas (PR #37) sin mergear.
- PC: claude 2.1.303, codex 0.163, grok 1.0.62, kimi 2.2.0, opencode 2.0.32, cursor-agent (sin adaptador en Hermes), gh 2.102 (gon0801), git 2.56, Python 3.14 solo el de Hermes; WSL NO instalado (se instalará para Muse). Gateway arranca por tarea programada al iniciar sesión ("Interactive only"): hoy nada corre sin sesión. Kaspersky intercepta TLS: todo proceso necesita NODE_EXTRA_CA_CERTS, SSL_CERT_FILE, REQUESTS_CA_BUNDLE.

## Mac (MacBook de David)
Los 7 CLIs (claude, muse, grok, codex, cursor-agent, opencode, kimi) con banderas headless verificadas (`taller doctor`), gh, worktrees en /Users/dn/dev/wt. La Mac se duerme al cerrar la tapa. El lead (Claude Code) vive aquí. Las escrituras del lead a producción las bloquea su entorno.

## Catálogo de fallas a evitar (10 familias, medido sep-oct 2026)
1 prompts de permiso que paran horas; 2 entregas no recibidas (tecleo en ventanas); 3 LISTO falso / "cerrado" sin cerrar; 4 coordinador sin herramientas → PR mergeado sin revisión; 5 vigilancia cara en tokens; 6 cuota y pausas más largas que la cuota; 7 reinicios y estado perdido; 8 compuertas humanas de horas; 9 merge antes de revisión/CI; 10 avisos técnicos. Patrones: esperar es la falla por omisión; proxies tomados como hechos; despertar frágil y caro; un proveedor por papel = punto único; reglas copiadas sin re-verificar; merge adelantado bajo presión.

## Credenciales y arranque sin sesión
Ver el anexo `credenciales.md` en esta misma carpeta si existe (lo escribe otro explorador). Dato conocido: en macOS varios CLIs guardan la sesión en el llavero de inicio de sesión, que está bloqueado sin sesión iniciada (así falla cursor-agent en la Mini).
