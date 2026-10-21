# Los agentes del motor trabajan con pstack y poteto-mode
autopiloto: motor
plan: pstack-01
repo: git@github.com:gon0801/goncloud-motor.git

<!-- Diseño: docs/diseno-pstack.md (y docs/arena/pstack/ con los tres candidatos y los hechos medidos).
Decisiones de David 2026-10-21: (1) el motor exige desde el primer día, por eso C mide el registro real
de cada CLI en cada máquina ANTES de encender la exigencia en E; (2) ningún agente lanza otros modelos
por la terminal; (3) el revisor usa poteto-mode + interrogate en carriles de código. Máquinas: Mac
(local, home ~/.motor), Mini (ssh gon@100.73.187.5), PC (ssh gwpc; Muse dentro de WSL). Nada se
instala a mano en las máquinas: pstack llega dentro de la conf compartida. -->

## Bloque A — pstack fijo dentro de la conf compartida
lane: gate
depende: -
repo: git@github.com:gon0801/goncloud-motor-conf.git
aviso: pstack quedó en la configuración compartida, en una versión fija, igual para las tres máquinas
prueba-real: python3 scripts/trae_pstack.py --comprueba
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | `scripts/trae_pstack.py <sha>`: baja cursor/plugins en ese commit (el mismo origen y transformación que `~/bin/sincronizar-pstack.sh` de la Mac: `disable-model-invocation: false`, nombres de carpeta) a `pstack/`; `--comprueba` lo regenera en una carpeta temporal y compara byte a byte (sale ≠0 si alguien editó la copia). Correrlo con el commit que hoy usa la Mac (`~/.agents/pstack-oficial`, `git -C … rev-parse HEAD`). | python3 scripts/trae_pstack.py --comprueba |
| A.2 | `pstack-motor/motor.md`: lo que manda sobre poteto-mode dentro de un encargo del motor (el agente no abre, marca listo ni mergea PRs: commit y push a su rama; Babysit y Shipping no aplican; no hay pausas ni preguntas, el plan es la autorización; desplegar es del motor; delega solo a subagentes de su propio CLI, nunca lanza otro CLI de agentes). Y un mapa por CLI en `pstack-motor/<cli>.md` (claude, kimi, opencode, grok, muse, qwen) con cómo carga una skill y cómo lanza un subagente en ese CLI, medido con `--help` (no inventar flags). | test -f pstack-motor/motor.md -a -f pstack-motor/claude.md -a -f pstack-motor/kimi.md -a -f pstack-motor/opencode.md -a -f pstack-motor/grok.md -a -f pstack-motor/muse.md -a -f pstack-motor/qwen.md |
| A.3 | `pstack.toml`: `[pstack] commit = "<sha>"`, `exigir = false` (se enciende en E) y `[exigencia]` por carril × papel: en todos, cargar poteto-mode; `codigo`/`gate`/`release` implementar: abrir un playbook; revisar en gate/release: además cargar `interrogate`; `min_subagentes = 0` en todas las celdas (se sube después de medir costo); fast: solo cargar poteto-mode. | python3 -c "import tomllib;t=tomllib.load(open('pstack.toml','rb'));assert t['pstack']['commit'] and t['pstack']['exigir'] is False and t['exigencia']" |

## Bloque B — el motor pide, verifica y hace cumplir
lane: gate
depende: -
aviso: el motor ya sabe pedirle a cada agente que use poteto-mode y comprobarlo en su registro real
prueba-real: python3 -m unittest tests.test_pstack tests.test_motor_e2e
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | `motor/pstack.py` puro: `Huella` (skills cargadas, playbook abierto, lista de tareas, subagentes, comandos) sacada del registro real del intento, nunca de lo que el agente dice; tabla `FORMATOS` con una fila por CLI (cómo se ve cargar una skill —herramienta `Skill`/`skill`/`read_skill` o lectura de `…/poteto-mode/SKILL.md` de la copia fija—, lanzar subagente y correr comando); `huella(fuente, formato)`, `juzga(huella, exigencia) -> Cumple \| Falta(lista en llano)`, `puerta(eventos, n)` (sin poteto-mode a la acción 6 → cortar), `instrucciones(exigencia, cli)` (texto de "Cómo trabajas" del encargo). Fuente por CLI según docs/arena/pstack/hechos.md §4: Claude el transcript de su `--session-id` fijo; Kimi y opencode (`--format json`) el `log.txt`; Grok su `updates.jsonl`; Muse su `session.jsonl`; Qwen su `--json-file`. Registro ilegible = "no verificable": pasa y avisa una vez por CLI y día. Pruebas `tests/test_pstack.py` con registros de ejemplo por CLI. | python3 -m unittest tests.test_pstack |
| B.2 | `effects`: materializa la copia de pstack de la conf por id de árbol en `~/.motor/pstack/<árbol>/` (atómico, inmutable mientras dura el intento; las viejas se borran con la limpieza de 14 días); agrega a `rutas.toml` los marcadores `{pstack}` y `{sesion}` (uuid por intento); el encargo de implementar y de revisar lleva `motor.md`, el mapa del CLI e `instrucciones(...)`; `collect` juzga la huella si `pstack.exigir` es true: si falta algo, kind `sin_pstack` con la lista (cuenta como falla del modelo; alta en `relevo.DE_KIND`), y deja `huella.json` en la carpeta del intento. Con `exigir = false` no cambia nada de hoy. `motor conf valida` exige `pstack.toml` coherente con la copia. | python3 -m unittest tests.test_pstack tests.test_motor_e2e |
| B.3 | `runwrap`: aplica la `puerta` en vivo leyendo el registro (corta al agente y deja `puerta` en outcome.json si a la acción 6 no cargó poteto-mode); pone en el PATH del agente shims de `claude`, `kimi`, `opencode`, `grok`, `muse`, `qwen` que salen con error ("dentro de un encargo no se lanza otro agente"), y lanza al agente con la ruta real de su CLI. Prueba con un agente falso que no carga la skill (se corta) y uno que intenta `kimi -p` (falla). | python3 -m unittest tests.test_motor_e2e |
| B.4 | Comprobaciones de resultado: (a) si el bloque es un arreglo de bug (`tipo: bug` en el bloque o la palabra "bug" en su título), el result del implementador trae `repro`: el motor lo corre en el SHA base (debe salir ≠0) y en la punta entregada (debe salir 0); si no, kind `sin_repro`. (b) Para cada bloqueante del revisor, el motor corre su `repro` en la copia del SHA revisado; si sale 0 no reprodujo y baja a nota. Pruebas e2e con el GitHub falso. | python3 -m unittest tests.test_motor_e2e |

## Bloque C — medición real en cada CLI y cada máquina
lane: gate
depende: A, B
aviso: medí cómo registra cada agente en cada máquina; el motor ya los lee bien a todos
prueba-real: bash ops/pstack/mide.sh --juzga
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | `ops/pstack/mide.sh`: en la Mac (local), la Mini (ssh) y la PC (ssh; Muse en WSL), materializa la copia de pstack de la rama de A y corre cada CLI con su ruta real y un encargo mínimo ("carga poteto-mode, abre el playbook de Docs y lanza un subagente que lea README.md; no cambies nada") en una carpeta temporal; trae el registro de cada uno a `tests/datos/pstack/<cli>-<maquina>/`. Solo escribe en carpetas temporales de cada máquina y las borra. | bash -n ops/pstack/mide.sh |
| C.2 | Con esos registros reales, pruebas en `tests/test_pstack.py` que exigen que `huella` vea, para cada CLI en cada máquina, la carga de poteto-mode, el playbook y el subagente; y que un registro sin carga dé `Falta`. Si algún CLI no puede cargar skills en alguna máquina, se dice en la prueba con el motivo y su fila de `FORMATOS` lo marca. | python3 -m unittest tests.test_pstack |
| C.3 | `mide.sh --juzga`: corre la medición en vivo y sale 0 solo si `juzga` da `Cumple` para todos los CLI de los carriles en las tres máquinas. | bash -n ops/pstack/mide.sh |

## Bloque D — instalar la versión nueva del motor en las tres
lane: release
depende: C
aviso: la versión del motor que exige pstack ya está en la Mac, la Mini y la PC (todavía sin exigir)
prueba-real: bash ops/huellas.sh --rutas
despliegue: desplegar=ops/instala-todo.sh checklist=ops/huellas.sh rollback=ops/desinstala-todo.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | Sube la etiqueta por omisión de `ops/lib.sh` a la siguiente versión (la última es v1.0.14) para que `instala-todo.sh` instale este código; ajusta `ops/tests/test-instala.sh`. | bash ops/tests/test-instala.sh |

## Bloque E — encender la exigencia
lane: gate
depende: D
repo: git@github.com:gon0801/goncloud-motor-conf.git
aviso: desde ahora todo agente del motor trabaja con poteto-mode, y el motor lo comprueba en las tres máquinas
prueba-real: ssh gon@100.73.187.5 'cd ~/motor && PYTHONPATH=. ~/.local/bin/uv run --python 3.12 python -m motor doctor --home ~/.motor --como-servicio --json' | python3 -c "import json,sys;t=sys.stdin.read();d=json.loads(t[t.find('{'):]);sys.exit(0 if d.get('ok') else 1)"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| E.1 | `rutas.toml`: cada ruta recibe pstack por la vía medida en C (Claude y DeepSeek `--plugin-dir {pstack}/pstack` y `--session-id {sesion}`; Kimi `--skills-dir`; opencode `--format json`; Muse y Grok la ruta absoluta del SKILL.md vía el encargo; Qwen `--json-file`). `pstack.toml` `exigir = true`. | PYTHONPATH="$MOTOR_RAIZ" python3 -m motor conf valida . |
