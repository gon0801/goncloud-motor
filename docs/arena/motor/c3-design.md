# Motor de planes: el estado vive en GitHub, el proceso local es desechable (candidato c3)

## Problem

David quiere UN motor de planes, instalado igual en MacBook, Mac Mini y PC Windows(+WSL), que corra
cada plan completo en la máquina donde se entregó, retome solo tras apagón, trabaje hasta probar con
datos reales y avise por un solo canal. Es multiproyecto. OpenClaw y Hermes quedan como "IA que
reparte": tareas chicas de solo lectura las resuelven, todo lo que cambie código se convierte en plan.

Lo no obvio: (1) `taller` ya acierta en el núcleo (daemon sin modelo, fold del log, relevo por
clasificación, MergeTicket), pero su estado de verdad es un SQLite local con un único escritor: si el
disco muere, el plan muere. (2) macOS guarda sesiones de claude/muse/cursor/gh en un llavero que está
bloqueado sin sesión gráfica; la MacBook tiene FileVault, así que tras un apagón NADA arranca hasta
que alguien teclee la contraseña. (3) La PC arranca el gateway solo al iniciar sesión y Kaspersky
intercepta TLS. (4) Req. 2 prohíbe repartir un plan entre máquinas, así que la resiliencia no puede
venir de "otra máquina lo toma". Debe venir de que el estado no dependa del disco.

Restricciones que el diseño respeta tal cual: req. 1-10 de base.md; sin tablero ni vigía; nunca
`--no-verify`; nunca push a la rama principal; sin secretos en repos públicos; el plan es la
autorización completa; nunca esperar por una prueba.

## Usage (caller's view)

Una sola herramienta, `motor`, mismo binario y mismos archivos en las tres máquinas. Cuatro verbos
para los clientes; todo con `--json` para IAs:

```
motor submit plan.md [--repo OWNER/REPO]   # valida, congela config, encola; imprime plan-id (idempotente por hash)
motor draft  --repo OWNER/REPO "texto"     # devuelve esqueleto de plan con filas y Acepta por llenar
motor status [plan-id]                      # una línea por bloque, leída de GitHub si el disco local no está
motor note   plan-id B "usa la función X"  # llega al siguiente encargo
motor hecho  plan-id E                      # David terminó un paso humano (login, 2fa, captcha, pago)
motor doctor [--headless]                   # CLIs, credenciales EN EL CONTEXTO DEL SERVICIO, hash de config
motor adopt  plan-id                        # tras reinstalar/perder disco: retoma desde GitHub (misma máquina)
```

**David** (Telegram, a claw o a Hermi): "arregla X en el repo Y". La IA responde con una línea, entrega
el plan y David recibe solo avisos de ≤8 líneas ("El plan 'costos' terminó y quedó probado con datos
reales."). Si quiere mirar, abre la app de GitHub: ve el issue de estado del plan (ver Shape).

**Lead (Mac)**: escribe el plan, `motor submit plans/x.md`, o `ssh mini motor submit ...` cuando
quiere correr sin depender de FileVault. No teclea en ventanas, no espera.

**IA que reparte (OpenClaw / Hermes)**: su skill `motor` (idéntica, generada) dice la regla de
enrutamiento y expone solo `motor draft|submit|status|note|hecho`. Ejemplo de llamada desde la skill:

```
$ motor submit /work/inbox/ajusta-umbral.md --json
{"plan":"ajusta-umbral-7f3c","host":"mini","estado":"encolado","aviso":"Recibí el plan y empieza en la Mini."}
```

### Escenario 1: plan entregado a Hermes durante un corte de luz
1. David manda el plan a Hermi. Hermi corre `motor submit` en la PC. Motor escribe el evento
   `plan.entregado` en el diario local, lo empuja a la rama `plan/ajusta-umbral-7f3c` del repo
   `gon0801/motor-state` (<1 min) y el implementador va por el bloque A con wip-push cada 5 min.
2. Corte de luz a mitad del bloque B. La PC se apaga.
3. Vuelve la luz; la PC enciende (BIOS "restore on AC", ya es AutoAdminLogon y nunca duerme). La
   tarea programada "Motor" corre al arranque aunque nadie haya iniciado sesión (usuario ehven con
   contraseña guardada). El tick hace `git fetch` del repo de estado, reconstruye el plan con fold,
   ve el intento de B "sin cierre", restaura el worktree desde `refs/motor/wip/...` y relanza.
   Ningún aviso a David salvo uno corto si la interrupción duró >30 min ("Hubo un corte; sigo desde
   donde iba.").
4. Variante peor: el disco de la PC muere. Se reinstala con `install.py` + `motor adopt ajusta-
   umbral-7f3c`: baja el estado, sube el epoch del arrendamiento y sigue. Se pierde como mucho lo
   no empujado en los últimos ~5 min. Un plan no se mueve de máquina solo; adoptar es acto del dueño.

### Escenario 2: "investiga X" a OpenClaw que se vuelve plan
1. David: "investiga por qué bajan las ventas del SKU 123". Claw es una IA que reparte: lanza
   `scout`/`odoo` (solo lectura) y contesta en el chat. Fin si la respuesta es información.
2. Hallazgo: "hay que cambiar el umbral en ads/rules.py". La regla (rules.toml `[ruta]`) dice: lo
   que escriba cualquier archivo versionado, base de datos o remoto es plan. Claw corre
   `motor draft --repo gon0801/goncloud-Orbit "cambiar umbral ..."`, completa filas y Acepta con lo
   que aprendió, `motor submit` y responde "Ya es un plan; empieza en la Mini. Te aviso al terminar."
3. En la Mini: implementa (relevo por la tabla), revisa Claude sin ventana, CI, merge con
   `--match-head-commit`, despliegue si el repo lo declara, prueba real, aviso final. Claw no
   vuelve a tocar el repo.

### Escenario 3: cuota agotada a mitad de revisión (PC)
Revisor por omisión: `claude` (Opus). Responde "limit reached, resets 03:10". `classify` → `cuota
con hora`. Motor anota evento `agente.cuota{agente, hasta}`, lo empuja, y elige el siguiente de la
escalera `revisar` que NO sea autor del bloque (autores = todos los agentes de intentos del bloque,
incluidos relevos previos). Pasa a codex; si también está caído pasa a grok. Si la escalera filtrada
queda vacía, no hay estado "pausado": el bloque queda "sin agente", se calcula cuándo vence el
primer cooldown y el siguiente tick (cada 30 s) lo toma solo; un único aviso "atorado" si pasa >2 h
sin ningún candidato. La hora de cooldown es absoluta en el evento, así sobrevive reinicio y reloj.

## Shape

### Estado de verdad: git, no disco
Dos repos privados nuevos en la cuenta de David, genéricos:

- `motor-config`: LA fuente única de roles, tabla de modelos con relevo por papel, reglas y versión
  fijada del motor. Cada máquina tiene un clon; el tick hace `git pull --ff-only` y cada plan
  guarda en su primer evento el SHA de config con que nació (reproducible, no cambia a media corrida).
- `motor-state`: una rama por plan, `plan/<id>`, con `plan.md` (copia inmutable), `events.ndjson`
  (solo se agrega) y `lock.json` (máquina anfitriona + epoch del arrendamiento). Un único escritor:
  el anfitrión. Un push rechazado por no-fast-forward significa "otro tomó el arrendamiento" y el
  proceso se detiene (per separate-before-serializing-shared-state: nunca dos escritores).

El proceso local no tiene estado propio: `Snapshot = fold(events)`. El diario local es un worktree de
la rama; cada tick agrega eventos, hace un commit por tick y empuja. Si GitHub no responde, el motor
sigue con el diario local (el trabajo no se detiene, los agentes ya hablan con internet) y reintenta
el push; la ventana de pérdida por disco = eventos desde el último push exitoso.

Efectos idempotentes y verificables contra la realidad, no contra el diario (per
make-operations-idempotent): cada acción lleva `idem = hash(plan, bloque, tipo, n)`; PR = buscar por
rama antes de crear; merge = `gh pr merge --squash --match-head-commit <SHA verificado>` (GitHub
mismo rechaza si el SHA cambió: el MergeTicket se hace cumplir en el servidor); despliegue "iniciado"
sin "terminado" → primero corre la sonda de estado (`deploy.verify`) y solo si falla relanza
(requiere `idempotent = true` en `.motor/repo.toml`).

Evidencia legible en GitHub sin tablero: el motor pone en cada SHA de bloque dos commit statuses
(`motor/entrega`, `motor/revision`), una línea de pie en el PR con el id del plan, y mantiene un
issue por plan en `motor-state` cuyo CUERPO (nunca comentarios, para no generar notificaciones) es la
tabla "bloque | estado | PR" re-renderizada del fold. Es derivado, no se escribe a mano, nadie lo
consulta por polling: es la pantalla que David abre si quiere, en la app de GitHub, de las tres
máquinas a la vez. Se puede apagar con una línea de config si "no tablero" es estricto (pregunta 3).

### wip-push que no rompe candados
Cada 5 min y al terminar, `runwrap` crea un commit con fontanería (índice temporal + `write-tree` +
`commit-tree`, sin hooks) y lo empuja a `refs/motor/wip/<plan>/<bloque>` con `--force-with-lease`.
No mueve la rama del bloque, no pasa por pre-commit, así que no hay `--no-verify`. El commit real del
agente (con sus candados) sigue siendo suyo. En la retoma, si el worktree no existe, se reconstruye
desde la wip. (A verificar en prototipo: que GitHub acepte esa ref; plan B: ramas `motor-wip/...`.)

### Paquete e instalación idénticas
- Repo `motor` (Python 3.11+, solo stdlib en el núcleo). Cada versión es un tag. `motor-config/
  release.toml` fija `motor = "0.3.1"`.
- `install.py` (stdlib, una pantalla, igual en las tres): asegura `uv`, hace `uv tool install
  --python 3.12 git+https://github.com/gon0801/motor@v0.3.1`, clona `motor-config`, escribe
  `machine.toml` (único archivo distinto por máquina), genera la skill para el entorno IA local,
  registra el servicio y corre `motor doctor --headless`.
- Actualizar: `motor update` solo cuando no hay intento vivo; compara el pin de `release.toml`,
  reinstala el tag, corre `doctor`, y si falla vuelve al tag anterior. Los eventos llevan
  `schema` y el fold tolera versiones anteriores, así que un plan en vuelo cruza una actualización.
- Deriva: `motor doctor` imprime `config_hash` y `motor_version`; la prueba de comparación entre
  marcas exige que sean iguales en las tres.

### Configuración: una fuente, tres capas
| Capa | Dónde vive | Contenido | Quién cambia |
|---|---|---|---|
| Compartida | `motor-config` (git) | `roles.toml`, `models.toml` (escaleras con relevo por papel), `rules.toml` (enrutamiento, revisor, avisos, topes), `release.toml` | David/lead por PR |
| Por repo | `.motor/repo.toml` en cada repo | CI requerido, `[deploy]` (run, verify, rollback, idempotent), `[needs]` (ssh, accesos), ramas protegidas, paths prohibidos | quien mantiene ese repo |
| Por máquina | `machine.toml` local (nunca en git) | nombre, rutas, CA (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`), tipo de servicio, tokens (archivo 600), chat de Telegram | instalador |
`machine.toml` tiene esquema cerrado: no admite roles, modelos ni reglas; así la identidad entre
marcas la impone el tipo, no la disciplina. El plan puede traer filas/Acepta/`humano:`, nada de
reglas.

### Servicio y credenciales por plataforma
| Máquina | Servicio | Credenciales | Nota |
|---|---|---|---|
| Mini | LaunchAgent en dominio `gui/<uid>` (auto-login de gon ya activo), KeepAlive; `pmset autorestart 1` | llavero desbloqueado por la sesión gráfica (claude, cursor, muse sirven); respaldo: `CLAUDE_CODE_OAUTH_TOKEN`, `CURSOR_API_KEY`, `META_API_KEY` en `secrets.env` 600 | `doctor --headless` corre CON el contexto del servicio (no por ssh, que es lo que falla hoy) |
| PC | Tarea programada "al iniciar el equipo", "aunque no haya sesión", usuario ehven con contraseña guardada (la teclea David una vez) + disparador de respaldo al iniciar sesión | todos por archivo (claude, muse, codex...) | env de Kaspersky en `machine.toml`; runwrap con backend Windows (Job objects, shims .cmd portados de `process.py`); Muse vía `wsl -d motor -- muse ...` con login propio en WSL; el motor corre nativo en Windows, no dentro de WSL |
| MacBook | LaunchAgent de usuario (arranca al iniciar sesión) | llavero normal (hay sesión) | ver FileVault abajo |

**FileVault en la MacBook, cómo se cumple req. 4.** No se cumple "sin sesión" y no pretendo
fingirlo: apagar FileVault o activar auto-login sería rebajar la seguridad de la laptop de David.
Se cumple lo que sí importa: (a) el estado no está en el disco, así que tras apagón y contraseña
el LaunchAgent arranca solo, hace fetch y retoma sin que David toque el plan; (b) `caffeinate -s`
(aserción de energía mientras haya plan activo) evita que la tapa o la inactividad lo pausen en
corriente; (c) actualizaciones planeadas usan `fdesetup authrestart` para reiniciar sin pedir
contraseña; (d) una etiqueta `anfitrion_confiable = false` en `machine.toml` hace que el aviso de
`submit` en la Mac diga "esta Mac se detiene si se apaga; para planes largos usa la Mini" y
`motor submit --en mini` (ssh a la Mini, que es entregarlo ALLÁ) está a un verbo. Req. 2 se respeta:
un plan se entrega a una máquina y corre ahí completo.

### Enrutamiento chico vs plan (regla única, en `rules.toml`)
Es "tarea chica" solo si cumple las tres: (1) no escribe ningún archivo versionado, base de datos,
configuración ni sistema remoto; (2) no despliega ni mergea; (3) cabe en una conversación del
agente (sin esperas a terceros). Si falla una, es plan, aunque sea una línea. Mixtas se parten: la
parte de lectura se contesta, el cambio se vuelve plan. Se hace cumplir con estructura: la skill de
la IA no tiene herramienta de escritura de repos; los agentes que reparte (`scout`, `verifier`,
`reviewer`, negocio) corren con sandbox de solo lectura; `implementer` sale de la lista que la IA
puede lanzar. `motor submit` es el único camino a cambiar código. La cadena fija de Hermes se retira
porque `motor` ocupa ese lugar.

### Revisor
`rules.toml [revision]`: primero `claude` (Opus, headless, sin ventana, rol `revisor`); si no está
disponible (cuota/auth/transitorio) se recorre la escalera `revisar` de `models.toml`, filtrando a los
autores efectivos del bloque (agentes de TODOS sus intentos). Sin candidato: espera el primer
cooldown, no se rebaja el criterio y no se mergea sin APROBADO sobre el mismo SHA con CI verde.

### Canal de avisos
Un solo bot de Telegram, enviado directo por `motor` con la Bot API (token en `secrets.env`), sin
pasar por OpenClaw ni Hermes (si el gateway cae, el aviso sale igual). Solo envía; David sigue
hablando con claw/Hermi como hoy. Cada aviso es una plantilla fija de `rules.toml`, pasa por
`lint_plain` (≤8 líneas, sin SHAs ni rutas) y lleva un prefijo corto con la máquina ("Mini"). Es
at-least-once con `idem`: el aviso queda como evento `aviso.pendiente` y se marca enviado solo con
respuesta 200, así un reinicio no lo pierde y el id evita duplicados.

### GitHub genérico y despliegue genérico
`gh` + `GH_TOKEN`/`hosts.yml` de la máquina; el motor solo conoce `OWNER/REPO`. CI = check runs del
SHA contra la lista `required` de `.motor/repo.toml` (o `ci="ninguno"` explícito: entonces Acepta en
copia limpia es la compuerta). Preflight por repo: rama principal protegida o negarse a empezar si
el motor pudiera empujar a ella. Despliegue: `[deploy] run/verify/rollback` en `.motor/repo.toml` más
el bloque `despliegue:` del plan; se ejecuta en el anfitrión. `[needs]` se prueba en `submit`
(ej. ssh a `goncloud`): un plan con deploy entregado a una máquina que no puede llegar se rechaza
al entrar, no al final.

### Profundidad de interfaz
Para los clientes son 6 verbos y un JSON. Detrás: parser, fold, decisión pura, relevo, arrendamiento,
sincronización git, procesos por SO, GitHub, despliegue, avisos. Los tipos de transporte (JSON de
`gh`, de Telegram, de cada CLI) se parsean dentro de `effects/` y nunca salen (boundary-discipline).
`decide()` es pura: `(Snapshot, Config, now) -> list[Action]`.

## Synthesis decision

(vacío; lo llena arena)

## Requisitos 1-10

| # | Requisito | Cómo se cumple |
|---|---|---|
| 1 | Un motor igual en 3 máquinas | Un paquete versionado (`motor@tag`), `install.py` idéntico, `release.toml` fija versión; `doctor` compara `config_hash`+versión |
| 2 | Plan completo en la máquina donde se entrega | `lock.json` nombra un solo anfitrión; el epoch impide dos escritores; no hay conmutación automática; `adopt` es acto del dueño en la misma máquina |
| 3 | Instalación idéntica | Capas de config: solo `machine.toml` difiere y su esquema cerrado no admite roles/modelos/reglas |
| 4 | Retoma sola, sin sesión, wip a GitHub | Servicio por SO (Mini LaunchAgent+auto-login, PC tarea de arranque, Mac LaunchAgent tras desbloqueo FileVault); estado y wip en GitHub cada ≤5 min; apagada = espera |
| 5 | Corre solo hasta probar | Máquina de estados de taller: Acepta, revisión, CI, merge, despliegue, `prueba-real`, aviso; humanos solo `humano:` |
| 6 | Multiproyecto | `OWNER/REPO` por plan; todo lo específico en `.motor/repo.toml` o en el plan |
| 7 | OpenClaw/Hermes iguales, cadena retirada | Skill generada idéntica, verbos `motor`, regla de enrutamiento en `rules.toml`; implementer/cadena fuera de su lista |
| 8 | Revisor Claude, relevo a no-autor | `rules.toml [revision]` + `revisores_para` sobre autores efectivos |
| 9 | Un canal Telegram | Envío directo desde `notify`, plantillas con lint, prefijo de máquina |
| 10 | Órdenes vigentes | Plan = autorización; sin esperas (cooldown vence solo); sin tablero (issue derivado, apagable); sin `--no-verify` (wip por fontanería); push a principal prohibido por preflight; secretos solo en `secrets.env`; `prueba-real` obligatoria |

## Las 10 familias de falla

| Familia | Cierre estructural |
|---|---|
| 1 Permisos que paran | Flags headless por CLI en `models.toml`, probadas por `doctor --headless` en contexto de servicio; stdin=/dev/null |
| 2 Entregas no recibidas | Sin teclear en ventanas: el plan entra por `submit` (archivo + evento) y el resultado se lee de git/PR |
| 3 LISTO falso | Se verifica SHA contra origin y Acepta en copia limpia; el agente no declara nada |
| 4 Coordinador sin herramientas | La IA solo puede `submit`; mergea el motor, y solo con MergeTicket + `--match-head-commit` |
| 5 Vigilancia cara | Daemon sin modelo; cero tokens entre eventos; el dueño mira GitHub, nadie hace polling |
| 6 Cuota/pausas largas | `classify` con hora; cooldown absoluto que vence solo; escalera de relevo por papel |
| 7 Reinicios/estado perdido | Estado en git; proceso sin estado; wip ref; efectos idempotentes; `adopt` tras pérdida de disco |
| 8 Compuertas humanas | Solo `humano:` listados; el resto autorizado por el plan; paso humano no bloquea otros bloques |
| 9 Merge antes de revisión/CI | `merge_gate` puro + `--match-head-commit` impuesto por GitHub |
| 10 Avisos técnicos | Plantillas fijas + `lint_plain` ≤8 líneas, sin SHAs/rutas |

## Qué pasa con lo existente

| Pieza | Destino | Detalle |
|---|---|---|
| taller (machine/plan/runwrap/notify) | REUSAR como núcleo | Se renombra `motor`; `store.py` SQLite se reemplaza por diario git; `effects` stub de CI/merge/deploy se implementan con gh genérico |
| runwrap | PORTAR | Añade backend Windows (de `process.py`) y lanzador `wsl`; wip por fontanería |
| Hermes `router.py` + `adapters/*` | PORTAR datos y lecciones | Tabla v6 (8 carriles x 4 posiciones) pasa a `models.toml`; arriendos/cuarentena se reducen a cooldown por clasificación; "continuación tras mutación" se reevalúa |
| Hermes `process.py` | PORTAR | Job objects, `_resolve_windows_shim`, terminación de árbol dentro de runwrap-windows |
| Hermes `entrega_pr.py` | PORTAR la idempotencia | Lógica de PR por API pasa a `effects/github.py`; el módulo se retira |
| Hermes `avisos_sesion.py` | RETIRAR | Motor avisa directo; la IA se entera con `motor status` |
| Hermes `local_orchestrator.py` y cadena | RETIRAR | Reemplazados por el motor |
| Raíces (SUA-07) | RETIRAR | Doble ejecutor; PR #37 se cierra sin merge |
| OpenClaw `agents/merge.py` | RETIRAR | El motor es la única ruta de merge |
| Agentes OpenClaw (implementer, etc.) | CONSERVAR solo lectura | Los de lectura siguen; `implementer` sale del conjunto de la IA |
| SUA v1 | RETIRAR spec | Se rescata una idea: `idem` por comando con recibo durable |
| Gateways y Telegram de claw/Hermi | CONSERVAR | Siguen siendo la cara de conversación; no avisan planes |

## Tradeoffs accepted

- Aceptamos depender de GitHub para sobrevivir a la pérdida de disco, a cambio de no tener ni
  copias ni sincronización entre máquinas; con GitHub caído el motor sigue local pero un disco
  perdido en ese rato pierde esos eventos.
- Aceptamos un commit+push por tick (~1/min, solo si hay eventos) a cambio de recuperación exacta.
- Aceptamos que la Mac con FileVault no retoma hasta que David desbloquee, a cambio de no debilitar
  su laptop; el motor lo dice al entregar y ofrece la Mini.
- Aceptamos un issue derivado por plan como "pantalla" mínima, a riesgo de que se perciba como
  tablero; es apagable.
- Aceptamos `uv` + un tag por versión (en vez de un zipapp) a cambio de actualizaciones por una línea.
- Aceptamos dos repos privados nuevos (config y estado) a cambio de que ningún repo de proyecto
  contenga estado del motor.
- Aceptamos que `adopt` sea manual; un failover automático chocaría con req. 2 y con el riesgo de
  doble ejecución.

## Alternatives considered

- **SQLite local por máquina (taller tal cual) + copia de respaldo**: interfaz igual de simple pero
  la recuperación depende de que el respaldo sea reciente y de un segundo mecanismo; perder disco =
  perder plan. Perdió contra "git es el único almacén".
- **Estado en el repo del proyecto (rama del propio repo)**: menos repos, pero ensucia cada
  proyecto, falla con repos sin permiso de empujar ramas auxiliares y obliga a cada repo a saber de
  motor. Perdió; el estado va en un repo central.
- **Estado en Issues/Check runs solamente**: visible pero sin orden total ni lectura atómica y con
  límites de API; el log append-only en git es más fuerte, los issues quedan como vista derivada.
- **Servidor central (goncloud) coordinando**: viola req. 2 en espíritu y añade punto único.

## Open questions y riesgos (para David, en llano)

1. ¿Te sirve que la MacBook solo retome después de que le teclees la contraseña tras un apagón, y
   que los planes largos se entreguen a la Mini? Es lo único que FileVault permite sin debilitarlo.
2. ¿Autorizas crear dos repos privados nuevos, uno para configuración y otro para el estado de los
   planes?
3. ¿Quieres ese resumen de cada plan visible en la app de GitHub (una lista de una línea por
   bloque), o prefieres que no exista nada parecido a un tablero?
4. ¿Escribes tú una vez la contraseña de ehven en la PC para que el motor arranque sin sesión?
5. ¿En la PC vale instalar WSL ya (lo necesita Muse) aunque el motor corra fuera de él?
6. Para repos sin CI: ¿basta con que los Acepta del plan corran en copia limpia como compuerta?
7. Riesgo: ¿los repos de despliegue (ej. Orbit) aceptan declarar `idempotent = true` en su
   despliegue? Si no, un corte durante el despliegue pedirá revisar a mano antes de repetir.
8. Riesgo técnico a probar: que GitHub acepte las refs `refs/motor/wip/*`.

## Prototype scope (en esta Mac, unas horas, sin tocar Orbit/claw/Hermes/producción)

Carpeta nueva `~/dev/goncloud-motor-proto` (copia de taller como base), remoto "GitHub" = repos bare
con `file://`. Sin red, sin Telegram real (buzón de archivos).
1. `GitState`: diario ndjson + sync a repo bare, arrendamiento con epoch, rechazo de segundo
   escritor. Prueba: dos instancias, solo una escribe.
2. Prueba central: plan de 3 bloques con agentes simulados; `kill -9` del daemon a mitad, se borra
   el `--home` por completo, `motor adopt` desde el bare, el plan termina y la secuencia de eventos
   final es la misma que sin corte (salvo el evento de retoma).
3. wip por fontanería: repo con pre-commit que siempre falla; verifica que la wip se empuja sin
   `--no-verify` y que el worktree se reconstruye de ella.
4. Config en capas: tres HOME simulados (mac, mini, pc) con `machine.toml` distinto; mismo
   `config_hash`; un `machine.toml` con `[models]` es rechazado.
5. Cliente: `motor submit/status/draft --json` y una "skill" falsa que decide chico vs plan con la
   regla de `rules.toml` (tabla de 12 pedidos de ejemplo → resultado esperado).
6. Revisor: caso de cuota con hora y autores excluidos (reusa pruebas de taller).
7. Generadores de servicio: plist (launchd) y XML (schtasks) y unidad systemd escritos a disco y
   validados con `plutil -lint` / parse XML; no se instalan.
8. `install.py` a un prefijo temporal con `uv tool install --from . ` y `motor doctor`.
Salida: prueba de punta a punta verde (>=25 pruebas), sin dependencias nuevas salvo uv.
Opcional con permiso: un repo privado desechable en GitHub para verificar las refs wip y
`--match-head-commit` reales.

## Rollout como UN plan (entregado primero a la Mini)

| Bloque | Contenido | Acepta (resumen) |
|---|---|---|
| A | Motor: taller + GitState + gh genérico + deploy genérico | pruebas del prototipo + canario en repo desechable real |
| B | `motor-config`, `motor-state`, `install.py`, `update`, skill generada | `doctor` imprime mismo hash en dos entornos |
| C | Instalar en Mac (LaunchAgent, `caffeinate`, authrestart) | plan canario completo; reiniciar sesión y retomar |
| D | Mini (LaunchAgent, `pmset autorestart 1`, `secrets.env`) | `doctor --headless` verde para los 7 CLIs en contexto de servicio; canario; `kill -9` + reinicio forzado y retoma |
| E | PC (tarea de arranque sin sesión, CA de Kaspersky, runwrap Windows, WSL+Muse) | `humano: contraseña de ehven`; canario; reinicio remoto retoma sin sesión |
| F | Skills OpenClaw y Hermes; sacar `implementer`/cadena de su alcance | prueba: "investiga" contesta; "cambia" produce plan |
| G | Comparación de marcas: los mismos 3 canarios por claw y por Hermi | mismos eventos y avisos; reporte de estabilidad |
| H | Retiro: cadena, Raíces, `merge.py`, `avisos_sesion`, SUA | referencias eliminadas; PRs viejos cerrados |

Pasos humanos listados: contraseña de ehven (E), login de CLIs en WSL (E), permiso de repos nuevos (B).

## Next implementation step

Copiar taller al prototipo y escribir `state.py` (`GitState` con arrendamiento) junto con la prueba
de "borrar el home y adoptar desde el bare".
