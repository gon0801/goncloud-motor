# Revisión: quién trabaja cada parte

Revisé la elección de modelo del motor (`motor/motor/machine.py`, `relevo.py`, `daemon.py`, `plan.py`, `conf.py`, `effects.py`) contra la conf que usan las tres máquinas (`conf/modelos.toml`, `conf/rutas.toml`, `conf/general.toml`, `conf/papeles.toml`).

Corrí `python3 -m unittest tests.test_puro tests.test_relevo tests.test_requiere` en `motor/`: 115 pruebas, OK. Esas pruebas no cubren los escenarios de abajo. Los escenarios los corrí con `Policy.from_conf(load_conf(conf))` y `machine.decide` (funciones puras, sin daemon y sin escribir archivos). No corrí `tests.test_motor_e2e`.

La conf real carga así: implementar = sonnet → k3 → glm → grok, revisar = opus → k3 → glm → grok, cada cuenta con `max_parallel` 4, `agentes_por_maquina` 4. Sonnet y Opus comparten la cuenta `anthropic`.

## Qué sí cumple

- Por omisión el primer implementador es Sonnet. Con la cuenta `anthropic` en pausa, el siguiente intento es k3 en ese mismo `decide`, sin aviso. Con 3 Sonnet ya corriendo y un lugar libre, el siguiente bloque sigue siendo Sonnet. Con 4, nadie arranca y no hay aviso de atorado.
- Cinco bloques listos arrancan cuatro Sonnet y el quinto espera, sin aviso. Si k3 ocupa los 4 lugares de la máquina, este plan no arranca y tampoco avisa. Si `anthropic` está en pausa, los 4 lugares los llena k3.
- Un plan con `implementa: qwen, muse` y `revisa: deepseek` solo usa esos. Con DeepSeek en pausa, la revisión espera y no entra otro. Un modelo sin ruta se rechaza al entregar con la lista `deepseek, glm, grok, k3, muse, opus, qwen, sonnet`. `implementa: grok` junto con `revisa: grok` se rechaza.
- Quien escribió la parte no la revisa. Sonnet falla y k3 entrega: los autores son `{sonnet, k3}` y revisa Opus. Si el plan puso a Opus a implementar y Opus entregó, revisa GLM.
- Dentro de un mismo plan, la pausa de `anthropic` saca a Sonnet y a Opus. El dato real `tests/datos/fallas/claude-cuota.txt` se clasifica como cuota con `reset_at` `2026-10-23T09:00:00Z`, y `effects.py` usa esa hora como fin de la pausa.
- Dos fallas del mismo modelo pasan al siguiente. Tres cortes dejan a Sonnet. Una falla, un corte y otra falla cuentan como dos y pasan a k3.

## Hallazgos

### 1. La revisión por omisión llama a Muse, y detrás pueden entrar DeepSeek y Qwen

**Severidad:** BLOQUEANTE. Rompe la regla 1 (el carril de revisar es Opus → k3 → GLM → Grok) y la regla 2 (muse, qwen y deepseek entran cuando el plan los nombra). También se contradice con `conf/modelos.toml:5` y `conf/rutas.toml:42`.

**Evidencia:** `motor/motor/machine.py:477-485`. Si el plan no trae `revisa:`, `revisores_para` arma la escalera con el carril de revisar, el de implementar y todas las llaves de `policy.cuenta`. Esas llaves son los ocho modelos, en el orden de `rutas.toml`: sonnet, opus, k3, glm, grok, muse, deepseek, qwen.

Escenario corrido: el bloque lo entregó Grok y las cuentas `anthropic`, `kimi`, `zai` y `xai` están en pausa. La escalera queda `('opus', 'k3', 'glm', 'sonnet', 'muse', 'deepseek', 'qwen')` y `decide` arranca a Muse. DeepSeek y Qwen son los siguientes. El mismo escenario, del lado de implementar, no arranca a nadie: se espera a que vuelva una cuenta del carril `codigo`.

`conf/modelos.toml:4` dice que, si no hay nadie del carril de revisar, revisa cualquier modelo que no haya trabajado el bloque. La línea siguiente dice que Muse se queda fuera hasta que David lo pida. `conf/rutas.toml:42` dice que Qwen y DeepSeek solo entran si el plan los pide. El código obedece la línea 4 y se salta la 5.

**Cambio:** en `revisores_para`, el fallback son los modelos que ya están en algún carril (así Sonnet puede revisar si no escribió y el carril de revisar no puede). Muse, Qwen y DeepSeek solo si vienen en `revisa:`. Si ninguna cuenta de esos carriles puede, se espera, igual que ya hace implementar, y sin aviso de atorado. Hay que reescribir `conf/modelos.toml:4` (sale de `conf/scripts/genera_modelos.py`) para que diga eso: hoy esa línea y la siguiente se contradicen.

La regla 5, leída como "cualquier ruta del archivo", pediría lo contrario: abrir también la escalera de implementar cuando el carril entero está en pausa. Dentro del carril la regla 5 ya se cumple (con `anthropic` en pausa entran 4 de k3). Dejar el fallback como está rompe la regla 1 en cada plan que se quede sin el carril de revisar.

### 2. Avisa "atorado" mientras una cuenta va a volver sola

**Severidad:** BLOQUEANTE. Rompe la regla 5: esperar turno no genera aviso de atorado. El plan no está parado; el aviso dice que ningún agente pudo.

**Evidencia:** `motor/motor/machine.py:782-830`. `_sin_agente` se queda callado solo si todas las cuentas de la escalera tienen cooldown, si la máquina está llena, o si alguna cuenta de la escalera está al `max_parallel`. La cuarentena no entra en esa cuenta. Una pausa de una sola cuenta tampoco, si otro modelo de la escalera está fuera por otro motivo.

Dos escenarios corridos con la conf real:

- k3, GLM y Grok llevan dos fallas cada uno, y `anthropic` está en pausa 30 minutos. `decide` no arranca a nadie y emite `atorado:A`. Al cumplirse la pausa, Sonnet puede tomar el bloque (sus fallas van en cero). El aviso ya salió. El texto (`motor/motor/notify.py:36-39`) dice que ningún agente pudo y que Claude la va a revisar.
- Sonnet, k3, GLM y Grok terminaron en `auth` hace 100 segundos. Los cuatro quedan en cuarentena una hora (`relevo.CUARENTENA_REINTENTO_S`). `decide` emite `atorado:A`. La auth no suma en la racha de dos fallas (`relevo.LAS_RESUELVE_EL_RELOJ`). Pasada la hora, Sonnet vuelve a ser elegible. La prueba `test_pasada_la_hora_la_cuenta_vuelve_a_probarse` ya fija esa vuelta; no fija que el aviso no salga durante la hora.

El caso de la máquina llena sí está bien: cuatro lugares ocupados, cero avisos.

**Cambio:** avisar `atorado` solo cuando ningún modelo de la escalera puede volver por reloj (cooldown o cuarentena) ni por un lugar libre. Si la escalera está vacía, o todos están fuera por la racha de dos fallas, el aviso sí corresponde. La cuarentena se consulta con la misma `_cuarentena` que ya usa `_elige_pasada`.

### 3. La pausa de cuota de un plan no frena a los otros planes de la máquina

**Severidad:** BLOQUEANTE. Rompe la regla 4. La cuota es de la cuenta. Si `anthropic` se quedó sin cupo, ni Sonnet ni Opus entran hasta la hora de renovación, en ningún plan de esa máquina.

**Evidencia:** el `Cooldown` se guarda en el log del plan que recibió el error (`motor/motor/effects.py:347-364`, `cuenta` = `policy.cuenta[modelo]`). `fold` solo lee los eventos de ese plan (`motor/motor/machine.py:185-186`, `daemon.py:277`). `Daemon.tick` le pasa a `decide` los agentes que corren en los otros planes (`daemon.py:299-309`) y no les pasa las pausas.

Escenario corrido: el plan A tiene `Cooldown` de `anthropic` vigente. `_ocupados` de ese plan es `{}` (ya no hay nadie corriendo). `decide` del plan B, con ese mapa como `externos`, arranca Sonnet.

La cuarentena de credenciales tiene la misma forma: `_cuarentena` (`machine.py:488-509`) solo mira los intentos del snapshot de un plan. No corrí un segundo escenario para la auth entre planes; el código es el mismo hueco.

Dentro de un solo plan la regla 4 sí se cumple: con `anthropic` en pausa, la revisión elige k3 y deja fuera a Opus.

**Cambio:** en `tick`, juntar por cuenta el `hasta` máximo de los `Cooldown` de todos los planes y pasarlo a `decide` junto con los ocupados. Para la cuarentena, mirar los intentos `auth` de todos los planes de la máquina antes de elegir. Un éxito que levanta la cuarentena también tiene que verse entre planes, si no la cuenta queda presa en un plan y libre en el otro.

### 4. `revisa: sonnet` sin `implementa:` se acepta y el bloque se atora

**Severidad:** BLOQUEANTE. Rompe la regla 2 (`revisa:` trae al menos un modelo que no esté entre quienes implementan) y deja el bloque parado.

**Evidencia:** el rechazo está en `motor/motor/plan.py:327-331` y solo corre cuando las dos líneas vienen. `Policy.para` (`machine.py:47-54`) con `revisa:` y sin `implementa:` fija la revisión y deja el carril `codigo` para implementar.

Escenario corrido, conf real, sin `implementa:`:

- `revisa: sonnet` pasa `parse_plan`. Sonnet entrega (es el primero del carril). `revisores_para` devuelve `()` y `decide` emite `atorado:A`. Nadie más puede revisar: la lista fija era solo el autor.
- `implementa: grok` y `revisa: grok` sí se rechaza, con el texto de "nadie revisa su propio trabajo".
- `revisa: grok` a solas se acepta. Mientras escribe Sonnet, Grok revisa. Si el relevo llega a Grok, Grok es autor, la lista fija queda vacía y el bloque se atora igual. No corrí ese relevo entero; sale de `autores()` (`machine.py:150-152`) más `revision_fija`.

El daemon, al tomar el plan, vuelve a mirar los modelos desconocidos (`daemon.py:160-161`) y no vuelve a mirar este cruce.

**Cambio:** al entregar, con la conf ya cargada, la lista efectiva de implementar es `implementa:` o, si viene vacía, el carril `codigo`. Si `revisa:` es un subconjunto de esa lista, rechazar con el mismo error de hoy. `revisa: sonnet, opus` sigue valiendo: Opus no está en el carril `codigo`. `revisa: sonnet` y `revisa: grok` a solas dejan de valer.

### 5. Un corte de luz, después de una cuarentena ya vencida, aparta otra vez a toda la cuenta

**Severidad:** BLOQUEANTE. Rompe la regla 6 en ese estado: el corte no es una falla, y aquí saca a Sonnet y a Opus durante una hora más.

**Evidencia:** `motor/motor/machine.py:500-508` y `motor/motor/relevo.py:158-161`. `rearma_cuarentena` devuelve verdadero para cualquier cosa que no sea un freno corto, incluido `None`. `desde_kind("corte")` es `None`. Si ya hubo un `auth`, el corte pisa `desde` con la hora del corte.

El camino común está bien y tiene prueba: tres cortes seguidos, sin auth previa, siguen en Sonnet (`test_un_corte_de_luz_no_cuenta_como_falla`). Lo corrí otra vez con la conf real y dio Sonnet.

El otro camino, también corrido: `auth` de Sonnet hace más de una hora (la cuarentena ya venció) y después un `corte`. `_cuarentena` de `anthropic` queda verdadera y `decide` elige k3. Opus queda fuera junto con Sonnet, porque la cuarentena es de la cuenta. Un corte no está en `NO_ES_FALLA` a la hora de rearma; `NO_ES_FALLA` solo se usa en la racha de dos fallas (`machine.py:97` y `575`).

**Cambio:** en `_cuarentena`, un intento cuyo `kind` está en `NO_ES_FALLA` no mueve `desde`. `rearma_cuarentena` devuelve falso si no hay falla. El corte sigue sin sumar en la racha, como ya hace.

## No bloquean

### El primer "sin avance" reintenta el mismo modelo

`conf/general.toml:5` dice que a los 45 minutos sin cambios se detiene y entra otro. `sin_avance` se traduce a `Falla.TIMEOUT` (`relevo.py:91`) y un timeout no releva (`relevo.py:36-37`). Lo corrí: un `sin_avance` vuelve a elegir Sonnet; dos tiempos seguidos pasan a k3. La regla 6 pide dos fallas para cambiar, y eso sí se cumple. El comentario de la conf promete el relevo al primero. O se reescribe el comentario, o un solo `sin_avance` entra en `AVANZAN`.

### Si el mensaje de cuota no trae hora, la pausa son 5 horas

Con el dato real de Claude, `reset_at` se respeta (`effects.py:347-352`). Si `reset_at` viene vacío, `_iso_a_epoch` devuelve 0 y la pausa es `cooldown_default_s` (18000 s, cinco horas en `conf/general.toml:6`). `relevo.COOLDOWN_CUOTA_S` (15 minutos) no se usa en ese camino. Lo comprobé con `clasifica("claude", "You have reached your weekly usage limit")`: `reset_at` queda `""`. No tengo en `tests/datos/fallas/` un mensaje real de k3, GLM o Grok sin hora; no puedo decir qué pausa les tocaría en la práctica.

### La conf de ejemplo y las pruebas no fijan el tope de 4

`motor/conf-ejemplo/rutas.toml` no trae `max_parallel`; el default de `conf.py:91` es 1. `motor/conf-ejemplo/general.toml` no trae `agentes_por_maquina`; el default es 0, sin tope de máquina. La conf real sí tiene 4 y 4, y `Policy.from_conf` los lee. `TopesPorMaquinaTest` ejercita topes de 1 y 2. Un cambio que borre `max_parallel` de `conf/rutas.toml` deja la cuenta en 1 sin que esas pruebas fallen. Conviene una prueba que cargue `conf/` y fije los dos 4.

### Un daemon que muere entre lanzar y anotar deja el intento fuera del conteo

No lo ejecuté. En `effects.py:174-175` y `239-256`, `start_attempt` escribe `started` y lanza el proceso antes de agregar `AttemptStarted`. Si el proceso muere en medio, al volver el archivo ya existe, la función regresa sin anotar, y `decide` no ve a ese agente: no cuenta para el tope de 4 y el bloque no tiene intento abierto. Hace falta anotar el `AttemptStarted` antes de lanzar, o recuperar el intento desde `started` al arrancar.

VEREDICTO: CAMBIOS
