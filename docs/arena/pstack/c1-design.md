# Candidato 1: el motor lee la huella del agente

## Problema

David quiere que todo agente que lance el motor (implementar y revisar, en la Mac, la Mini y la PC,
con cualquier modelo) trabaje con pstack y poteto-mode, y que el motor lo haga cumplir. Hoy no pasa:
en `pstack-aviso-claw` ni Sonnet ni Kimi cargaron poteto-mode, aunque en la Mac lo tenían instalado y
el hook se lo pidió (hechos §4). En la Mini y la PC ni siquiera está instalado (hechos §5).

Lo que hace que la forma no sea obvia:
- El motor no usa modelos para vigilar. Cualquier comprobación tiene que ser código sobre algo que el
  agente deja en disco.
- Seis CLIs con seis formas de cargar skills, de lanzar subagentes y de dejar registro (hechos §3, §4).
  Grok ni siquiera tiene herramienta de skills.
- Lo que el agente *dice* que hizo no cuenta (`effects.py:308`). El campo `resumen` es palabra.
- poteto-mode choca con el contrato del motor: su playbook "Opening a PR" abre PRs listos y su
  Autonomía pide pausar antes de desplegar. Bajo el motor, el agente solo hace push y nunca pausa.
- La conf compartida es el único canal que ya llega igual a las tres máquinas, fijado y validado.

## Uso (vista de quien llama)

**El agente** recibe en su encargo una sección nueva, generada de la misma exigencia con la que luego
se le juzga:

```
## Cómo trabajas (obligatorio; el motor lo comprueba en tu registro de herramientas)
1. Lee /Users/gon/.motor/pstack/3f9a1c…/pstack-motor/motor.md y …/pstack-motor/kimi.md.
2. Carga poteto-mode leyendo …/pstack/skills/poteto-mode/SKILL.md antes de tu acción número 6.
3. Elige un playbook de poteto-mode, ábrelo y copia sus pasos a tu lista de tareas.
4. Carga y sigue deslop y no-comments antes de tu último commit.
5. Delega al menos una parte del trabajo a un subagente tuyo (no a otro programa).
El motor revisa tu registro de herramientas, no tu resumen.
```

**Quien mantiene la conf** (`goncloud-motor-conf`) edita dos archivos:

```toml
# pstack.toml
[puerta]
llamadas = 6                       # poteto-mode antes de la acción 6 o se corta el intento

[formatos]                         # cómo se lee el registro de cada CLI; "observa" solo en el canario
claude-stream = "exige"
kimi-stream = "exige"
opencode-json = "observa"
grok-acp = "observa"
muse-jsonl = "observa"

[exige.implementar.gate]           # release igual que gate
skills = ["poteto-mode", "deslop", "no-comments"]
playbook = true
todos = true
subagentes_min = 1
comandos_min = 1

[exige.implementar.fast]           # solo documentos
skills = ["poteto-mode", "unslop", "technical-writing"]
playbook = false
todos = true
subagentes_min = 0
comandos_min = 1

[exige.revisar.gate]
skills = ["poteto-mode", "interrogate"]
playbook = false
todos = false
subagentes_min = 2
comandos_min = 1

[exige.revisar.fast]
skills = ["poteto-mode", "unslop"]
playbook = false
todos = false
subagentes_min = 0
comandos_min = 0
```

```toml
# rutas.toml: cada ruta nombra el formato de su registro y usa {pstack} y {sesion}
[rutas.sonnet]
huella = "claude-stream"
argv = ["claude", "-p", "--model", "sonnet", "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json", "--verbose", "--session-id", "{sesion}",
        "--plugin-dir", "{pstack}/pstack", "{prompt}"]

[rutas.k3]
huella = "kimi-stream"
argv = ["kimi", "-p", "{prompt}", "--output-format", "stream-json", "--skills-dir", "{pstack}/pstack/skills"]

[rutas.glm]
huella = "opencode-json"
argv = ["opencode", "run", "--auto", "--format", "json", "-m", "zai-coding-plan/glm-5.3", "{prompt}"]

[rutas.grok]
huella = "grok-acp"
argv = ["grok", "--prompt-file", "{brief}", "--always-approve", "--output-format", "streaming-json",
        "--session-id", "{sesion}"]

[rutas.muse]
huella = "muse-jsonl"
argv = ["muse", "exec", "--yolo", "--json", "--trust-workspace", "--session-id", "{sesion}",
        "--prompt-file", "{brief}"]
```

**El motor** (dos llamadas nuevas, nada más):

```python
# effects.start_attempt, antes de armar argv
pi = self.prepara_pstack(d, a.role, block.lane, ruta)      # copia fija, sesión, exigencia -> pstack.json
argv = [x.format(brief=…, prompt=…, result=…, pstack=str(pi.raiz), sesion=pi.sesion) for x in ruta.argv]
brief += pstack.instrucciones(pi.exigencia, pi.raiz, ruta.cli)

# effects.collect, justo antes de devolver "entregado" o de anotar "Reviewed"
if (r := self.juzga_pstack(d)) is not None:
    return r                                                 # {"kind": "sin_pstack", "detalle": …}
```

**David** no ve nada nuevo mientras todo va bien. Si un modelo no usa pstack dos veces seguidas, entra
el siguiente de la escalera, como con cualquier otra falla.

## Forma

### Datos primero

Una sola idea: **la Huella**. Es lo que el agente hizo según su propio registro de herramientas,
reducido a cinco señales que sí se pueden probar (`Senal`: skill, playbook, todos, subagente,
comando). Se deriva de `log.txt`, que ya es del motor (`runwrap.py:58`), nunca de lo que el agente
escribe en `result.json`. Así no hay nada que el agente "declare" y que haya que sincronizar con la
realidad (per derive instead of sync).

Del lado de la política, **la Exigencia**: el piso por (papel, carril). Vive en `pstack.toml` de la
conf compartida y hace dos trabajos con el mismo valor: escribe la sección "Cómo trabajas" del encargo
y juzga la huella. Lo que se pide y lo que se comprueba no se pueden separar.

Entre las dos, **la tabla FORMATOS**: una fila por registro de CLI (qué herramienta carga skills, cuál
lee archivos, cuál lanza subagentes…). Agregar un CLI es una fila, un mapa `pstack-motor/<cli>.md` y
fixtures medidos (per model-the-domain: una tabla, no `if cli == …` regados).

### Cómo fluye

```
conf (pstack/ + pstack-motor/ + pstack.toml) ──15 min──► cada máquina, solo si pasa `conf valida`
        │
start_attempt: asegura_pstack -> <home>/pstack/<árbol>/  (git archive, idempotente, inmutable)
               prepara_pstack -> <intento>/pstack.json   (árbol, sesión uuid5, formato, exigencia)
               argv con {pstack} {sesion}; encargo + instrucciones(exigencia)
        │
runwrap: corre al agente; lee log.txt cada 0.2 s; si pasa la acción N sin poteto-mode -> corta
        │       (outcome.json lleva "puerta")
collect: classify -> "sin_pstack" si cortó la puerta
         result.json, SHA (como hoy) -> juzga(huella(log.txt), exigencia del pstack.json)
         -> "sin_pstack" con lo que faltó, o "entregado"/"veredicto"; huella.json queda para auditar
```

Tres archivos para seguir el dato: `pstack.py` (todo el conocimiento), `effects.py` (dos métodos),
`runwrap.py` (la puerta en vivo). `machine.py` y `relevo.py` cambian una línea cada uno.

### Interfaz

La superficie pública de `pstack.py` para el resto del motor son cuatro funciones puras: `huella`,
`juzga`, `puerta` e `instrucciones`, más `parse_conf` y `valida` para la conf. Detrás quedan los seis
formatos de registro, las rutas con `/`, `\` o `/mnt/c/` de WSL, el filtro por sesión, la diferencia
entre `Skill("pstack:poteto-mode")` y `Read(…/poteto-mode/SKILL.md)`, y la regla de que solo cuenta la
copia fija de este intento. `effects` no sabe qué CLI es cuál. Profundidad alta: el que llama pasa un
log y una exigencia y recibe "cumple" o una lista de faltas en palabras llanas.

### 1. Cómo llega pstack a cada CLI y cómo se mantiene igual

- **Una copia, dentro de la conf compartida.** `goncloud-motor-conf/pstack/` es la copia exacta de
  `cursor/plugins` en un commit (hoy `190d287`, MIT). La genera `scripts/trae_pstack.py <sha>` con la
  misma transformación de `sincronizar-pstack.sh` (`disable-model-invocation: false`, nombres de
  carpeta). Al lado, `pstack-motor/` con lo propio del motor, que nunca se pisa al actualizar.
- **La versión es el árbol git.** `git rev-parse HEAD:pstack` + `HEAD:pstack-motor` dan el id. El
  motor lo materializa en `<home>/pstack/<árbol>/` con `git archive` y un rename atómico. Inmutable:
  un intento largo no ve cambiar sus skills aunque la conf avance. Las copias viejas se borran con los
  registros de 14 días.
- **Cada CLI la recibe por su flag cuando lo tiene**, y si no, por ruta absoluta en el encargo:
  Claude y DeepSeek `--plugin-dir {pstack}/pstack` (trae `.claude-plugin/`); Kimi `--skills-dir`
  (sustituye el autodescubrimiento: herméticamente la versión fijada); Muse `--trust-workspace` para
  poder delegar; opencode y Grok leen el `SKILL.md` por ruta absoluta.
- **Nada se instala a mano en las máquinas.** No hace falta `~/.agents` en la Mini ni en la PC. Lo
  que hay en la Mac (launchd `sincronizar-pstack`, hook `SessionStart`) sigue sirviendo a las sesiones
  interactivas de David y el motor no depende de eso.
- **Actualizar es un plan del motor** en `goncloud-motor-conf`: corre `trae_pstack.py <sha nuevo>`.
  `Acepta`: `python scripts/trae_pstack.py --comprueba` (regenera y compara byte por byte, así nadie
  edita a mano lo copiado) y `python -m motor conf valida .`. Al mergear, llega sola a las tres.

### 2. Cómo se le exige en cada encargo

- **La misma skill para los dos papeles: poteto-mode.** Lo que cambia es la exigencia (tabla de arriba).
  Quien implementa elige el playbook (Feature, Bug fix, Refactoring…), lo abre, copia sus pasos y
  delega al menos una parte (el paso 4 de Feature dice que la delegación es obligatoria). Quien revisa
  carga poteto-mode e `interrogate`, con al menos dos subagentes de solo lectura.
- **`pstack-motor/motor.md` pisa a poteto-mode donde choca con el motor.** "Opening a PR" = commit y
  push a tu rama; nunca abras, marques listo ni mergees (el shim de `gh` ya lo impide). Babysit y
  Shipping no aplican. "Always pause" no aplica: el plan es la autorización y desplegar es del motor.
  No hay a quién preguntarle.
- **Subagentes: solo los del propio CLI.** `poteto-agent` y `comment-sicko` (Claude los toma del
  plugin; los mapas por CLI dicen el nombre en cada uno). Nuevos shims `claude`, `kimi`, `opencode`,
  `grok`, `muse`, `qwen` en el `PATH` del agente salen con error: dentro de un encargo no se lanza
  otro agente por la terminal. Razones: la cuota se cuenta por cuenta y un panel de GLM lanzado por
  Muse gastaba la de Z.AI sin que el motor lo viera; y un `claude -p` anidado ensuciaba el registro
  (13 sesiones en `A-implementar-1/log.txt`). La diversidad de modelos ya la pone el revisor del motor,
  que por regla no es el autor. `runwrap` lanza al agente con la ruta real de su CLI, resuelta sin
  los shims.

### 3. Qué comprueba el motor y qué hace si no se usó

| Comprobable por código (en log.txt) | Solo palabra del agente |
|---|---|
| Cargó poteto-mode y cada skill pedida, por herramienta o leyendo el `SKILL.md` de la copia fija | Que siguió la skill completa y no solo la leyó |
| Abrió un playbook de poteto-mode | Que eligió el playbook correcto |
| Usó su lista de tareas | Que copió los pasos tal cual |
| Cuántos subagentes lanzó y de qué tipo | Que revisó lo que le entregaron |
| Que corrió comandos | Que el resultado es bueno (eso lo prueban `Acepta` y el revisor) |

- **Puerta temprana, en `runwrap`.** Si el agente llega a su acción 6 sin haber cargado poteto-mode, se
  corta ahí. Es el equivalente universal de un hook `PreToolUse`, pero sirve en los seis CLIs y no
  gasta horas de cuota en un intento que se iba a rechazar.
- **Juicio completo, en `collect`.** Después de validar el SHA (mentir es peor) y antes de
  "entregado". Para el revisor, antes de anotar el veredicto.
- **Si falta algo:** kind `sin_pstack` con la lista en palabras llanas. Va tal cual al siguiente
  encargo ("El intento anterior terminó por: sin_pstack (no lanzó ningún subagente; no cargó
  deslop)"). Los commits se quedan en la rama, así que el siguiente intento completa los pasos que
  faltan sobre lo hecho, no empieza de cero.
- **Cuenta para el relevo:** `DE_KIND["sin_pstack"] = TASK_FAILURE`, fuera de `NO_ES_FALLA`. Dos
  seguidas del mismo modelo y entra el siguiente de la escalera. Un modelo que no trabaja con pstack
  deja de recibir trabajo, que es lo que David pidió.
- **Se juzga contra la exigencia con la que se escribió el encargo** (guardada en `pstack.json`),
  aunque la conf cambie mientras el agente trabaja.
- **Huella ilegible no es culpa del agente.** Si el log trae salida y ninguna línea se pudo leer (el
  CLI cambió su formato), la entrega sigue a `Acepta` y al revisor, `huella.json` lo anota y sale un
  aviso una vez. Un error de lectura del motor no debe atorar todos los planes.

### 4. CLIs sin herramienta de skills

No hay que sacar a nadie por eso. Todos los CLIs pueden leer un archivo, y "cargar una skill" se
acepta como leer su `SKILL.md` en la copia fija (el adaptador oficial de Claude Code hace lo mismo).
Grok entra así.

El criterio real es otro: **¿puede el motor leer su registro?** Una ruta sin formato en `FORMATOS` no
pasa `conf valida`. Hoy eso deja fuera a **Qwen** hasta medir cómo registra la carga de una skill. No
está en los carriles; un plan que lo pida se rechaza al entregarlo con la lista de modelos que sí hay.

Rechazado: pegar el texto de poteto-mode en el encargo. No prueba nada, rompe las referencias
relativas a playbooks y principios, y agrega 20 KB a cada encargo.

### 5. Costo por carril

La exigencia es por carril, así que el costo es una decisión de datos en la conf:
- **fast (solo documentos):** poteto-mode, unslop y technical-writing, sin subagentes ni playbook. Son
  tres lecturas de archivo. Revisar un fast pide poteto-mode y unslop.
- **gate y release:** el piso completo, con al menos un subagente al implementar y dos al revisar.
- Subir o bajar un mínimo es un PR de una línea a `pstack.toml`, validado y aplicado en las tres
  máquinas a los 15 minutos.

### Qué no hace a propósito

No juzga la calidad del trabajo. No evalúa si el playbook elegido era el correcto. No lee sesiones en
el home de cada CLI (`~/.claude/projects`, `~/.grok/sessions`…): todo sale de `log.txt`, que está en la
carpeta del intento en las tres máquinas, también con Muse dentro de WSL.

## Decisión de síntesis

Pendiente: la llena arena al comparar candidatos.

## Tradeoffs aceptados

- Aceptamos copiar unos MB de skills de terceros (MIT) en el repo de la conf a cambio de usar el único
  canal que ya llega fijado, validado e idéntico a las tres máquinas.
- Aceptamos un piso mecánico (leyó, delegó, corrió comandos) que no prueba que siguió la skill a
  cambio de no tomar nunca la palabra del agente como evidencia. La calidad sigue en `Acepta` y en el
  revisor.
- Aceptamos rechazar entregas que quizá estaban bien a cambio de que la regla se cumpla. El costo baja
  porque los commits se quedan y la puerta corta temprano.
- Aceptamos paneles de una sola familia dentro de un intento (interrogate pierde diversidad) a cambio
  de cuota honesta por cuenta y un registro limpio. La diversidad la pone el revisor del motor.
- Aceptamos cambiar las rutas a salida en streaming. `fallas.clasifica` lee la cola de `log.txt` y
  ahora verá JSON por línea; las frases de error siguen ahí como texto, pero hay que medirlo.
- Aceptamos mantener un lector por CLI. Cuando un CLI cambie su formato, la huella sale ilegible y
  avisa, en vez de rechazar a todos los modelos.

## Alternativas consideradas

- **Pedirlo en `reglas/encargo.md` y que el agente lo declare en `result.json`.** Superficie mínima,
  pero no esconde nada: deja toda la prueba en la palabra del agente. Ya falló: Sonnet y Kimi ignoraron
  hasta el hook.
- **Hooks de cada CLI** (`PreToolUse` en Claude vía `--settings`). Previene en vez de detectar, pero
  solo existe en Claude (y quizá Qwen). Kimi, opencode, Grok y Muse quedarían sin cumplir. La puerta de
  runwrap es la versión que sirve para todos.
- **El motor corre cada paso del playbook como un intento aparte** (how, architect, implementar,
  deslop…). Obligaría por estructura, pero el motor tendría que elegir playbooks, que es juicio. Es lo
  que la regla "el motor no usa modelos" prohíbe, y multiplica intentos y cuota.
- **Instalar pstack en cada máquina con un sincronizador como el de la Mac**, más enlaces por CLI.
  Expone a cada máquina cinco formas de instalar, tres sistemas y WSL, y deriva entre ellas. La conf
  ya resuelve fijar, validar y repartir.
- **Leer las sesiones que cada CLI guarda en su home.** Más completo en Claude (subagentes), pero son
  seis rutas que dependen del usuario, del sistema y de WSL. `log.txt` es del motor en todos los casos.

## Preguntas abiertas y riesgos

- **Riesgo mayor: los formatos de opencode, Grok y Muse no están medidos.** Si su lector no reconoce
  una carga de skill que sí ocurrió, rechaza trabajo bueno, gasta la escalera y el plan se atora. Por
  eso nacen en modo `observa`, se miden en el canario de cada máquina, se guardan como fixtures en las
  pruebas del motor y solo entonces pasan a `exige`. ¿Te parece bien que ningún modelo de esos tres
  quede obligado hasta pasar su canario?
- ¿Aceptas que dentro de un encargo Muse ya no arme su panel con GLM por opencode? Su panel queda de
  dos o más hijos Muse; la otra familia la pone el revisor del motor.
- ¿El revisor de gate debe pagar `interrogate` con dos subagentes, o basta poteto-mode más comandos
  reales? Es lo que más sube el costo de una revisión.
- Cuatro intentos por cuenta, cada uno con sus subagentes, pueden pegarle al límite de velocidad de
  Anthropic. Se trata como cualquier `rate_limit`, pero ¿quieres un tope de subagentes escrito en
  `motor.md`?
- En la Mac, Claude también carga el hook y las skills de usuario; en la Mini y la PC no. ¿Igualamos
  las tres con `--setting-sources`? (`--bare` no sirve: no lee OAuth.)
- ¿Te parece bien que una huella ilegible deje pasar la entrega con aviso, en vez de atorar el plan?

## Plan del motor necesario: "PSTACK 01"

1. **A, goncloud-motor (gate).** `motor/pstack.py`, cambios en `conf`, `effects`, `runwrap`,
   `machine`, `relevo`, shims de agente ajeno y `conf valida`. Mientras la conf no traiga
   `pstack.toml`, no se exige nada. Prueba que discrimina: los registros reales de
   `pstack-aviso-claw` (Kimi `A-implementar-4/log.txt` y el transcript de Sonnet) dan `sin_pstack`;
   un registro que sí carga poteto-mode da "cumple". Nueva versión del motor.
2. **B, goncloud-motor-conf (gate).** `scripts/trae_pstack.py`, `pstack/` en `190d287`,
   `pstack-motor/` (motor.md más cinco mapas; se corrige el `--dir` que no existe en `opencode run`),
   `pstack.toml`, rutas con streaming y `{pstack}` `{sesion}`, y `[motor] version` fijada a la de A.
   `Acepta`: `--comprueba` y `conf valida`.
3. **C, canario en cada máquina (gate).** Un plan desechable por máquina con un bloque fast y uno gate,
   con `implementa:` y `revisa:` forzando cada CLI de esa máquina. Se guardan sus `log.txt` como
   fixtures en goncloud-motor y los formatos medidos pasan a `exige`.
4. **D, cierre (fast).** `pstack.toml` se vuelve obligatorio y se borra el camino sin pstack (per
   migrate-callers-then-delete-legacy-apis).

## Siguiente paso de implementación

Escribir `pstack.huella` y `pstack.juzga` contra el `log.txt` real de Kimi en `A-implementar-4`, que
debe dar `sin_pstack` con "no cargó poteto-mode".
