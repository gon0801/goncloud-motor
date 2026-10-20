# Motor de planes en las tres máquinas
autopiloto: motor
plan: motor-01
repo: git@github.com:gon0801/goncloud-motor.git

<!-- Diseño y decisiones: docs/diseno.md. Revisado por Grok el 2026-10-20 (a pedido de David).
Antes de entregarlo, el lead: (1) crea privados gon0801/goncloud-motor (con este prototipo) y
gon0801/goncloud-motor-conf (con conf-ejemplo/). GitHub gratuito no protege ramas privadas: los checks
que importan los exige el motor (`checks:` y `.motor.toml`), no la protección; (2) arranca el prototipo con
`--home ~/.motor-arranque`, que NUNCA es el ~/.motor que instala F. Todo lo que este plan necesita del
motor para verificarse ya está en el prototipo (conf valida, checks en matriz, dependencias antes de
pasos humanos). Lo que B y C agregan llega a las máquinas con la instalación de F, no al prototipo.
Ningún bloque reinicia la Mac. -->

## Bloque B — el motor habla con GitHub de verdad y aprende del router de Hermes
lane: gate
depende: -
aviso: el motor ya abre, revisa y mergea PRs en GitHub de verdad
prueba-real: MOTOR_GH_SANDBOX=gon0801/motor-sandbox python3 -m unittest tests.test_github_contrato.ContratoReal
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Crear el repo privado gon0801/motor-sandbox con un workflow "ci" que corre `true` (GitHub gratuito no protege ramas privadas: el motor exige "ci" como check extra). Correr `ContratoReal` y arreglar `GhGitHub` hasta que pase: borrador, listo, checks requeridos de la protección, merge con `--match-head-commit`, rechazo si la punta se movió. | MOTOR_GH_SANDBOX=gon0801/motor-sandbox python3 -m unittest tests.test_github_contrato.ContratoReal |
| B.2 | Portar a `motor/relevo.py` las reglas puras de `_run_owned` de `plugins/goncloud-team/subscriptions/router.py` (goncloud-hermes, `origin/feat/hermes-team`): avance por tipo de falla, horizonte de cuarentena, `retry_after`, rate limit corto (2 min). Sin arriendos ni estado. Portar al menos 40 pruebas a `tests/test_relevo.py` y usar `relevo.py` en `machine._elige`. | python3 -c "import sys,unittest;s=unittest.defaultTestLoader.loadTestsFromName('tests.test_relevo');sys.exit(0 if s.countTestCases()>=40 and unittest.TextTestRunner().run(s).wasSuccessful() else 1)" |
| B.3 | Clasificación de fallas por proveedor (claude, kimi, opencode, grok, muse, qwen, deepseek) tomada de los adapters de Hermes, con una salida real por caso en `tests/datos/fallas/<proveedor>-<caso>.txt` y la prueba `tests/test_fallas.py` que recorre esa carpeta. | python3 -m unittest tests.test_fallas |
| B.4 | Clave `tiene = ["ssh-goncloud", ...]` en `maquina.toml` (esquema cerrado). `motor entrega` rechaza un plan cuyo repo pide en `[deploy] requiere` algo que la máquina no tiene, con el motivo en una frase llana. Prueba `tests/test_requiere.py`. | python3 -m unittest tests.test_requiere |
| B.5 | `motor doctor --como-servicio`: corre cada ruta con un encargo de prueba de 1 minuto en el entorno del servicio (stdin nulo, env de `maquina.toml` + `secretos.env`, PATH = `path_extra` + PATH del servicio) y dice cuáles responden. Prueba `tests/test_doctor.py` con una ruta falsa que responde y otra que no. | python3 -m unittest tests.test_doctor |
| B.6 | `motor version` (versión del paquete y SHA) y salida 75 del daemon cuando la conf fija otra versión (el supervisor lo relanza). Prueba `tests/test_version.py`. | python3 -m unittest tests.test_version |

## Bloque C — el motor en Windows y Muse en WSL
lane: gate
depende: B
aviso: el motor ya funciona en la PC, con Muse dentro de Linux
prueba-real: bash ops/prueba-pc.sh "$(git rev-parse HEAD)"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Portar de goncloud-hermes `plugins/goncloud-team/subscriptions/process.py` el Job object con kill-on-close, la terminación del árbol y el guardián de WSL a `motor/plataforma/windows.py`; quitar las marcas "sin probar". | bash ops/prueba-pc.sh "$(git rev-parse HEAD)" |
| C.2 | `tests/test_windows.py`, solo en Windows: un proceso lanzado separado sobrevive al cierre del daemon, `estado_proc`, `boot_id`, shims `.cmd`, `wsl.exe -e` con `WSLENV`. En la Mac se salta con el motivo. | bash ops/prueba-pc.sh "$(git rev-parse HEAD)" |
| C.3 | `ops/prueba-pc.sh <sha>`: manda ese SHA a la PC con `git archive <sha> \| ssh gwpc "tar -x -C %TEMP%\motor-prueba-<sha>"` y corre allí `uv run --python 3.12 python -m unittest -v tests.test_puro tests.test_windows` (en la PC no hay `py` ni `python` en el PATH de ssh; `uv` sí, en `C:\Users\ehven\.local\bin`). Sale ≠0 si algo falla, si ssh falla o si `test_windows` se saltó. Solo escribe en esa carpeta temporal y la borra al final. | bash ops/prueba-pc.sh "$(git rev-parse HEAD)" |

## Bloque A — la conf compartida
lane: gate
depende: B, C
repo: git@github.com:gon0801/goncloud-motor-conf.git
aviso: las tres máquinas ya comparten la misma tabla de modelos y las mismas reglas
prueba-real: PYTHONPATH="$MOTOR_RAIZ" python3 -m motor conf valida .
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | `modelos.toml` generado desde `model-routing.source.json` de goncloud-hermes (`origin/feat/hermes-team`; carriles "codigo" y "planear") con `scripts/genera_modelos.py`, que también escribe la copia para Hermes y para OpenClaw. `--comprueba` sale ≠0 si algo no coincide. | python3 scripts/genera_modelos.py --comprueba |
| A.2 | `rutas.toml` con la ruta sin ventana de cada CLI (sonnet, opus, k3, glm, grok, muse, qwen, deepseek), cada una con `{prompt}` o `{brief}`. DeepSeek por el CLI de Claude con `env` y `${DEEPSEEK_API_KEY}`; ninguna llave en el repo. | PYTHONPATH="$MOTOR_RAIZ" python3 -m motor conf valida . |
| A.3 | `maquinas/mac.toml`, `maquinas/mini.toml`, `maquinas/pc.toml`: `nombre`, `conf` (ruta del clone de esta conf en esa máquina), `path_extra` medido en esa máquina por ssh (Mac: incluye `~/.opencode/bin` y `~/.local/bin`; PC: `C:\Users\ehven\.local\bin`), `env` (PC: certificados de Kaspersky, leídos de la conf de Hermes), `wsl = ["muse"]` en la PC, y en la Mac `[no_dormir] encender = ["sudo","-n","pmset","-a","disablesleep","1"]` y `apagar` con `"0"`. | PYTHONPATH="$MOTOR_RAIZ" python3 -m motor conf valida . |
| A.4 | `general.toml [avisos] chat` = el chat de Telegram de David, leído por ssh (solo lectura) del cron de OpenClaw en la Mini que ya le entrega; el token de cada bot va en `secretos.env`, no aquí. | python3 -c "import tomllib,sys;sys.exit(0 if tomllib.load(open('general.toml','rb'))['avisos']['chat'].strip() else 1)" |

## Bloque F — instalar en la Mac, la Mini y la PC
lane: release
depende: A, B, C
aviso: el motor ya está copiado en las tres máquinas; falta que pegues las llaves
prueba-real: bash ops/huellas.sh
despliegue: desplegar=ops/instala-todo.sh checklist=ops/huellas.sh rollback=ops/desinstala-todo.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| F.1 | `ops/instala-todo.sh`: crea y empuja la etiqueta `v1.0.0` sobre el SHA mergeado (si no existe) y, por ssh en cada máquina (Mac local, Mini `gon@100.73.187.5`, PC `gwpc`), clona o actualiza goncloud-motor en `~/motor` en esa etiqueta y goncloud-motor-conf en `~/motor-conf`, copia `maquinas/<m>.toml` a `~/.motor/maquina.toml` y escribe `~/.motor/instalar-servicio.txt` con el comando de `motor instala --imprime` y las líneas de sudoers (Mac: `pmset`; Mini: `shutdown`). No pide sudo. Nunca toca `~/dev/goncloud-motor` ni `~/.motor-arranque`. | bash ops/tests/test-instala.sh |
| F.2 | `ops/huellas.sh [--servicio] [--rutas]`: en las tres, `motor version`, sha de la conf y `motor doctor --json`; sale 0 solo si las tres huellas coinciden y conf y máquina están ok; con `--servicio` exige el servicio cargado; con `--rutas`, `doctor --como-servicio` con al menos un modelo por papel. | bash ops/tests/test-huellas.sh |
| F.3 | `ops/desinstala-todo.sh`: descarga el servicio y deja carpetas y registros. | bash ops/tests/test-instala.sh |
| F.4 | `ops/tests/test-instala.sh` y `ops/tests/test-huellas.sh`: corren los scripts con un `ssh` falso en el PATH y exigen salida ≠0 cuando el remoto falla, cuando una huella no coincide y cuando `doctor` no está ok, y 0 cuando todo está bien. | bash ops/tests/test-huellas.sh |

## Bloque E — llaves y servicio en cada máquina
lane: fast
depende: F
humano: login En la Mac, la Mini y la PC: corre `claude setup-token`; llena `~/.motor/secretos.env` con el token de Claude, de Cursor, de Muse, de DeepSeek, de GitHub y del bot de esa marca, y luego `chmod 600 ~/.motor/secretos.env`; corre una vez, con tu contraseña, lo que dice `~/.motor/instalar-servicio.txt`
sonda-humano: bash ~/motor/ops/huellas.sh --servicio --rutas

## Bloque D — Orbit listo para el motor
lane: gate
depende: -
repo: git@github.com:gon0801/goncloud-Orbit.git
aviso: Orbit ya puede recibir planes del motor
prueba-real: python3 -c "import tomllib;c=tomllib.load(open('.motor.toml','rb'));assert c['repo']['principal']=='master' and c['github']['checks_extra']==['completa'] and c['deploy']['requiere']==['ssh-goncloud']"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | `.motor.toml` en la raíz, exactamente con `[repo] principal = "master"`, `[github] checks_extra = ["completa"]` (el motor cuenta las seis `completa (i, 6)`), `[deploy] requiere = ["ssh-goncloud"]` y `[pruebas.env]` con lo que necesita la batería. Sin receta de despliegue por omisión: cada plan de Orbit trae la suya. | python3 -c "import tomllib;c=tomllib.load(open('.motor.toml','rb'));assert c['repo']['principal']=='master' and c['github']['checks_extra']==['completa'] and c['deploy']['requiere']==['ssh-goncloud']" |
| D.2 | En `CLAUDE.md`, una línea: los planes de Orbit se corren con el motor (`autopiloto: motor`). | grep -q "autopiloto: motor" CLAUDE.md |

## Bloque G — Hermes usa el motor y se retira su cadena fija
lane: release
depende: A, E
repo: git@github.com:gon0801/goncloud-hermes.git
checks: espejo-de-g
aviso: Hermes ya reparte las tareas chicas y manda al motor todo lo que cambia código
prueba-real: bash ops/motor-g/checklist.sh
despliegue: desplegar=ops/motor-g/desplegar.sh checklist=ops/motor-g/checklist.sh rollback=ops/motor-g/rollback.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| G.1 | Skill `motor` (copia de `skill/motor/SKILL.md` de goncloud-motor) en `skills/motor/SKILL.md` y registrada en los perfiles de Hermes que reciben pedidos de cambio (comprobar por ssh, solo lectura, desde dónde carga las skills el perfil de Hermi). | test -f skills/motor/SKILL.md |
| G.2 | Apagar `plugins/goncloud-team/local_orchestrator.py` (la cadena de 7 pasos), `plugins/goncloud-team/raices/` y `plugins/goncloud-team/avisos_sesion.py`; actualizar o quitar las pruebas que los importan. Hermes reparte la lectura a sus agentes como OpenClaw. | python3 -m unittest discover -s tests -p "test_sistema_agentes_*.py" && ! git grep -q -e "local_orchestrator" -e "avisos_sesion" -- plugins tests |
| G.3 | `model-routing.source.json` generado por `scripts/genera_modelos.py` de goncloud-motor-conf (sin edición a mano). | bash ops/motor-g/comprueba-tabla.sh |
| G.4 | Comentar en el PR #37 (Raíces, ya cerrado) que lo reemplaza el motor, citando `motor-01`. | bash ops/motor-g/pr37.sh |
| G.5 | `ops/motor-g/desplegar.sh`: actualiza el checkout de Hermes en la PC, borra el cron `despertar-raices` (`hermes -p default cron ...`) y reinicia el gateway. `checklist.sh` sale 0 solo si Hermi responde por ssh, la cadena ya no está registrada y no existe el cron (≠0 si ssh falla). `rollback.sh` vuelve al SHA anterior. Prueba `ops/motor-g/test-checklist.sh` con `ssh` falso. | bash ops/motor-g/test-checklist.sh |

## Bloque H — OpenClaw usa el motor y se retiran los loops
lane: release
depende: A, E
repo: git@github.com:gon0801/goncloud-openclaw.git
checks: gate
aviso: OpenClaw ya reparte las tareas chicas y manda al motor todo lo que cambia código
prueba-real: bash ops/motor-h/checklist.sh
despliegue: desplegar=ops/motor-h/desplegar.sh checklist=ops/motor-h/checklist.sh rollback=ops/motor-h/rollback.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| H.1 | Skill `motor` en `agents/main/agent/workshop-skills/motor/SKILL.md` y en los demás agentes que reciben pedidos de cambio. | test -f agents/main/agent/workshop-skills/motor/SKILL.md |
| H.2 | `workspace-implementer/AGENTS.md`: sin `write`/`edit` en repos y sin la ruta de `merge.py`; lo que cambia código va por `motor entrega`. Lo mismo en el `AGENTS.md` de `ingenieria` donde viva. | grep -q "motor entrega" workspace-implementer/AGENTS.md && ! grep -q "merge.py" workspace-implementer/AGENTS.md |
| H.3 | Borrar `scripts/agent-work/merge.py` y `scripts/tests/test-agent-work-merge.py`. | test ! -e scripts/agent-work/merge.py -a ! -e scripts/tests/test-agent-work-merge.py |
| H.4 | La tabla de modelos de OpenClaw (`docs/model-routing.md`) generada desde goncloud-motor-conf. | bash ops/motor-h/comprueba-tabla.sh |
| H.5 | `ops/motor-h/desplegar.sh`: actualiza el checkout en la Mini, quita de `openclaw.json` las herramientas de escritura de `implementer` e `ingenieria`, borra los crons `*-vigia` del gateway, reinicia el gateway y en la Mac mueve `~/.claude/skills/prompt-claw` a `~/.claude/skills-retiradas/`. `checklist.sh` sale 0 solo si claw responde, tiene la skill `motor`, no quedan crons `*-vigia` ni `prompt-claw` (≠0 si ssh falla). Prueba `ops/motor-h/test-checklist.sh` con `ssh` falso. | bash ops/motor-h/test-checklist.sh |

## Bloque I — prueba de fuego con reinicio de la Mini y la PC
lane: release
depende: E, G, H
aviso: el motor pasó la prueba de fuego: terminó dos planes aunque se reiniciaron las máquinas a la mitad
prueba-real: bash ops/canario/verifica.sh
despliegue: desplegar=ops/canario/corre.sh checklist=ops/canario/verifica.sh rollback=ops/canario/limpia.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| I.1 | Repo privado gon0801/motor-canario y `ops/canario/plan.md`: 3 bloques (código con `Acepta` y `prueba-real`, un segundo repo, un despliegue a una carpeta) y un bloque `humano: login` con `sonda-humano` automática. Debe pasar `motor valida`. | python3 -m motor valida ops/canario/plan.md |
| I.2 | `ops/canario/corre.sh`: le entrega el plan a claw (Mini) y a Hermi (PC) por su canal normal; cuando cada motor lleva un bloque a medias, reinicia esa máquina (`sudo -n shutdown -r now` en la Mini, `shutdown /r /t 0` en la PC), solo después de ver su servicio arriba. Nunca reinicia la Mac. | bash ops/canario/test-corre.sh |
| I.3 | `ops/canario/verifica.sh`: espera con tope de 3 h; sale 0 solo si en cada máquina el plan terminó, hay un PR y un merge por bloque, un corte anotado como corte (no como falla) y llegaron los avisos por el bot de cada marca. Prueba `ops/canario/test-verifica.sh` con estados falsos. | bash ops/canario/test-verifica.sh |
