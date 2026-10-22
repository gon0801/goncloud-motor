# Los agentes del motor trabajan con pstack y poteto-mode
autopiloto: motor
plan: pstack-01
repo: git@github.com:gon0801/goncloud-motor.git

<!-- Diseño completo: docs/diseno-pstack.md. Los tres candidatos y los hechos medidos están en docs/arena/pstack/.

Qué cambia: hoy ningún agente del motor usa poteto-mode, ni siquiera en la Mac. Este plan hace tres cosas.
Pone una copia fija de pstack dentro de la conf compartida, para que llegue igual a la Mac, la Mini y la PC.
Le dice a cada agente cómo trabajar con poteto-mode. Y comprueba en el registro real de cada intento que lo hizo.

Decisiones de David (2026-10-21):
1. El motor exige poteto-mode desde el primer día. Por eso el bloque C mide el registro real de cada CLI en cada máquina antes de que el bloque E encienda la exigencia.
2. Un agente puede llamar a otros modelos para sus paneles, porque poteto-mode los pide (interrogate, arena, swarm). El motor cuenta cada llamada en los topes y en la cuota.
3. El revisor usa poteto-mode e interrogate en los carriles de código. En el carril fast usa solo poteto-mode.

Máquinas: la Mac (local, motor en ~/.motor), la Mini (ssh gon@100.73.187.5) y la PC (ssh gwpc; Muse corre dentro de WSL).
No instales nada a mano en ninguna máquina. -->

## Bloque A — pstack fijo dentro de la conf compartida
lane: gate
depende: -
repo: git@github.com:gon0801/goncloud-motor-conf.git
aviso: pstack ya está en la configuración compartida, en una versión fija e igual para las tres máquinas
prueba-real: python3 scripts/trae_pstack.py --comprueba
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | Escribe `scripts/trae_pstack.py <commit>`. El script baja cursor/plugins en ese commit a la carpeta `pstack/` y aplica la misma transformación que `~/bin/sincronizar-pstack.sh` de la Mac (`disable-model-invocation: false` y los nombres de carpeta). Con `--comprueba`, el script regenera la copia en una carpeta temporal, la compara byte a byte con `pstack/` y sale ≠0 si son distintas. Usa el commit que tiene hoy la Mac: `git -C ~/.agents/pstack-oficial rev-parse HEAD`. | python3 scripts/trae_pstack.py --comprueba |
| A.2 | Escribe `pstack-motor/motor.md` con lo que manda sobre poteto-mode dentro de un encargo del motor. El agente nunca abre, marca listo ni mergea un PR: hace commit y push a su rama. Los playbooks Babysit y Shipping no aplican. El agente no pausa ni pregunta, porque el plan es la autorización. El despliegue lo hace el motor. Para un panel de otros modelos, el agente llama al CLI de ese modelo y el motor cuenta la llamada. Escribe también un mapa por CLI en `pstack-motor/<cli>.md` (claude, kimi, opencode, grok, muse y qwen). Cada mapa dice cómo carga una skill ese CLI y cómo lanza un subagente. Saca cada flag de `<cli> --help`; no inventes flags. | test -f pstack-motor/motor.md -a -f pstack-motor/claude.md -a -f pstack-motor/kimi.md -a -f pstack-motor/opencode.md -a -f pstack-motor/grok.md -a -f pstack-motor/muse.md -a -f pstack-motor/qwen.md |
| A.3 | Escribe `pstack.toml` con tres partes. `[pstack] commit` es el commit de A.1. `exigir = false`, porque el bloque E la enciende. `[exigencia]` es la tabla de exigencias por carril y papel: en todas las celdas, cargar poteto-mode; al implementar en gate y release, además abrir un playbook; al revisar en gate y release, además cargar interrogate; en fast, solo cargar poteto-mode. Pon `min_subagentes = 0` en todas las celdas; se sube después de medir el costo. | python3 -c "import tomllib;t=tomllib.load(open('pstack.toml','rb'));assert t['pstack']['commit'] and t['pstack']['exigir'] is False and t['exigencia']" |

## Bloque B — el motor pide y comprueba poteto-mode
lane: gate
depende: -
aviso: el motor ya sabe pedirle a cada agente que use poteto-mode y comprobarlo en su registro real
prueba-real: python3 -c "import os,subprocess,sys;sys.exit(subprocess.call([sys.executable,'-m','unittest','tests.test_pstack']+([] if os.name=='nt' else ['tests.test_motor_e2e.TestPstack','tests.test_motor_e2e.TestPuertaEnVivo','tests.test_motor_e2e.TestLlamadasAOtrosModelos','tests.test_motor_e2e.TestResultadosNoSoloCarga'])))"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Crea `motor/pstack.py` con funciones puras. `huella(fuente, formato)` saca del registro real del intento qué hizo el agente: las skills que cargó, si abrió un playbook, si usó su lista de tareas, los subagentes que lanzó y los comandos que corrió. Nunca uses lo que el agente escribe en su resultado. `juzga(huella, exigencia)` devuelve `Cumple` o `Falta` con la lista de lo que faltó en lenguaje llano. `puerta(eventos, n)` dice si hay que cortar al agente: si a la acción 6 no cargó poteto-mode, se corta. `instrucciones(exigencia, cli)` escribe la sección "Cómo trabajas" del encargo. La tabla `FORMATOS` tiene una fila por CLI. Cada fila dice cómo se ve en su registro cargar una skill, lanzar un subagente y correr un comando. Cargar una skill cuenta de dos formas: por la herramienta de skills del CLI (`Skill`, `skill` o `read_skill`) o por leer `…/poteto-mode/SKILL.md` de la copia fija. La fuente del registro de cada CLI está en `docs/arena/pstack/hechos.md`, sección 4. Si el registro no se puede leer, el resultado es "no verificable": la entrega pasa y el motor avisa una vez por CLI y por día. Pruebas en `tests/test_pstack.py` con un registro de ejemplo por CLI. | python3 -m unittest tests.test_pstack |
| B.2 | En `motor/effects.py`: materializa la copia fija de pstack en `~/.motor/pstack/<id de árbol>/`, con un rename atómico. La copia no cambia mientras dura un intento. La limpieza de 14 días borra las copias viejas. Agrega los marcadores `{pstack}` y `{sesion}` a las rutas de `rutas.toml`; `{sesion}` es un uuid por intento. El encargo de implementar y el de revisar llevan `motor.md`, el mapa del CLI y la sección "Cómo trabajas". Si `pstack.exigir` es true, `collect` juzga la huella. Si falta algo, el resultado es el kind `sin_pstack` con la lista de lo que faltó. `sin_pstack` cuenta como falla del modelo: agrégalo a `relevo.DE_KIND`. Guarda `huella.json` en la carpeta del intento. Con `exigir = false`, el motor se comporta igual que hoy. `motor conf valida` exige que `pstack.toml` coincida con la copia fija. | python3 -c "import os,subprocess,sys;sys.exit(subprocess.call([sys.executable,'-m','unittest','tests.test_pstack']+([] if os.name=='nt' else ['tests.test_motor_e2e.TestPstack'])))" |
| B.3 | En `motor/runwrap.py`: aplica la puerta en vivo. Si el agente llega a la acción 6 sin cargar poteto-mode, córtalo y deja `puerta` en `outcome.json`. Pon en el PATH del agente un shim por cada CLI de modelos (`claude`, `kimi`, `opencode`, `grok`, `muse` y `qwen`). El shim deja pasar la llamada y la cuenta. Registra cada llamada en la carpeta del intento: modelo, cuenta, inicio y fin. El motor cuenta esas llamadas en los topes (4 por máquina y 4 por cuenta) y respeta las pausas de cuota de la cuenta. Si la cuenta no tiene lugar o está en pausa, el shim sale enseguida con el mensaje "esa cuenta no tiene lugar ahora; usa otro modelo". Dentro de una llamada, los shims rechazan otra llamada. Escribe tres pruebas con un agente falso: una llamada a otro CLI pasa y queda contada; con la cuenta llena, se rechaza; una llamada desde dentro de otra, se rechaza. | python3 -c "import os,subprocess,sys;sys.exit(subprocess.call([sys.executable,'-m','unittest','tests.test_pstack']+([] if os.name=='nt' else ['tests.test_motor_e2e.TestPuertaEnVivo','tests.test_motor_e2e.TestLlamadasAOtrosModelos'])))" |
| B.4 | Comprueba resultados, no solo la carga de la skill. Primero, un bloque es de bug si su campo `tipo` es `bug` o su título dice "bug". En un bloque de bug, el resultado del implementador trae un comando `repro`. El motor corre `repro` en el commit base, donde debe fallar, y en la punta entregada, donde debe salir 0. Si no se cumple, el resultado es el kind `sin_repro`. Segundo, el motor corre el `repro` de cada bloqueante del revisor en una copia del commit revisado. Si el comando sale 0, no reprodujo nada y el bloqueante baja a nota. Escribe pruebas e2e de los dos casos con el GitHub falso. | python3 -c "import os,subprocess,sys;sys.exit(subprocess.call([sys.executable,'-m','unittest','tests.test_pstack']+([] if os.name=='nt' else ['tests.test_motor_e2e.TestResultadosNoSoloCarga'])))" |

## Bloque C — el motor lee bien a cada agente en cada máquina
lane: gate
depende: A, B
aviso: medí cómo registra cada agente en cada máquina, y el motor ya los lee bien a todos
prueba-real: bash ops/pstack/mide.sh --juzga
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Escribe `ops/pstack/mide.sh`. El script corre en la Mac (local), en la Mini (ssh) y en la PC (ssh; Muse dentro de WSL). En cada máquina, materializa la copia fija de pstack de la rama del bloque A y corre cada CLI con su ruta real. El encargo es mínimo: "carga poteto-mode, abre el playbook Docs y lanza un subagente que lea README.md; no cambies nada". Cada corrida usa una carpeta temporal. El script trae el registro de cada CLI a `tests/datos/pstack/<cli>-<maquina>/` y borra las carpetas temporales. No escribe en ninguna otra parte. | bash -n ops/pstack/mide.sh |
| C.2 | Agrega a `tests/test_pstack.py` pruebas con esos registros reales. Para cada CLI en cada máquina, `huella` debe ver la carga de poteto-mode, el playbook y el subagente. Un registro sin carga debe dar `Falta`. Si un CLI no puede cargar skills en una máquina, la prueba lo dice con el motivo y la fila de ese CLI en `FORMATOS` lo marca. | python3 -m unittest tests.test_pstack |
| C.3 | Agrega a `mide.sh` la opción `--juzga`. Con ella, el script corre la medición en vivo y sale 0 solo si `juzga` da `Cumple` para todos los CLI de los carriles en las tres máquinas. | bash -n ops/pstack/mide.sh |

## Bloque D — la versión nueva del motor en las tres máquinas
lane: release
depende: C
aviso: la versión del motor que comprueba poteto-mode ya está en la Mac, la Mini y la PC, todavía sin exigirlo
prueba-real: bash ops/huellas.sh --rutas
despliegue: desplegar=ops/instala-todo.sh checklist=ops/huellas.sh rollback=ops/desinstala-todo.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | Sube la etiqueta por omisión de `ops/lib.sh` a la versión siguiente. La última instalada es v1.0.14. Así, `instala-todo.sh` instala este código. Ajusta `ops/tests/test-instala.sh` a la etiqueta nueva. | bash ops/tests/test-instala.sh |

## Bloque E — encender la exigencia
lane: gate
depende: D
repo: git@github.com:gon0801/goncloud-motor-conf.git
aviso: desde ahora todo agente del motor trabaja con poteto-mode, y el motor lo comprueba en las tres máquinas
prueba-real: ssh gon@100.73.187.5 'cd ~/motor && PYTHONPATH=. ~/.local/bin/uv run --python 3.12 python -m motor doctor --home ~/.motor --como-servicio --json' | python3 -c "import json,sys;t=sys.stdin.read();d=json.loads(t[t.find('{'):]);sys.exit(0 if d.get('ok') else 1)"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| E.1 | En `rutas.toml`, dale pstack a cada ruta por la vía que midió el bloque C. Claude y DeepSeek: `--plugin-dir {pstack}/pstack` y `--session-id {sesion}`. Kimi: `--skills-dir`. opencode: `--format json`. Muse y Grok: la ruta absoluta de `SKILL.md`, que va en el encargo. Qwen: `--json-file`. Después pon `exigir = true` en `pstack.toml`. | PYTHONPATH="$MOTOR_RAIZ" python3 -m motor conf valida . |
