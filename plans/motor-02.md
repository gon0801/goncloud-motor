# El motor no se cae, no pierde trabajo y no se atora en silencio
autopiloto: motor
plan: motor-02
repo: git@github.com:gon0801/goncloud-motor.git

<!-- Origen: la revisión del motor del 2026-10-21. Tres modelos (Opus, Fable y Sonnet) revisaron cada
uno por su cuenta el código en ea1e709. Este plan arregla todo lo que encontraron, salvo lo que va
contra la regla de David de no ponernos candados a nosotros mismos (ver "Fuera del plan").

Qué cambia para David: el motor deja de caerse por un archivo malo, deja de perder el trabajo de
los agentes, avisa una vez y con el motivo cuando algo se atora, y se destraba solo cuando se corrige
el plan o la máquina. En la PC los planes corren igual que en la Mac, y Muse puede trabajar en WSL.

Arranque: en la Mac, entregado por OpenBot (DG), cuando se integre la parte B de PSTACK 01 (PR 41).
PSTACK 01 quedó cortado en la PC a sus bloques A y B (toca los mismos archivos y sus bloques C y D
no corren bien en Windows); sus bloques C, D y E van después de este plan.

Reglas para quien implementa:
0. Trabaja con poteto-mode de pstack. La primera fila de cada bloque dice cómo cargarlo.
1. Cada arreglo lleva en el mismo commit una prueba que falla sin el arreglo. El revisor lo comprueba
   quitando el arreglo y corriendo la prueba.
2. Las Acepta corren solo las pruebas de su fila. La batería completa corre en CI.
3. El motor tiene que seguir leyendo los logs que ya existen en las tres máquinas. Un evento viejo
   sin un campo nuevo vale como antes. Nunca vuelvas a correr un paso de un bloque ya hecho por un
   cambio de formato.
4. Las rutas y nombres de esta lista son del código en ea1e709. Si PSTACK 01 los movió, sigue el
   código actual.

Máquinas: la Mac (local, motor en ~/.motor), la Mini (ssh gon@100.73.187.5) y la PC (ssh gwpc;
Muse corre dentro de WSL).

Medido el 2026-10-21 para este plan:
- En la PC, `bash` es el lanzador de WSL (`WindowsApps\bash.exe`). Git Bash está en
  `C:\Program Files\Git\bin\bash.exe`. Dentro de Git Bash, `python3` es el acceso directo de la
  Tienda de Windows.
- El git de WSL no reconoce una carpeta de trabajo creada por el git de Windows:
  `wsl.exe -e git -C /mnt/c/Users/ehven/.motor/wt/pstack-01/B status` da "not a git repository".
- Versiones de git: Mac 2.55, Mini 2.54, PC 2.56 y WSL 2.53. Todas aceptan `--relative-paths`. -->

## Bloque A - quitar lo que el motor no usa
lane: gate
depende: -
aviso: quité del motor el código que no usaba y las reglas escritas que no cumplía
prueba-real: python3 -m unittest tests.test_relevo tests.test_fallas
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. En `motor/relevo.py`, borra lo que producción no llama: `tras_falla`, `Paso`, `espera_rate_limit`, `cuenta_sana`, `BLOQUEA_TRAS_MUTACION`, `CUENTA_SANA_TRAS`, `ESPERAS_RATE_LIMIT_S`, `ESPERA_MAX_S` y `DORMIDO_MAX_S`, y los parámetros `mutado` y `heuristica` de `avanza`. Borra sus pruebas en `tests/test_relevo.py`. Reescribe el docstring del módulo para que diga solo lo que el motor hace hoy. Por ejemplo, hoy la pausa por cuota dura `cooldown_default_s` (5 h), no 15 min. En `motor/fallas.py`, cambia el comentario de `FRENO_CORTO_S` que cita `ESPERA_MAX_S`. | python3 -m unittest tests.test_relevo tests.test_fallas |
| A.2 | Borra `retry_after_s` de `motor/machine.py`, que nadie usa. Borra la clave `boot_id_archivo` de `motor/conf.py` y de `conf-ejemplo/`. | python3 -m unittest tests.test_puro tests.test_doctor |

## Bloque B - un archivo, un texto o un resultado malo ya no tumban al motor
lane: gate
depende: A
aviso: un archivo, un texto o un resultado malo ya no tumban al motor; lo aparta y te avisa
prueba-real: python3 -m unittest tests.test_no_caerse
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. En `daemon.ingest_inbox`, procesa cada archivo de cada bandeja dentro de su propio `try`. Valida la forma del mensaje en la frontera con `Comando.from_dict` en `motor/plan.py`. `cmd_id` y `tipo` son texto. `plan` es un dict con `id`. `nota` trae `block` y `texto`. `hecho` trae `block`. Cualquier error termina igual: JSON roto, UTF-8 inválido, clave faltante, `PlanError`, o un `.motor.toml` mal escrito en la rama base. El archivo pasa a `inbox/rechazados/`, se registra `CommandReceived` con `resultado` igual a `rechazado: <motivo>`, se encola una vez el aviso `rechazado`, y el archivo siguiente se procesa en el mismo tick. Pruebas en `tests/test_no_caerse.py`, clase `BandejaTest`. Pon en la bandeja un JSON roto, un mensaje sin `cmd_id`, un plan sin `id` y un plan cuyo repo trae un `.motor.toml` inválido, y detrás de ellos un plan bueno. El plan bueno queda `tomado` en el mismo tick. | python3 -m unittest tests.test_no_caerse.BandejaTest |
| B.2 | `notify.render` nunca lanza. Si el texto completo no pasa `lint_plain`, prueba en este orden. Primero, con "un plan" en lugar del título. Después, con el texto fijo de la plantilla sin ningún campo del plan. Al final, con el aviso genérico. Vale para las diez plantillas. Pruebas en la clase `AvisosTest`. Usa los títulos "Webhooks JSON de Shopify", "Sincronizar /ads/campaign", "Reporte 20261021" y "commit diario", con cada plantilla. Todas devuelven un texto que pasa `lint_plain`. | python3 -m unittest tests.test_no_caerse.AvisosTest |
| B.3 | En `daemon.tick`, cada plan corre `ingest_attempts`, `fold` y `decide` dentro de su propio `try`. `trae_conf`, `recargar_conf`, `ajusta_sueno`, `purga_registros` y `envia_pendientes` también, cada uno por separado. Un error se registra como `EffectFailed`, con `accion` igual a la fase, y el tick sigue con lo demás. `Daemon.run` atrapa el error de un tick, lo escribe en el log del servicio y sigue con el tick siguiente. Pruebas en la clase `TickTest`. Un plan cuyo log trae un `Reviewed` con `bloqueantes` mal formados no detiene a otro plan, que sí avanza. Un `ajusta_sueno` que se pasa de su tope no detiene el tick. | python3 -m unittest tests.test_no_caerse.TickTest |
| B.4 | En `effects.collect`, valida `result.json` en la frontera. Tiene que ser un dict. Al implementar trae `sha` y `resumen` de texto. Al revisar trae `sha`, `veredicto` (APROBADO o CAMBIOS), `bloqueantes` y `notas`. `bloqueantes` es una lista de dicts con `texto`, y opcionalmente `repro` y `arreglo`, todos de texto. `notas` es una lista de textos. Si no cumple, el intento termina como `sin_resultado` (implementar) o `veredicto_invalido` (revisar), con el motivo exacto. Pruebas en la clase `ResultadoTest`. Prueba una lista en lugar de un dict, `bloqueantes: ["texto"]`, `bloqueantes: null` y un bloqueante sin `texto`. | python3 -m unittest tests.test_no_caerse.ResultadoTest |
| B.5 | En `parse_plan`, los ids de plan y de bloque cumplen `^[A-Za-z0-9][A-Za-z0-9._-]*$`, no terminan en `.lock` y no contienen `..`. Una fila con más de tres celdas es un error que dice que hay que escapar la barra vertical con una barra invertida. Un bloque `lane: release` sin `despliegue:` y sin receta en el `.motor.toml` del repo se rechaza al entregar. Pruebas en la clase `PlanTest`. | python3 -m unittest tests.test_no_caerse.PlanTest |
| B.6 | En `Daemon.run`, si la conf fija una versión que no está instalada en disco, el daemon no sale. Encola una vez el aviso "esta máquina necesita la versión X del motor" y sigue trabajando. Si el código instalado en disco cambió desde que arrancó (`version.sha_actual()` distinto del sha con el que arrancó), sale con 75 al terminar el tick, y el supervisor lo relanza con el código nuevo. Así, instalar una versión basta para que cada máquina la use, sin reinicio a mano. Pruebas en la clase `VersionTest`. | python3 -m unittest tests.test_no_caerse.VersionTest |

## Bloque C - el trabajo de un agente nunca se pierde
lane: gate
depende: B
aviso: el trabajo de un agente ya no se pierde si se corta, se cuelga o falla la red
prueba-real: python3 -m unittest tests.test_trabajo
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. En `workspace.prepara`, decide el punto de partida antes de tocar la carpeta. Si existe la foto wip (`origin/motor-wip/...` o `refs/motor-wip/...`) y su padre desciende del punto de partida (`origin/<rama>` o, si no existe, `origin/<base>`), la rama arranca en el padre de la foto. Encima queda, sin commitear, el árbol de la foto. Nunca corras `reset --hard` ni `clean` antes de esa decisión. Agrega a `proto/fake_agent.py` el modo `commits_sin_push`: dos commits locales y un archivo sin commitear, y después se corta. Prueba e2e en `tests/test_trabajo.py`, clase `RetomaTest`. El intento 2 ve los dos commits en `git log` y el archivo sin commitear. | python3 -m unittest tests.test_trabajo.RetomaTest |
| C.2 | Al restaurar la foto, lo que el agente borró sigue borrado. Usa `git read-tree -m -u`, o borra los archivos que lista `git diff --name-only --diff-filter=D HEAD <foto>`. Prueba en `RetomaTest`. El agente borra `viejo.py`, se toma la foto y se corta. El intento 2 no tiene `viejo.py`. | python3 -m unittest tests.test_trabajo.RetomaTest |
| C.3 | `workspace.punta` lanza una excepción si `git ls-remote` falla. Devuelve "" solo cuando la rama no existe. `collect` deja subir esa excepción, así el daemon reintenta recoger en el tick siguiente y no escribe `AttemptEnded`. Prueba en la clase `PuntaTest`, con un remoto que no responde. No se registra `mintio` y el intento se recoge cuando el remoto vuelve. | python3 -m unittest tests.test_trabajo.PuntaTest |
| C.4 | `Daemon.__init__` toma un candado exclusivo sobre `home/motor.lock`: `fcntl.flock` en la Mac y la Mini, `msvcrt.locking` en Windows. Un segundo daemon sobre el mismo home sale con un mensaje claro. `motor estado` abre la base en solo lectura, sin construir un `Daemon`. `start_attempt` escribe `started` antes de lanzar el proceso. Pruebas en la clase `ProcesosTest`. | python3 -m unittest tests.test_trabajo.ProcesosTest |
| C.5 | Cuando `collect` da `perdido`, el motor mata el grupo del pid de `agent.pid` antes de lanzar otro intento en la misma carpeta. Para decidir si un pid sigue vivo, compara también su hora de inicio con la que guarda `started`. Un pid reusado no cuenta como vivo. Un intento que sigue "vivo" después de `timeout_s` más 15 min se da por perdido. En `collect`, vuelve a buscar `outcome.json` después de consultar el pid, porque pudo aparecer entre las dos lecturas. Pruebas en la clase `ProcesosTest`. | python3 -m unittest tests.test_trabajo.ProcesosTest |
| C.6 | Si existe un `result.json` válido cuyo SHA es la punta remota de la rama, se evalúa antes de clasificar la salida del proceso. Un agente que dejó su entrega y después se pasó del tope, se quedó sin avance o salió distinto de 0 entregó igual. Prueba en la clase `RetomaTest`, con un agente falso que escribe su resultado y sigue imprimiendo hasta el tope. | python3 -m unittest tests.test_trabajo.RetomaTest |

## Bloque D - el motor dice por qué está parado, y avisa una vez cuando se atora
lane: gate
depende: C
aviso: cuando algo se atora, el motor te dice qué y por qué, una vez, y el resumen de cada hora ya no dice "nada atorado" en falso
prueba-real: python3 -m unittest tests.test_diagnostico
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. Crea `motor/diagnostico.py` con `diagnostico(snap, bid, policy, now) -> Diagnostico`. `Diagnostico` es una dataclass con `causa` (un enum), `motivo` (la frase para David), `clave` (la del aviso), `atorado` (bool) y `detalle` (texto técnico). Es la única fuente del aviso de atasco, del resumen de cada hora, de `motor estado` y del motivo en el encargo del agente. Borra `_sin_avance_propio`, `motivo` y `_ultima_falla` de `motor/machine.py`, y la parte de avisos de `_sin_agente`. Mueve `_resumen`, `NOMBRES`, `_dura` y `_lista` a `motor/avisos.py`. Cada causa tiene su propia clave de aviso. Ninguna clave se comparte entre causas. Pruebas en `tests/test_diagnostico.py`, una por causa. | python3 -m unittest tests.test_diagnostico && python3 -c "import sys;sys.exit(0 if sum(1 for _ in open('motor/machine.py')) < 900 else 1)" |
| D.2 | La alarma de "un paso del motor lleva una hora fallando" funciona con lo que el daemon escribe de verdad. El primer `EffectFailed` de una racha guarda `desde`, por plan, bloque y acción. La racha termina con el primer éxito de esa acción, que se anota una vez como `EffectOk`. Una racha de una hora o más es atasco. Las pruebas pasan por `Daemon._falla_efecto`, sin meter eventos a mano. Pruebas en la clase `RachaTest`. Cuatro horas fallando cada 5 min avisan una vez. Dos fallas sueltas separadas por una hora no avisan. Tras un éxito, una falla nueva empieza otra racha. | python3 -m unittest tests.test_diagnostico.RachaTest |
| D.3 | `fold` borra `ready`, `listo_desde` y el `ChecksResult` del SHA cuando ve `PrCerrado`. Un PR nuevo vuelve a pasar por "listo" y por las pruebas. Prueba en la clase `PrTest`. El bloque se entrega, se marca listo, sale verde, se cierra el PR y se abre otro. El siguiente paso es marcar listo, no mergear. | python3 -m unittest tests.test_diagnostico.PrTest |
| D.4 | Las fallas pasajeras (`transitorio`, `sin_resultado`, `perdido` y `tiempo`) ya no cuentan para `fallas_seguidas_max`. La cuenta queda en una pausa corta que crece: 1, 5, 15 y 30 min. `sin_cambios` tampoco cuenta cuando el bloque falló por algo que el agente no controla: un comando que falta, la prueba real o el despliegue. Pruebas en la clase `EscaleraTest`. Cuatro fallas pasajeras seguidas no agotan la escalera, y el bloque sigue cuando la red vuelve. | python3 -m unittest tests.test_diagnostico.EscaleraTest |
| D.5 | Si todas las cuentas de la escalera de un bloque están sin sesión (`auth`), el motor manda el aviso `humano` con el nombre del CLI y el login que falta, una vez por cuenta y por racha. Si todas están sin cupo, avisa al entrar en la pausa, con la hora a la que vuelven, sin esperar `aviso_lento_s`. Con la escalera agotada, el diagnóstico es atasco con su motivo, y el resumen lo dice. Pruebas en la clase `EscaleraTest`. | python3 -m unittest tests.test_diagnostico.EscaleraTest |
| D.6 | La plantilla `atorado` ya no promete que "Claude la va a revisar". Un aviso que Telegram rechaza con un 4xx pasa a `avisos/fallidos/` y la cola sigue con el siguiente. Si faltan el token o el chat, `motor doctor` lo marca como error. El resumen de cada hora se recorta para no pasar de 4000 caracteres. Las notas del revisor que no bloquean van en el encargo del intento siguiente. Pruebas en la clase `AvisosColaTest`. | python3 -m unittest tests.test_diagnostico.AvisosColaTest |

## Bloque E - la primera entrega en las tres máquinas
lane: release
depende: D
aviso: la versión del motor que no se cae, no pierde trabajo y avisa cuando se atora ya está en la Mac, la Mini y la PC
prueba-real: bash ops/huellas.sh --rutas
despliegue: desplegar=ops/instala-todo.sh checklist=ops/huellas.sh rollback=ops/desinstala-todo.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| E.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. Sube la etiqueta por omisión de `ops/lib.sh` a la siguiente de la última publicada (`git tag -l 'v1.*' --sort=-v:refname`, la primera). Ajusta `ops/tests/test-instala.sh` a la etiqueta nueva. Con B.6, cada daemon se reinicia solo al ver la versión nueva en disco. | bash ops/tests/test-instala.sh |

## Bloque F - corregir el plan o la máquina destraba el bloque
lane: gate
depende: E
aviso: si corriges el plan o instalas lo que faltaba, el motor vuelve a revisar solo y el bloque sigue
prueba-real: python3 -m unittest tests.test_reevalua
| Fila | Contenido | Acepta |
| --- | --- | --- |
| F.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. `Verified`, `Proved` y `Deployed` guardan la huella de su insumo: las Acepta del bloque, la `prueba-real` y la receta, respectivamente, más la huella de `path_extra`, `env` y `tiene` de la máquina. `fold` ignora un resultado cuya huella no coincide con el plan y la máquina actuales. Así, reentregar el plan corregido o cambiar la máquina vuelve a correr el paso sobre el mismo SHA, sin lanzar un agente. Un resultado sin huella (de antes de este cambio) vale igual que hoy. Prueba en la clase `ReevaluaTest`. La verificación falla por un comando que falta, se reentrega el plan con el comando corregido, y el siguiente paso es verificar el mismo SHA. | python3 -m unittest tests.test_reevalua.ReevaluaTest |
| F.2 | `comandos_faltantes` se aplica también en `prove` y en `deploy`. Si falta un comando, el bloque no vuelve al agente. El diagnóstico es atasco con el motivo, y el bloque espera a que cambien el plan o la máquina (F.1). Prueba en `ReevaluaTest`. | python3 -m unittest tests.test_reevalua.ReevaluaTest |
| F.3 | Cada `PlanLoaded` lleva un número de versión, y el plan vigente es el de la última entrega. Reentregar una versión anterior la reactiva, y el recibo dice "tomado (versión N)". Si la versión nueva cambia el contenido de un bloque ya hecho (filas, Acepta o prueba real), ese bloque se reabre, y el encargo dice qué cambió. Si la versión nueva quita un bloque con un intento en curso, el motor detiene ese intento. Pruebas en la clase `ReentregaTest`. | python3 -m unittest tests.test_reevalua.ReentregaTest |
| F.4 | `Merged` solo nace de `effects.merge` con su `MergeTicket`, o de adoptar un merge hecho por fuera. Si la entrega es la punta de la base que había al cargar el plan, el intento termina `sin_cambios`, no `Merged`. Un merge hecho por fuera se adopta y se avisa una vez ("alguien integró el bloque X por fuera; sigo con eso"). Pruebas en la clase `MergeTicketTest`. | python3 -m unittest tests.test_reevalua.MergeTicketTest |
| F.5 | Las claves que evitan anotar dos veces el mismo hecho (`ukey`) se construyen en un solo módulo, `motor/claves.py`, con una función por tipo de evento. Ninguna otra parte del código arma una a mano. Varias fallas de la revisión vinieron de una clave demasiado gruesa o demasiado fina, armada en el lugar. Pruebas en la clase `ClavesTest`. Cada tipo de evento tiene su clave, y ninguna depende de la hora del reloj. | python3 -m unittest tests.test_reevalua.ClavesTest |
| F.6 | Si llega una nota o una versión nueva del plan para un bloque con un intento de implementar en curso, el motor detiene ese intento (con su foto wip) y lanza otro con la nota o el cambio en el encargo. El intento detenido no cuenta como falla del modelo. El 2026-10-22, Kimi trabajó 90 min en PSTACK 01 con un aviso viejo después de que el plan ya se había corregido. Pruebas en la clase `InterrumpeTest`. | python3 -m unittest tests.test_reevalua.InterrumpeTest |
| F.7 | El encargo del implementador dice que, si lo que falla no es el código del bloque (la máquina, una Acepta que no puede pasar ahí, un servicio externo), no lo arregle por su cuenta. El agente termina con el resultado `bloqueado` y el motivo. El motor no le regresa el bloque a otro agente: el diagnóstico es atasco con ese motivo y avisa una vez. `bloqueado` no cuenta como falla del modelo. Se destraba con una nota o con un plan o una máquina corregidos (F.1 y F.6). Pruebas en la clase `BloqueadoTest`. | python3 -m unittest tests.test_reevalua.BloqueadoTest |

## Bloque G - nada congela la máquina
lane: gate
depende: F
aviso: una conexión colgada o una verificación larga ya no congelan el motor; lo demás sigue y los avisos salen a tiempo
prueba-real: python3 -m unittest tests.test_topes
| Fila | Contenido | Acepta |
| --- | --- | --- |
| G.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. `workspace.git` y `GhGitHub._gh` llevan `timeout`: 180 s para clone, fetch y push, y 60 s para lo demás. Las dos ponen `GIT_TERMINAL_PROMPT=0`. Pasarse del tope lanza una excepción que el daemon trata como falla del efecto. Prueba en la clase `TopesTest`, con un remoto que acepta la conexión y no responde. | python3 -m unittest tests.test_topes.TopesTest |
| G.2 | `_run` espera a que salga el proceso, no al fin de la salida. La salida va a un archivo. Un hijo en segundo plano que hereda la salida no convierte un éxito en tope vencido. Al pasarse del tope devuelve 124 con la salida parcial. Después de matar el grupo nunca espera sin tope. Borra `_run_tope`, porque `_run` ya hace lo mismo. Pruebas en `TopesTest`. `echo ok; (sleep 30) &` sale 0 en menos de 2 s. Un comando que imprime y se cuelga devuelve 124 con lo que imprimió. | python3 -m unittest tests.test_topes.TopesTest |
| G.3 | La verificación, el despliegue y la prueba real corren aparte, igual que los agentes con `runwrap`. El daemon los lanza, anota `VerifyStarted`, `DeployStarted` o `ProveStarted`, y los recoge en un tick posterior desde su `outcome.json`. El tick nunca los espera. Si el motor se reinicia a medias, recoge el que ya terminó y relanza el que murió, sin repetir lo que ya terminó. Pruebas e2e en la clase `ApartTest`. Con una verificación de 60 s en curso, una nota entregada se toma en menos de 20 s y otro plan avanza. | python3 -m unittest tests.test_topes.ApartTest |
| G.4 | `plans()` deja fuera los planes con `Limpiado`. Los cooldowns y los agentes de los otros planes se calculan una vez por tick, no una vez por plan. La tabla `events` tiene un índice por `plan`. Prueba en la clase `CicloTest`. Con 100 planes terminados de 300 eventos cada uno, un tick sin trabajo tarda menos de 0.5 s. Hoy tarda unos 30 s, según la medición de la revisión. | python3 -m unittest tests.test_topes.CicloTest |
| G.5 | `runwrap` revisa el avance de la carpeta cada 10 s, no cada 0.2 s, y salta `.git`, `.venv`, `node_modules` y `__pycache__`. La foto wip corre fuera del bucle que vigila `--timeout`, `--sin-avance` y `--cierre`, así un `git push` colgado no los apaga. Pruebas en `tests/test_runwrap.py`. | python3 -m unittest tests.test_runwrap |

## Bloque H - GitHub y el despliegue dicen la verdad
lane: gate
depende: G
aviso: el motor ya no da por buenas pruebas que no corrieron, le dice al agente qué falló en GitHub, y despliega sin dejar producción a medias
prueba-real: python3 -m unittest tests.test_github_despliegue
| Fila | Contenido | Acepta |
| --- | --- | --- |
| H.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. Si leer la protección de la rama falla con algo distinto de 404 "Branch not protected", es una falla del efecto y se reintenta. Ya no cuenta como "sin pruebas obligatorias". Lee también las reglas de la rama (`repos/{n}/rules/branches/{base}`) y los estados del commit (`commits/{sha}/status`), y pagina los check runs. Sin pruebas obligatorias conocidas, el veredicto exige que todos los check runs y estados del SHA terminen y pasen. Pruebas en `tests/test_github_despliegue.py`, clase `ChecksTest`, con respuestas grabadas de GitHub. | python3 -m unittest tests.test_github_despliegue.ChecksTest |
| H.2 | Si una prueba obligatoria se saltó en borrador y no vuelve a correr, el motor la dispara de nuevo una sola vez (`gh run rerun` de su corrida) tras 30 min con GitHub terminado. Si sigue sin correr, es atasco con su motivo. Prueba en `ChecksTest`. | python3 -m unittest tests.test_github_despliegue.ChecksTest |
| H.3 | `GitHubPort.merge` lanza excepciones con tipo, en `GhGitHub` y en `FakeGitHub`: `PuntaMovida`, `Conflicto`, `PoliticaBloquea` y `Borrador`. Con `PuntaMovida`, el agente lee "alguien subió un commit a la rama del PR después de la revisión; revísalo y vuelve a entregar". Con `PoliticaBloquea`, el `ChecksResult` del SHA se invalida y el bloque vuelve a esperar las pruebas. Si la política no cambia en una hora, es atasco con el texto de GitHub. Con `Borrador`, el motor marca el PR como listo. Borra `es_del_agente` y su comparación de textos en inglés. Pruebas en la clase `MergeTest`. | python3 -m unittest tests.test_github_despliegue.MergeTest |
| H.4 | Cuando las pruebas de GitHub salen en rojo, el encargo del agente trae el número del PR, la URL de cada corrida que falló y las últimas 80 líneas de `gh run view <id> --log-failed`. Si el agente vuelve a entregar el mismo SHA tras un rojo de GitHub, el motor corre otra vez las pruebas que fallaron (`gh run rerun --failed`) una sola vez antes de contarle la falla, porque una prueba que falla a veces no es culpa del agente. Prueba en `ChecksTest`, con un `gh` falso. | python3 -m unittest tests.test_github_despliegue.ChecksTest |
| H.5 | La receta de despliegue trae los tres scripts, ninguno vacío, y se valida al entregar el plan. El checklist recibe `MOTOR_SHA` y sale 0 solo si producción corre ese commit. El motor ya no da por desplegado un SHA solo porque el checklist pasa sobre lo que había antes. Dos despliegues del mismo repo en la misma máquina corren de uno en uno. Agrega a `RECETA` el contrato de salida. Un script de la receta o una prueba real que sale con 75 dice "no es el código; reintenta luego". El motor reintenta a los 15 min sin culpar al agente, y desplegar no hace rollback porque no cambió nada. Tres horas seguidas de 75 son atasco. Pruebas en la clase `DespliegueTest`. | python3 -m unittest tests.test_github_despliegue.DespliegueTest |

## Bloque I - en la PC los planes corren igual que en la Mac, y Muse trabaja en WSL
lane: gate
depende: H
aviso: en la PC los planes corren igual que en la Mac, y Muse puede trabajar dentro de WSL
prueba-real: python3 -m unittest tests.test_windows tests.test_windows_wsl
| Fila | Contenido | Acepta |
| --- | --- | --- |
| I.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. En Windows, `boot_id_sistema` lee la hora de arranque del sistema (`LastBootUpTime` de `Win32_OperatingSystem`) en lugar de calcularla de dos relojes. En la Mac y la Mini, dos lecturas de `kern.boottime` que difieren menos de 120 s son el mismo arranque. Una marca distinta sin reinicio no debe declarar "corte". Pruebas en `tests/test_windows_wsl.py`, clase `ArranqueTest`, con relojes falsos. | python3 -m unittest tests.test_windows_wsl.ArranqueTest |
| I.2 | En Windows, las Acepta, las pruebas reales, las sondas y las recetas corren con Git Bash, nunca con cmd ni con el `bash.exe` de WSL. La ruta es `C:\Program Files\Git\bin\bash.exe` por omisión, y la clave `bash` de `maquina.toml` la cambia. Dentro de esos comandos, `python` y `python3` apuntan a un intérprete real, no al acceso directo de la Tienda. Pruebas en la clase `ShellTest`. En la PC, `test -f`, `"$(...)"` y `python3 -c` funcionan. | python3 -m unittest tests.test_windows_wsl.ShellTest |
| I.3 | Crea las carpetas de trabajo con `git worktree add --relative-paths`, y pon `worktree.useRelativePaths=true` en cada espejo, así el git de WSL las reconoce. Las carpetas que ya existen se convierten con `git worktree repair --relative-paths`. Prueba en la clase `WslTest`. Una carpeta creada por `Workspace.prepara` guarda en `.git` una ruta relativa. | python3 -m unittest tests.test_windows_wsl.WslTest |
| I.4 | Lanza a Muse con `argv_wsl` y el guardián `GUARDIAN_WSL`, que lo mata al pasarse del tope. Arma `WSLENV` con `env_wsl` para que pasen `MOTOR_*`, `META_API_KEY` y el PATH con el filtro de `gh`. `motor doctor` usa el mismo constructor de lanzamiento que `start_attempt`. Pruebas en la clase `WslTest`. | python3 -m unittest tests.test_windows_wsl.WslTest |

## Bloque J - la versión final en las tres máquinas, probada en cada una
lane: release
depende: I
aviso: la versión nueva del motor ya corre en la Mac, la Mini y la PC, y la probé en las tres
prueba-real: bash ops/prueba-motor-02.sh
despliegue: desplegar=ops/instala-todo.sh checklist=ops/huellas.sh rollback=ops/desinstala-todo.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| J.1 | Trabaja con poteto-mode de pstack. Antes de tocar código, lee `/Users/dn/.motor/conf/pstack-motor/motor.md`, que manda donde choca con poteto-mode, y el mapa de tu CLI en `/Users/dn/.motor/conf/pstack-motor/<tu cli>.md`. Después carga `/Users/dn/.motor/conf/pstack/skills/poteto-mode/SKILL.md`, abre el playbook que toca (Bug fix para cada arreglo, Refactoring para mover o borrar código, Feature para lo nuevo) y copia sus pasos a tu lista de tareas. Al terminar, nombra en `resumen` el playbook que seguiste. Sube la etiqueta por omisión de `ops/lib.sh` a la siguiente de la última publicada. Ajusta `ops/tests/test-instala.sh` a la etiqueta nueva. | bash ops/tests/test-instala.sh |
| J.2 | Escribe `ops/prueba-motor-02.sh`. Comprueba en las tres máquinas que `motor doctor --como-servicio` da ok y que el daemon corre la etiqueta nueva sin que nadie lo haya reiniciado a mano. Deja un archivo roto en la bandeja de cada máquina y comprueba que termina en `inbox/rechazados/` y que el daemon sigue vivo. En la PC, comprueba que una Acepta `test -f README.md && python3 -c "print(1)"` sale 0 por la ruta de I.2, y que `wsl.exe -e git -C <carpeta de trabajo nueva> status` sale 0. El script limpia lo que creó, sale distinto de 0 si algo falla y dice qué. | bash -n ops/prueba-motor-02.sh |

<!-- Fuera del plan, por la regla de David del 2026-10-21 de no ponernos candados a nosotros mismos:
- Pasarle a cada agente solo sus propios secretos.
- Verificar quién deja una orden en un buzón.
- Cerrar el filtro de `gh` contra `-R` y `gh api graphql`. Si un agente mergea por su cuenta, se salta
  la revisión y las pruebas; el motor lo adopta y lo avisa (F.4).

También fuera del plan:
- Que `fast` y `gate` se comporten distinto en todo. Hoy difieren en la exigencia de PSTACK 01, y
  así se queda.
- Ordenar despliegues del mismo repo entre máquinas distintas. H.5 los ordena dentro de una
  máquina. Entre máquinas queda como riesgo: dos planes de release del mismo repo en dos máquinas
  pueden dejar producción en el commit más viejo. -->
