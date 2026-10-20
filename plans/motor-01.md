# Motor de planes en las tres máquinas
autopiloto: motor
plan: motor-01
repo: https://github.com/gon0801/goncloud-motor.git

<!-- Diseño y decisiones: docs/diseno.md. Lo corre el prototipo en la Mac. Nadie usa el motor para
trabajo real hasta que pase el bloque I. Repos nuevos que crea el plan, todos privados:
goncloud-motor-conf y motor-sandbox (contrato de GitHub) y motor-canario (bloque I). -->

## Bloque B — el motor habla con GitHub de verdad y aprende del router de Hermes
lane: gate
depende: -
aviso: el motor ya abre, revisa y mergea PRs en GitHub de verdad
prueba-real: MOTOR_GH_SANDBOX=gon0801/motor-sandbox python3 -m unittest tests.test_github_contrato
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Crear el repo privado gon0801/motor-sandbox con rama main protegida y un check requerido "ci" (workflow que corre `true`). Correr el contrato real (`ContratoReal`) y arreglar `GhGitHub` hasta que pase: borrador, listo, checks requeridos de la protección, merge con `--match-head-commit`, rechazo si la punta se movió. | MOTOR_GH_SANDBOX=gon0801/motor-sandbox python3 -m unittest tests.test_github_contrato |
| B.2 | Portar a `motor/relevo.py` las reglas puras de `router._run_owned` de goncloud-hermes (`plugins/goncloud-team/subscriptions/router.py` en `origin/feat/hermes-team`): avance por tipo de falla, horizonte de cuarentena, `retry_after`, rate limit corto (2 min, no 5 h). Sin arriendos ni estado. Portar las pruebas que apliquen (≥40) a `tests/test_relevo.py`. | python3 -m unittest tests.test_relevo |
| B.3 | Clasificación de fallas por proveedor (claude, kimi, opencode, grok, muse, cursor) tomada de los adapters de Hermes, con un ejemplo real de salida por caso en `tests/datos/fallas/`. | python3 -m unittest tests.test_puro |
| B.4 | `motor entrega` rechaza un plan cuyo repo pide en `requiere` algo que la máquina no tiene (p. ej. ssh a goncloud), con el motivo en lenguaje llano. | python3 -m unittest tests.test_motor_e2e |
| B.5 | Verbo `motor conf valida <carpeta>`: papeles, carriles y rutas coherentes; cada papel con un modelo; plantillas que pasan `lint_plain`. Sale ≠0 con la lista de errores. | python3 -m motor conf valida conf-ejemplo |
| B.6 | `motor doctor --como-servicio`: corre cada ruta con un encargo de prueba de 1 minuto en el mismo entorno que el servicio (sin sesión, stdin nulo, env de `maquina.toml` + `secretos.env`) y dice cuáles responden. | python3 -m unittest tests.test_puro |
| B.7 | `motor version` y etiqueta `v1.0.0`. El servicio se actualiza solo a la etiqueta que fija la conf (salida 75 = reiniciar). | python3 -m motor version |

## Bloque C — el motor en Windows y Muse en WSL
lane: gate
depende: B
aviso: el motor ya funciona en la PC, con Muse dentro de Linux
prueba-real: ssh gwpc "cd %TEMP%\motor-prueba && py -m unittest tests.test_puro tests.test_windows"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Portar de goncloud-hermes `subscriptions/process.py` el Job object con kill-on-close, la terminación del árbol y el guardián de WSL a `motor/plataforma/windows.py`; quitar las marcas "sin probar". | python3 -m unittest tests.test_puro |
| C.2 | `tests/test_windows.py` que corre solo en Windows: lanzar separado un proceso que sobrevive al cierre del daemon, `estado_proc`, `boot_id`, shims `.cmd`, `wsl.exe -e` con `WSLENV`. En la Mac se salta con el motivo. | python3 -m unittest tests.test_windows |
| C.3 | Script `ops/prueba-pc.sh`: clona la rama en `%TEMP%\motor-prueba` de la PC por ssh y corre C.2 allí. Solo escribe en esa carpeta temporal. | bash -n ops/prueba-pc.sh |

## Bloque A — la conf compartida
lane: gate
depende: B
repo: https://github.com/gon0801/goncloud-motor-conf.git
aviso: las tres máquinas ya comparten la misma tabla de modelos y las mismas reglas
prueba-real: python3 -m motor conf valida .
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | Crear el repo privado gon0801/goncloud-motor-conf (rama main protegida, check requerido "conf"). Contenido inicial = `conf-ejemplo/` de goncloud-motor. | test -f papeles.toml -a -f modelos.toml -a -f rutas.toml -a -f general.toml |
| A.2 | `modelos.toml` generado desde `model-routing.source.json` v6 de goncloud-hermes (`origin/feat/hermes-team`) con `scripts/genera_modelos.py`; la tabla de Hermes y la de OpenClaw salen de aquí desde ahora. | python3 scripts/genera_modelos.py --comprueba |
| A.3 | `rutas.toml` con la ruta sin ventana de cada CLI, medida en su máquina con `motor doctor --como-servicio` (Mac, Mini y PC). Muse con `wsl = ["muse"]` en la PC. | python3 -m motor conf valida . |
| A.4 | `maquinas/mac.toml`, `maquinas/mini.toml`, `maquinas/pc.toml` (sin secretos): nombre, path_extra, env (certificados de Kaspersky en la PC), wsl y, en la Mac, `[no_dormir]` con `sudo -n pmset -a disablesleep 1/0`. `[avisos] chat` = el chat de David. | python3 -m motor conf valida . |
| A.5 | Workflow "conf" que instala goncloud-motor en la etiqueta fijada y corre `motor conf valida .`. | test -f .github/workflows/conf.yml |

## Bloque F — instalar en la Mac, la Mini y la PC
lane: release
depende: A, B, C
aviso: el motor ya está instalado y encendido en las tres máquinas
prueba-real: bash ops/huellas.sh
despliegue: desplegar=ops/instala-todo.sh checklist=ops/huellas.sh rollback=ops/desinstala-todo.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| F.1 | `ops/instala-todo.sh`: por ssh en cada máquina (Mac local, Mini `gon@100.73.187.5`, PC `gwpc`) clona o actualiza goncloud-motor y goncloud-motor-conf en la etiqueta fijada, copia `maquinas/<m>.toml` a `~/.motor/maquina.toml` y deja en `~/.motor/instalar-servicio.txt` el comando de servicio de `motor instala --imprime` más las líneas de sudoers que hacen falta (`pmset` en la Mac, `shutdown` en la Mini). No pide sudo. | bash -n ops/instala-todo.sh |
| F.2 | `ops/huellas.sh`: en las tres, `motor version`, sha de la conf y `motor doctor --json`; sale 0 solo si las tres huellas coinciden y conf y máquina están ok. | bash -n ops/huellas.sh |
| F.3 | `ops/desinstala-todo.sh`: descarga el servicio y deja las carpetas (no borra registros). | bash -n ops/desinstala-todo.sh |

## Bloque E — llaves y servicio en cada máquina
lane: fast
depende: F
humano: login En la Mac, la Mini y la PC: corre `claude setup-token`, llena `~/.motor/secretos.env` (token de Claude, de Cursor, de Muse, de GitHub y del bot de esa marca) y corre una vez con tu contraseña el comando de `~/.motor/instalar-servicio.txt`
sonda-humano: bash ops/huellas.sh --servicio --rutas

## Bloque D — Orbit listo para el motor
lane: gate
depende: -
repo: https://github.com/gon0801/goncloud-Orbit.git
aviso: Orbit ya puede recibir planes del motor
prueba-real: python3 -c "import tomllib;c=tomllib.load(open('.motor.toml','rb'));assert c['repo']['principal']=='master' and 'completa' in c['github']['checks_extra']"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | `.motor.toml` en la raíz: principal `master`, `checks_extra = ["completa"]`, `[pruebas.env]` con lo que necesita la batería, `requiere = ["ssh-goncloud"]`. Sin receta de despliegue por omisión: cada plan de Orbit trae la suya en su bloque. | test -f .motor.toml |
| D.2 | En `CLAUDE.md`, una línea: los planes de Orbit se corren con el motor (`autopiloto: motor`); los loops de claw quedan retirados. | grep -q "autopiloto: motor" CLAUDE.md |

## Bloque G — Hermes usa el motor y se retira su cadena fija
lane: release
depende: -
repo: https://github.com/gon0801/goncloud-hermes.git
aviso: Hermes ya reparte las tareas chicas y manda al motor todo lo que cambia código
prueba-real: bash ops/motor-g/checklist.sh
despliegue: desplegar=ops/motor-g/desplegar.sh checklist=ops/motor-g/checklist.sh rollback=ops/motor-g/rollback.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| G.1 | Skill `motor` (copia de `skill/motor/SKILL.md` de goncloud-motor) en el perfil de Hermi y en los perfiles que hoy reciben pedidos de cambio. | test -f plugins/goncloud-team/skills/motor/SKILL.md |
| G.2 | Apagar `plugins/goncloud-team/local_orchestrator.py` (la cadena de 7 pasos), `raices/`, `avisos_sesion.py` y el cron `despertar-raices`; Hermi reparte la lectura a sus agentes como OpenClaw. Pruebas de Hermes en verde sin esos módulos. | python3 -m pytest -q plugins/goncloud-team/tests |
| G.3 | La tabla de modelos de Hermes se genera desde goncloud-motor-conf (sin copia a mano). | test -f model-routing.source.json |
| G.4 | Cerrar sin mergear el PR #37 (Raíces) con un comentario que apunte a este plan. | gh pr view 37 -R gon0801/goncloud-hermes --json state -q .state \| grep -q CLOSED |
| G.5 | `ops/motor-g/desplegar.sh`, `checklist.sh` y `rollback.sh`: actualizan el checkout de Hermes en la PC, reinician el gateway; `checklist.sh` sale 0 solo si Hermi responde por ssh y la cadena de 7 pasos ya no está registrada (y ≠0 si ssh falla). | bash -n ops/motor-g/desplegar.sh |

## Bloque H — OpenClaw usa el motor y se retiran los loops
lane: release
depende: -
repo: https://github.com/gon0801/goncloud-openclaw.git
aviso: OpenClaw ya reparte las tareas chicas y manda al motor todo lo que cambia código
prueba-real: bash ops/motor-h/checklist.sh
despliegue: desplegar=ops/motor-h/desplegar.sh checklist=ops/motor-h/checklist.sh rollback=ops/motor-h/rollback.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| H.1 | Skill `motor` en el agente main (claw) y en los que hoy reciben pedidos de cambio. | test -f agents/main/agent/skills/motor/SKILL.md |
| H.2 | `implementer` e `ingenieria` sin herramientas de escritura en repos; lo que cambia código va por `motor entrega`. | grep -q "motor entrega" agents/implementer/agent/AGENTS.md |
| H.3 | Retirar `scripts/agent-work/merge.py` y su prueba, los crons de vigías de loops y la skill `prompt-claw` (`~/.claude/skills/prompt-claw` en la Mac, con copia en `docs/retirado/`). | test ! -e scripts/agent-work/merge.py |
| H.4 | La tabla de modelos de OpenClaw se genera desde goncloud-motor-conf. | test -f docs/model-routing.md |
| H.5 | `ops/motor-h/desplegar.sh`, `checklist.sh` y `rollback.sh`: actualizan el checkout en la Mini, reinician el gateway; `checklist.sh` sale 0 solo si claw responde, tiene la skill `motor` y no queda ningún cron de vigía (y ≠0 si ssh falla). | bash -n ops/motor-h/desplegar.sh |

## Bloque I — prueba de fuego con apagón en la Mini y la PC
lane: release
depende: E, G, H
aviso: el motor pasó la prueba de fuego: terminó dos planes aunque se reiniciaron las máquinas a la mitad
prueba-real: bash ops/canario/verifica.sh
despliegue: desplegar=ops/canario/corre.sh checklist=ops/canario/verifica.sh rollback=ops/canario/limpia.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| I.1 | Repo privado gon0801/motor-canario y un plan canario de 3 bloques (código, otro repo, despliegue a una carpeta) con un paso humano de prueba con sonda automática. | test -f ops/canario/plan.md |
| I.2 | `ops/canario/corre.sh`: le pide el plan a claw (Mini) y a Hermi (PC) por su canal normal; cuando cada motor lleva un bloque a medias reinicia esa máquina (`sudo -n shutdown -r now` en la Mini, `shutdown /r /t 0` en la PC). | bash -n ops/canario/corre.sh |
| I.3 | `ops/canario/verifica.sh`: en cada máquina el plan termina, hay un PR y un merge por bloque, un corte anotado como corte (no como falla) y llegaron los avisos por el bot de cada marca. | bash -n ops/canario/verifica.sh |
