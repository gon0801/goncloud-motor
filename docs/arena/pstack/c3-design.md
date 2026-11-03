# Candidato 3: pstack obligatorio, el brief lo pide y el motor lee el rastro

## Problem

Hoy el motor solo "pide" pstack: ni `reglas/encargo.md` ni `revisor.md` lo mencionan, ninguna ruta de `rutas.toml` pasa un flag, y en los intentos reales Sonnet y Kimi no cargaron poteto-mode aunque lo tenían disponible. La Mini y la PC ni siquiera lo tienen instalado (hechos 5). Seis CLIs cargan skills de seis formas distintas, y algunos (Grok) no tienen herramienta de skill. Restricciones que el diseño respeta: el tick es código (nada de vigías), la conf llega sola y solo si pasa `motor conf valida`, cada máquina difiere solo en `maquina.toml`, y cualquier modelo puede pedirse en un plan.

## Usage (caller's view)

Para David y para quien escribe planes no cambia nada: el plan sigue diciendo `implementa: k3` y `revisa: opus`. Lo que ve el agente, al inicio de todo `brief.md`, sin importar CLI ni máquina:

    ## Método obligatorio
    Antes de tocar nada, lee COMPLETO /Users/dn/.agents/skills/poteto-mode/SKILL.md y síguelo.
    Si tu CLI tiene herramienta de skill, úsala con "poteto-mode"; si no, léelo como archivo.
    Esta entrega se rechaza si el motor no ve esa lectura en tu rastro.

Lo que ve el motor, dentro de `collect` (un solo punto nuevo, justo antes de leer `result.json`):

    ev = rastro.leer(att.cli, rastro_fuente(att), pstack_dir)       # Evidencia
    if (k := rastro.veredicto(ev, exigencia(plan, block, att.role))) is not None:
        return {"kind": "sin_pstack", "detalle": k}                 # cuenta como falla; relevo normal

Lo que ve David en el aviso/ledger: `pstack: cargado (skill) · 2 subagentes` o `pstack: no verificable (qwen)`.

## Shape

Tres piezas pequeñas y una regla, sin tocar la máquina de estados.

1. **Una sola instalación, fijada en la conf.** `pstack.toml` en goncloud-motor-conf: `{ origen = "cursor/plugins", sha = "190d287…", skills_extra = [...] }`. El script `scripts/pstack-instalar.sh` (la versión fijada de `sincronizar-pstack.sh`: baja ese SHA, nunca `main`) deja una copia en `<pstack_dir>/pstack-oficial` y los enlaces que cada CLI necesita: `~/.agents/skills` (opencode, kimi, muse, grok, qwen y Claude por enlaces), `~/.claude/{skills,agents}`, `~/.grok/skills`, `~/.qwen/skills`. En Windows usa junctions/copia y en WSL su propia copia para Muse. `pstack_dir` sale de `maquina.toml`. Escribe `ORIGEN` con el SHA. Instalar y actualizar es un plan del motor por máquina ("pstack-instalar", un bloque por máquina, `Acepta` = `motor pstack comprueba`). Subir de versión = cambiar el SHA en la conf y entregar ese mismo plan otra vez.

2. **Candado de coherencia en código.** `start_attempt` llama `pstack_listo(maquina, conf)` (pura: ¿`ORIGEN` == SHA de la conf?). Si no, no lanza y avisa una vez por Telegram. Así "idéntico en las tres" es una condición que el motor exige, no un deseo, y una conf nueva con otro SHA detiene los intentos hasta que el plan de instalación corra. (Mientras no exista la instalación, el flag `pstack.exigir=false` de la conf permite arrancar; se enciende cuando las tres tengan ORIGEN.)

3. **El texto se inyecta siempre; la skill es solo un camino de lectura.** El brief lleva el bloque "Método obligatorio" con la ruta ABSOLUTA del SKILL.md de esa máquina (`pstack_dir`), para implementar y para revisar. Esto resuelve la pregunta 4: un CLI sin herramienta de skill (Grok) o con skills deshabilitadas igual puede leer el archivo; ningún CLI queda fuera. Misma skill para los dos roles: poteto-mode decide el playbook (revisar es un playbook suyo). Los subagentes los nombra poteto-mode (`poteto-agent`, `comment-sicko`); el motor no les asigna.

4. **Verificación por rastro, un lector por CLI, todos puros.** `rastro.py` convierte la fuente del rastro en `Evidencia(cargo_skill, subagentes, comandos, fuente)`. Cargar cuenta si hay un `Skill`/`skill`/`read_skill` con nombre `poteto-mode` O una lectura de una ruta que termine en `poteto-mode/SKILL.md` (acepta las dos formas, hecho 3 nota Claude). Fuentes: Claude, transcript `~/.claude/projects/<cwd>/<uuid>.jsonl` con `--session-id {sesion}` que el motor genera y guarda en el intento (resuelve el log con 13 sesiones mezcladas); Kimi, `log.txt` ya en stream-json; opencode, se agrega `--format json` a la ruta y se lee `log.txt`; Grok, `updates.jsonl` por `sessionId` del JSON final (carga = `read_file` del SKILL.md, subagente = `spawn_subagent`); Muse, `session.jsonl` por `--session-id`; Qwen, `--json-file {rastro}`. Cada lector devuelve `None` si la fuente no existe o no tiene ni un evento reconocible: eso es "no verificable", NO rechazo. Así un cambio de formato tras una actualización del CLI no genera un bucle de rechazos falsos (dueño de esto: la alerta "no verificable" pasa a Telegram una vez por CLI y por día).

5. **Qué hace el motor al no verse.** `sin_pstack` es un kind nuevo: el bloque queda en ARREGLAR, el feedback dice "no cargaste poteto-mode: léelo completo en <ruta>", y cuenta como falla del modelo (NO está en `NO_ES_FALLA`), así que dos seguidas disparan el relevo existente (`fallas_seguidas_max`). Sin cambios en `relevo.py` salvo agregar `sin_pstack` a `DE_KIND` como falla de obediencia. Para el revisor es igual: sin lectura, el veredicto no se registra y se pide otro revisor (sigue valiendo "revisa un modelo que no escribió la parte").

6. **Costo por carril, en conf, no en código.** `pstack.toml` trae una tabla `[exigencia]` con `carril × rol → {skill: bool, min_subagentes: int}`. Arranque: todo exige `skill=true`; `min_subagentes=0` en todas las celdas, salvo `codigo.implementar=1` una vez medidos 2-3 intentos reales. Lane fast (solo docs/chore/cierre) exige solo cargar la skill, nunca subagentes; el revisor tampoco lo exige. Pocos subagentes obligatorios porque poteto-mode puede decidir con razón que no hacen falta y no se debe castigar eso.

Qué es verificable y qué no: mecánico = carga de la skill, número y tipo de subagentes, comandos ejecutados. Palabra del agente = elegir bien el playbook y seguirlo completo; el motor no lo finge: el resumen sigue sin contar como prueba (effects.py:308) y las pruebas `Acepta` siguen siendo la defensa real. El brief pide además el listado de skills y subagentes en `resumen`, solo informativo.

Profundidad de interfaz: la superficie nueva es `rastro.leer`, `rastro.veredicto`, `pstack_listo`, un kind y dos tablas de conf. Esconden seis formatos de rastro y la regla de equivalencia (Skill o lectura). `machine.py` no se entera de pstack.

## Synthesis decision

(Lo llena arena.)

## Tradeoffs accepted

- Aceptamos que "cargó la skill" sea una prueba de intención, no de cumplimiento, a cambio de no usar ningún modelo para vigilar y de un chequeo de segundos en código.
- Aceptamos pinear un SHA y actualizar con un plan a mano (rápido de hacer, pero no automático) a cambio de que las tres máquinas sean idénticas y una actualización rota de upstream no tumbe el motor a las 04:10.
- Aceptamos que "no verificable" pase sin rechazo, a cambio de que un cambio de formato del CLI no cause bucles de rechazo.
- Aceptamos `min_subagentes=0` al inicio: se mide antes de exigir.
- No usamos flags específicos de pstack por CLI (`--plugin-dir`, `--skills-dir`, `--agent`): cada uno sustituye descubrimiento y es una rama más por CLI; basta el texto del brief y el enlace de skills.

## Alternatives considered

- **Hook/flags por CLI que inyecten la skill** (`--append-system-prompt`, `--rules`, `--skills-dir`): más fuerte para el modelo, pero 4 mecanismos distintos, Kimi y opencode sin prompt de sistema, y `--skills-dir` sustituye el descubrimiento. Mucha superficie en rutas.toml por poca ganancia sobre un brief que ya lee.
- **Pegar el texto completo de poteto-mode en el brief**: robusto para CLIs sin skills pero infla cada encargo y se desfasa de la versión fijada; pierde frente a apuntar a la ruta única. Queda como plan B por CLI si un lector falla.
- **Pedir solo la declaración del agente en `resultado.json`** (como los mapas de plataforma): costo cero pero es exactamente "pedirlo"; la tarea exige comprobar.
- **Un vigía que lea los rastros en vivo**: prohibido por restricción.

## Open questions and risks

- Riesgo mayor: los lectores de rastro dependen de rutas de archivos y formatos de seis CLIs que se actualizan seguido (Kimi ya va por 2.1.1 vs 2.2.0 entre máquinas); ¿aceptas la alerta "no verificable" como contrapeso o prefieres congelar también las versiones de CLI?
- ¿Qwen expone la llamada a skill en `--json-file`? Sin medirlo queda "no verificable"; hay que probarlo en un intento real antes de exigir.
- Grok y la ruta: ¿el `sessionId` del JSON final siempre llega aunque el proceso termine por timeout? Si no, el intento no tiene rastro.
- ¿`motor pstack comprueba` debe bloquear también cuando la conf cambia el SHA a medio plan? (Propuesta: la conf del plan se fijó al tomarlo; el candado usa esa.)
- Windows: ¿junctions bastan para `.agents/skills` o hay que copiar? Se resuelve en el primer plan de instalación.
- Planes necesarios: (a) `pstack-instalar` por máquina, Mac/Mini/PC (+copia en WSL), antes de encender `exigir`; (b) un plan de código en el motor con rastro.py, el candado, el kind y los placeholders `{sesion}`/`{rastro}`; (c) un plan de conf con `pstack.toml`, las dos reglas nuevas y los cambios en `rutas.toml`.

## Next implementation step

Escribir `rastro.py` con el lector de Kimi y el de Claude (los dos con rastro ya medido) y sus pruebas contra los log.txt/transcripts reales de `pstack-aviso-claw`, que deben dar "no cargó" en ese caso.
