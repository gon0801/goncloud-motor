# Hechos para obligar a usar pstack/poteto-mode desde el motor (2026-10-21)

Investigación de solo lectura. Las salidas completas de `--help` quedaron en `help/` junto a este archivo.
Rutas `~` = `/Users/dn` salvo que se diga otra máquina.

---

## 1. Cómo está instalado pstack hoy en la Mac

### 1.1 Fuente única: copia del original de Cursor (no el port)
- `~/bin/sincronizar-pstack.sh` (launchd `~/Library/LaunchAgents/ai.goncloud.sincronizar-pstack.plist`, 04:10 diario):
  - `preparar` baja `gh api repos/cursor/plugins/tarball/main`, copia `harness/pstack/claude-code` + 7 skills de `cursor-team-kit` a `~/.agents/pstack-oficial` (líneas 56-126). Cambia `disable-model-invocation: true` → `false` y `name` = nombre de carpeta (líneas 70-80). Falla si no hay `poteto-mode`, `agents/{poteto-agent,comment-sicko}.md`, `docs/harness/claude-code.md` o < 40 skills (107-118).
  - `activar` enlaza `~/.agents/skills/<skill>` → copia, `~/.claude/skills/<skill>` → `~/.agents/skills/<skill>`, y `~/.claude/agents/{poteto-agent,comment-sicko}.md` → copia (160-183). Deja la marca `~/.agents/.pstack-oficial-activo`.
- Estado: `~/.agents/pstack-oficial/ORIGEN` = `origen: cursor/plugins cursor-plugins-190d287 / fecha: 2026-10-21T11:10:12Z / skills: 63`. Log `~/Library/Logs/sincronizar-pstack.log`: `[2026-10-21 04:10:25] activada`.
- Comprobado: `~/.claude/skills/poteto-mode -> ../../.agents/skills/poteto-mode`, `~/.claude/agents/poteto-agent.md -> ~/.agents/pstack-oficial/agents/poteto-agent.md`. El frontmatter de `poteto-mode/SKILL.md` ya dice `disable-model-invocation: false`.

### 1.2 Claude Code
- El plugin port `pstack@pstack-claude` **ya no está instalado**: `~/.claude/plugins/installed_plugins.json` solo trae `superpowers`, `claude-mem`, `code-simplifier`, `codex`. `enabledPlugins` en `~/.claude/settings.json:146-151` tampoco lo nombra.
- Quedan restos: marketplace `pstack-claude` (`michael-denyer/pstack-claude`) en `known_marketplaces.json` y `settings.json:171-176` (`extraKnownMarketplaces`), y caché `~/.claude/plugins/cache/pstack-claude/pstack/{0.9.30,0.9.84}`. (Esta sesión de Claude todavía lista skills `pstack:*`; no averigüé de dónde salen.)
- Lo que hoy carga pstack en Claude Code: las skills de usuario enlazadas (1.1) y el hook `SessionStart` `~/bin/pstack-arranque.sh` (`settings.json:113-121`, matcher `startup|resume|clear|compact`). El hook imprime un `<EXTREMELY_IMPORTANT>` que pide invocar `poteto-mode` si la tarea toca más de un archivo, es de diseño o es un bug/perf de causa desconocida. Solo habla si existe la marca `~/.agents/.pstack-oficial-activo`.
- `~/bin/pstack-update.sh` es del esquema viejo (port: `claude plugin update pstack@pstack-claude` + `git pull` de `~/dev/pstack-claude`, que está en VERSION 0.9.30). Ya no aplica al esquema actual.
- `~/dev/upstream` no tiene nada de pstack (solo `hermes-jev-plugin` y `jev-skill-router`).

### 1.3 opencode (Mac)
- Las skills se cargan solas desde `~/.agents/skills` (lo dice `~/.agents/pstack-plataformas/opencode.md`, tabla Herramientas). Comprobado en la base de datos: hay llamadas `skill` con `poteto-mode` (sección 4).
- `~/.config/opencode/AGENTS.md:3` (global): antes de cualquier skill de pstack, leer completo `~/.agents/pstack-plataformas/opencode.md`.
- Agentes en `~/.config/opencode/agent/`: `poteto-agent.md` (glm-5.3), `poteto-agent-rapido.md` (glm-5.3-flash), `comment-sicko.md`, `panel-glm53/52/47/5turbo.md`. Todos `mode: subagent`. Ojo: `comment-sicko.md` apunta a `/Users/dn/dev/pstack-claude/plugins/pstack/agents/comment-sicko.md` (el port), no a la copia oficial.
- `opencode.jsonc`: modelo por omisión `zai-coding-plan/glm-5.3-flash`.

### 1.4 Muse (Mac)
- `muse skills list --source user --json` (en /tmp) da 123 skills; `poteto-mode` aparece con `"scope": "user"`, `"path": "$HOME/.agents/skills/poteto-mode/SKILL.md"`, `"activation": "on"`.
- Agentes en `~/.config/muse/agents/`: `poteto-agent.md`, `comment-sicko.md` (más adversary, implementer, reviewer, verifier).

### 1.5 Kimi, Grok y Qwen (Mac)
- **Kimi 2.1.1**: descubre `~/.agents/skills` solo. En la sesión del motor (`~/.kimi-code/sessions/wd_a_0e8bad0d5f66/session_f8771c9e-…/agents/main/wire.jsonl`) el listado de skills incluye `poteto-mode … Path: /Users/dn/.agents/skills/poteto-mode/SKILL.md`, y la herramienta `Skill` está declarada. `~/.kimi-code/AGENTS.md` solo trae las reglas de quality-kit. No existe `~/.kimi` (el directorio es `~/.kimi-code`).
- **Grok 1.0.62**: `~/.grok/skills/<skill> -> ../../.agents/skills/<skill>` (enlaces del 15 de octubre). `grok inspect` (en /tmp) lista `poteto-mode user`, y en Agents `poteto-agent user` y `comment-sicko user`. También toma `~/.claude/CLAUDE.md` como instrucciones y `~/.claude/settings.local.json` como permisos.
- **Qwen 0.25.0**: `~/.qwen/skills/<skill> -> ../../.agents/skills/<skill>` (54 enlaces, entre ellos `poteto-mode`).
- Ninguna de las tres tiene un mapa de plataforma en `~/.agents/pstack-plataformas/` (solo existen `opencode.md` y `muse.md`).

---

## 2. Qué dicen los mapas de plataforma

`~/.agents/pstack-plataformas/opencode.md` y `muse.md` tienen la misma estructura:
- "Una skill de pstack se ejecuta completa: no se lee y se aproxima" (línea 3 de cada uno).
- Traducen herramientas: skill → `skill` (opencode) / `muse.read_skill` (Muse); subagente → `task` / `muse.subagent_spawn` o `muse.workflow`; lista de tareas → `todowrite` / `muse.write_todos`.
- Agentes: opencode usa `poteto-agent`, `poteto-agent-rapido`, `comment-sicko` y los paneles `panel-glm*` (≥ 3 en paralelo, escribir "diversidad reducida: solo familia GLM"). Muse usa `poteto-agent` y `comment-sicko`, que heredan el modelo de la sesión; los paneles son 2 hijos Muse + 3 GLM por `opencode run -m zai-coding-plan/<m> --dir <repo> …` (muse.md:40-46).
- Solo suscripción `zai-coding-plan/*`. Prohibidos `opencode-go/*`, `opencode/*` y `zai/*`.
- Muse necesita un workspace de confianza; si dice "Agent delegation unavailable: workspace is untrusted", parar (muse.md:25).
- **"Qué exige poteto-mode completo"** (opencode.md y muse.md:56-62):
  1. cargar `poteto-mode` con la herramienta de skill, elegir playbook y copiar sus pasos textuales a la lista de tareas (`skip: <razón>` si no aplica);
  2. cargar y seguir completa cada skill que nombre el playbook;
  3. fan-out con subagentes reales del agente de la tabla;
  4. verificación con comandos reales;
  5. **la entrega dice qué skills cargó, qué subagentes corrieron con qué agente y modelo, y las salidas reales.**
- **Cómo prueban que se cargó:** solo por el punto 5, es decir, lo que el agente declara. Ningún mapa trae una comprobación mecánica.
- Lo mismo exigía la skill retirada `~/.claude/skills-retiradas/prompt-claw/SKILL.md:18-22` ("poteto-mode al 100% fuera de Claude… El LISTO trae la evidencia: skills cargadas, cada subagente con su agente y modelo, salidas reales") y `:48-50` (LISTO con SHA y evidencia).
- Choque comprobado: muse.md:42-44 usa `opencode run … --dir <repo>`, pero `opencode run --help` (v2.0.29) **no tiene `--dir`**. Sus flags son `--standalone --server --continue --session --fork --model --agent --format --file --title --thinking --auto`.

---

## 3. Qué ofrece cada CLI sin ventana (flags reales de `--help`)

| CLI (versión Mac) | Skills / plugins | Instrucciones de sistema | Agente | Salida |
|---|---|---|---|---|
| `claude` 2.1.302 | `--plugin-dir <path>` (repetible), `--plugin-url`, `--disable-slash-commands` (apaga todas las skills), `--safe-mode` y `--bare` (ver nota) | `--system-prompt`, `--append-system-prompt`; `--settings <file-or-json>` (hooks); `--setting-sources`; `--add-dir` | `--agent <agent>`, `--agents <json-or-file>` | `--output-format text\|json\|stream-json`, `--include-hook-events` y `--forward-subagent-text` (solo stream-json), `--json-schema`, `--session-id <uuid>` |
| `kimi` 2.1.1 | `--skills-dir <dir>` (repetible; **sustituye** el autodescubrimiento) | no hay flag de prompt de sistema | `--agent <name>`, `--agent-file <path>` | `--output-format text\|stream-json` |
| `opencode run` 2.0.29 | ninguno (las skills salen de la config/`~/.agents/skills`) | ninguno; usa `AGENTS.md` global y del proyecto | `--agent <string>` | `--format default\|json`, `--session`, `--title` |
| `grok` 1.0.62 | subcomando `grok plugin …` (install/enable/…); sin flag de skills | `--rules <RULES>` ("Extra rules to append to the system prompt"), `--system-prompt-override` | `--agent <NAME or file>`, `--agents <JSON>`, `--no-subagents` | `--output-format plain\|json\|streaming-json\|streaming-messages-json`, `--json-schema`, `--session-id` |
| `qwen` 0.25.0 | `-e/--extensions`, `--safe-mode` (apaga skills y extensiones), `--disabled-slash-commands` | `--system-prompt`, `--append-system-prompt` | (sin flag de agente) | `-o/--output-format text\|json\|stream-json`, `--json-file <path>` (eventos JSON a archivo), `--json-schema`, `--max-subagent-depth` |
| `muse exec` 1.4.11 | `--trust-workspace` ("Load this workspace's skills and rules"), `--no-foreign-personal-context`; subcomandos `muse skills list/inspect/enable/install/import --from claude\|codex`, `muse plugins` | `-c/--config KEY=VALUE`; reglas del workspace con `--trust-workspace` | `--agents <JSON>` (en `muse`, "one ephemeral agent-definition overlay") | `--json` (eventos JSONL), `--output-schema <FILE>`, `--session-id <UUID>`, `--no-session-log` |

Notas:
- Claude: `--bare` salta hooks, sincronización de plugins y CLAUDE.md, aunque "Skills still resolve via /skill-name"; el contexto se da con `--system-prompt[-file]`, `--append-system-prompt[-file]`, `--plugin-dir`, `--agents`, `--settings`. Las variantes `-file` solo aparecen en ese texto, no como opción propia en la lista.
- Claude `--agent poteto-agent` usaría `~/.claude/agents/poteto-agent.md`. Ese agente dice "Read the `poteto-mode` skill's SKILL.md in full before any work", pero sin una ruta dada busca en `~/.cursor/plugins/` (texto de Cursor en `~/.agents/pstack-oficial/agents/poteto-agent.md`). Sin ruta explícita en el encargo, falla.
- El adaptador `~/.agents/pstack-oficial/docs/harness/claude-code.md:12` dice "Run another pstack skill → `Read <pstack>/skills/<name>/SKILL.md`… The `Skill` tool refuses most pstack skills". La sincronización pone `disable-model-invocation: false`, así que en Claude se puede cargar con `Skill` **o** con `Read` del SKILL.md. Cualquier comprobación debe aceptar las dos formas.
- Rutas actuales (`~/motor-conf/rutas.toml`): sonnet/opus/deepseek `claude -p … --output-format json "{prompt}"`; k3 `kimi -p "{prompt}" --output-format stream-json`; glm `opencode run --auto -m zai-coding-plan/glm-5.3 "{prompt}"` (sin `--format json` ni `--agent`); grok `grok --prompt-file {brief} --always-approve --output-format json`; muse `muse exec --yolo --json --prompt-file {brief}`; qwen `qwen --approval-mode yolo --output-format json "{prompt}"`. Ninguna ruta pasa hoy un flag de pstack.

---

## 4. Cómo se comprueba después que usó poteto-mode

Todo lo que el agente imprime en stdout/stderr queda en `<intento>/log.txt` (`motor/runwrap.py:58-77`, se abre en modo append). `outcome.json` guarda solo los últimos 2000 caracteres (`runwrap.py:104`).

| CLI | Qué deja | ¿Se puede comprobar? |
|---|---|---|
| **Claude `-p --output-format json`** (ruta actual) | Un objeto final. Claves medidas en `~/.motor/intentos/pstack-aviso-claw/A-implementar-1/log.txt`: `session_id, num_turns, subagent_stats{spawned, by_type…}, modelUsage, result, permission_denials, terminal_reason…`. **No lista herramientas ni skills.** | Solo `subagent_stats.by_type` (subagentes por tipo). Para las skills hace falta el transcript. |
| Claude: transcript | `~/.claude/projects/<cwd-con-guiones>/<session_id>.jsonl`. Ejemplo: `-Users-dn--motor-wt-pstack-aviso-claw-A/2c9ca226-….jsonl`. Trae los `tool_use` (`name`, `input`), los adjuntos de hooks y el `skill_listing`. | **Sí.** En ese intento Sonnet usó `Bash` ×5 y `Read` ×1, **sin `Skill` ni `Agent`**, aunque el hook SessionStart sí inyectó el aviso de pstack (`hook_success … content '<EXTREMELY_IMPORTANT>…'`). Con `--session-id <uuid>` el motor conoce la ruta de antemano. Con `--output-format stream-json` los `tool_use` saldrían directo en log.txt (no lo probé). |
| **Kimi `--output-format stream-json`** (ruta actual) | log.txt con un JSON por línea: `{"role":"assistant","tool_calls":[{"function":{"name":…,"arguments":…}}]}`. Intento A-implementar-4: Bash 36, Read 4, Grep 4, Edit 1, Write 1; **ningún `Skill`**. Copia completa en `~/.kimi-code/sessions/<wd>/<session>/agents/main/wire.jsonl`. | **Sí**: buscar `name == "Skill"` con `arguments.skill == "poteto-mode"` y `Agent`/`AgentSwarm`. |
| **opencode** | `~/.local/share/opencode/opencode.db` (SQLite, 4.1 GB). En v2: tabla `session_v2` (`directory`) + `session_message.data` con `{"type":"tool","name":"skill","state":{"input":{"name":"…"}}}`. En la tabla vieja `part`: 438 llamadas `skill` a `poteto-mode` y `task` con `subagent_type` `poteto-agent` (217), `panel-glm*`, etc. | **Sí**, con lectura de la base. Las sesiones del motor (`session_v2.directory LIKE '%motor%'`) tienen **0** mensajes con poteto-mode, y su agente fue `build`. `--format json` existe y daría los eventos en log.txt (no lo probé). Hoy el log de texto solo muestra `→ Read …` y `$ cmd`. |
| **Grok `--output-format json`** (ruta actual) | Un objeto: `text, thought, sessionId, cwd, num_turns, usage, modelUsage, total_cost_usd`. **Sin herramientas.** | Por sesión: `~/.grok/sessions/<cwd-codificado>/<sessionId>/updates.jsonl` (`"tool_name":"read_file"` …), `chat_history.jsonl`, `tool_definitions.json`. Grok **no tiene herramienta de skill**: sus herramientas son `run_terminal_command, read_file, search_replace, spawn_subagent, todo_write, use_tool, workflow…`, y las skills le llegan como lista con "Absolute path". "Cargar poteto-mode" = un `read_file` de `…/poteto-mode/SKILL.md`, comprobable en `updates.jsonl`. Subagentes: `spawn_subagent`. |
| **Muse** | `~/.local/share/muse/sessions/YYYY/MM/DD/<id>/session.jsonl`, evento `assistant_tool_calls_committed` con `{"name":"read_skill","args":"{\"name\":\"…\"}"}`. `muse trace inspect --session-log <jsonl> --format json` lo proyecta. `muse exec --json` emite JSONL por stdout (no medí su contenido). | **Sí**, por el session.jsonl, salvo que se corra con `--no-session-log`. |
| **Qwen** | `~/.qwen/projects/<cwd>/chats/<id>.jsonl` registra las llamadas (`run_shell_command`, `read_file` vistas). `--json-file` puede mandar eventos a un archivo. | Probable; no vi ninguna llamada a una skill, así que no conozco el nombre de esa herramienta. |

**Comprobable de forma mecánica:** que hubo una llamada de herramienta que cargó `poteto-mode` (Skill/skill/read_skill o lectura del SKILL.md), cuántos subagentes y de qué tipo, y que hubo comandos reales (Bash) con su salida.

**Solo la palabra del agente:** que eligió bien el playbook, que copió los pasos, que siguió la skill "completa" y no solo la leyó, y que los resultados de los subagentes los revisó él. El campo `resumen` del result.json es palabra del agente. El brief mismo ya lo dice: "Lo que digas no cuenta como prueba" (`effects.py:308`).

---

## 5. Qué hay en la Mini y en la PC (lectura por ssh)

### Mac Mini (`gon@100.73.187.5`)
- Versiones: claude 2.1.304, kimi 2.2.0 (`~/.openclaw/tools/node/bin`), opencode v2.0.32, grok 1.0.62, qwen 0.25.3, muse 1.4.11.
- `~/.claude/settings.json` = solo `{"theme":"dark","tui":"fullscreen"}`: sin hooks ni plugins. `~/.claude/plugins/known_marketplaces.json` solo tiene `anthropics/claude-plugins-official`. No hay `plugins/cache` ni `~/.claude/agents`. `~/.claude/skills` solo tiene `synced/` (skills de cuenta: docs, docx, deep-research…).
- **No hay `~/.agents`**, ni `~/.grok/skills`, ni `~/.config/opencode/agent` o `AGENTS.md`. `opencode.jsonc` existe (19-oct).
- `grok inspect`: 31 skills y 3 agentes, sin poteto. `muse skills list --json`: 24 skills, scopes `bundled`/`plugin`, **0 poteto**.
- `~/.motor/conf -> ~/motor-conf` (HEAD `8f6a7d7`, limpio).

### PC Windows (`gwpc`, ehven)
- Versiones: claude 2.1.303, kimi 2.2.0, opencode v2.0.32 (npm `.ps1`), grok 1.0.62, qwen 0.25.2 (npm), muse.exe 1.4.10 en Windows. En WSL (usuario ehven): `/home/ehven/.local/bin/muse` 1.4.11.
- `.claude/plugins`: marketplaces superpowers, claude-mem, claude-plugins-official, claude-code-harness y codex. Instalados: claude-mem, codex, superpowers, harness@claude-code-harness-marketplace. **Sin pstack.** `settings.json` tiene `SessionStart` y `enabledPlugins`, pero nada de pstack/poteto.
- `.claude/skills`: 29 skills (saikit, loops…), **sin poteto**. `.claude/agents`: adversary, closer, implementer, loop-verifier, retro, reviewer, verifier.
- **No hay `%USERPROFILE%\.agents`** ni `.config\opencode\agent`. `.grok\skills` = solo `loop-architect`. `.kimi-code\skills` = `docs-groom`, `loop-architect`.
- WSL: no hay `~/.agents` ni `~/.claude/skills`. `muse skills list --json | grep -c poteto` = **0**.
- `.motor\conf` es un checkout git de motor-conf.

---

## 6. El motor (`~/dev/goncloud-motor`, rama `main`, HEAD `36c891b`)

### Dónde se arma el encargo
- `motor/conf.py:119-130`: `reglas` = `<conf>/reglas/encargo.md`, `reglas_revisor` = `<conf>/reglas/revisor.md` (de la conf fijada al tomar el plan). Hoy `encargo.md` tiene 2 líneas y `revisor.md` 3. **Ninguna menciona pstack ni poteto.**
- `motor/effects.py:173-270` `start_attempt`:
  - 192-206: elige `_brief_implementar` o `_brief_revisar` y escribe `<intento>/brief.md` (207).
  - 209: el prompt que se pasa como `{prompt}` es `"Lee <dir>/brief.md y ejecútalo completo sin preguntar nada."`.
  - 210-217: arma `argv` de la plantilla de `rutas.toml` (`{brief}`, `{prompt}`, `{result}`). Si `cli in maquina.wsl` (Muse en la PC), lo envuelve con `argv_en_wsl`.
  - 220-234: variables de entorno `MOTOR_RESULT, MOTOR_MODEL, MOTOR_BLOCK, MOTOR_ROLE`; `WSLENV` pasa `META_API_KEY` y `MOTOR_RESULT`; `PATH` empieza con `motor/shims` (el `gh` que no deja mergear).
  - 235-251: lanza `python -m motor.runwrap --dir --cwd --timeout --sin-avance [--wip-*] -- argv`. runwrap agrega `MOTOR_ATTEMPT_DIR` (`runwrap.py:62`).
- `effects.py:272-309` `_brief_implementar`: reglas + "Qué entregar" (filas con `Acepta`) + feedback + pendientes + "Cómo se entrega" (commit por fila, push a la rama, prohibido mergear, JSON en `$MOTOR_RESULT`).
- `effects.py:311-328` `_brief_revisar`: reglas del revisor + filas + formato del veredicto.
- Antecedente: `docs/arena/motor/c2-design.md:129` dice que al portar desde Hermes se retiró `pre_invoke` ("autorización poteto").

### Qué lee del resultado (`effects.py:337-428`, `collect`)
- Primero `outcome.json` de runwrap (`exit, timed_out, no_progress, tail, fotos, segundos`) → `machine.classify` (`machine.py:882-893`): tiempo / sin avance / falla del proveedor / `terminado`.
- Luego `result.json` (`$MOTOR_RESULT`):
  - **revisar**: `sha` (debe ser igual al revisado), `veredicto` ∈ {APROBADO, CAMBIOS}, `bloqueantes[{texto, repro, arreglo}]`, `notas`.
  - **implementar**: `sha` (debe ser la punta de la rama remota, `ws.punta`) y `resumen` (se recorta a 500).

### Dónde se puede rechazar una entrega
1. `effects.py:381-387`: no hay result.json o no se puede leer → `sin_resultado`.
2. `effects.py:389-397` (revisor): SHA o veredicto inválido → `veredicto_invalido`.
3. `effects.py:412-418` (implementador): el SHA no es la punta de la rama → `mintio`.
4. `effects.py:419-423`: entregó lo mismo que ya había fallado → `sin_cambios`.
5. Ahí (antes de `return {"kind":"entregado"…}` en 424, y antes de 398 para el revisor) se podría insertar otra comprobación, con `<intento>/log.txt`, el `session_id` o el transcript a la mano (`d = Path(att.dir)`).
6. Después de entregar: `effects.verify` (`effects.py:431-452`) corre cada `Acepta` en una copia limpia. Otro punto para un rechazo mecánico.

### Qué pasa con un "kind" nuevo
- Lo guarda `daemon.ingest_attempts` (`daemon.py:259-275`) como `AttemptEnded`.
- Cualquier kind distinto de `entregado` deja el bloque en `ARREGLAR` (`machine.py:288`). El siguiente brief recibe "El intento anterior terminó por: <kind> (<detalle>)" (`machine._feedback`, `machine.py:434-443`).
- Cuenta como falla del modelo si no está en `NO_ES_FALLA = ("entregado","veredicto","corte")` (`machine.py:97`, conteo en 588-598) → relevo al llegar a `fallas_seguidas_max`. `relevo.DE_KIND` (`relevo.py:85-97`) no conoce `mintio` ni `sin_cambios`; un kind nuevo tampoco estaría ahí: `desde_kind` → `None`, cuenta como falla normal pero no "avanza" por regla (`_avanzo_de`).

### Observaciones de los intentos reales (`~/.motor/intentos/pstack-aviso-claw/`)
- Modelos: sonnet ×3, k3 ×1 (implementar) y opus ×1 (revisar) (`~/.motor/motor.db`, eventos `AttemptStarted`).
- Ni Sonnet ni Kimi cargaron poteto-mode (sección 4), aunque en la Mac lo tenían disponible y el hook lo pidió.
- `A-implementar-1/log.txt` trae 13 objetos JSON de `claude -p` con `session_id` distintos (el log es append). Un verificador debe decidir qué sesiones cuentan.
