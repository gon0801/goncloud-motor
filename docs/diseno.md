# motor: diseño

Fecha: 2026-10-20. Reemplaza el diseño de `taller` (en `docs/arena/taller/`). Viene de una
segunda arena con tres candidatos (`docs/arena/motor/`) y un juez independiente:

| | C1 hacer crecer taller | C2 extraer de Hermes | C3 estado en git |
|---|---|---|---|
| Total (6 criterios, 1-5) | **23** | 22 | 18 |

## Qué pidió David (2026-10-20)

1. Un solo motor, **instalado igual** en la Mac (lead), la Mac Mini (OpenClaw) y la PC (Hermes,
   con WSL solo para Muse).
2. **Un plan corre completo en la máquina donde se entrega.** No se reparte entre computadoras.
3. **Retoma solo** tras un apagón o reinicio: arranca al prender la máquina, sin que nadie
   inicie sesión, y sube el trabajo a medias cada pocos minutos.
4. Corre hasta terminar **y probar**, sin pedirle aprobaciones a David. Solo le avisa para login,
   código 2FA, captcha o pago.
5. OpenClaw y Hermes reparten las tareas chicas de solo lectura entre sus agentes. **Todo lo que
   cambie código va al motor.** Se retira la cadena fija de 7 pasos de Hermes.
6. Mismos papeles, tabla de modelos y reglas en las dos marcas.
7. Revisor: **cualquier modelo que no trabajó el bloque.**
8. Avisos en lenguaje llano por el bot de la marca que corre el plan.
9. Sirve para cualquier repo, no solo Orbit.

## Decisión de síntesis

**Base: C1.** Es el único candidato con un registro SQLite y un solo escritor por máquina, sin
nada compartido entre máquinas (requisito 2). También tiene la superficie más chica (7 verbos),
cuenta la cuota por cuenta y no por modelo, y sube el trabajo a medias de la forma más segura.

**Injertos tomados:**

| De | Injerto | Estado en el prototipo |
|---|---|---|
| C3 | `maquina.toml` con esquema cerrado: rechaza papeles, modelos, rutas y reglas | Hecho y probado |
| C3 | `requiere` del repo revisado al entregar el plan | Se lee de `.motor.toml`. Falta rechazar el plan cuando la máquina no lo cumple (MOTOR 01 B) |
| C3 | `doctor` que corre dentro del contexto del servicio, no por ssh | `doctor` existe. Correrlo como el servicio queda para MOTOR 01 F |
| C2 | Código de procesos de Windows de Hermes `process.py`: shims `.cmd`, grupo propio fuera del Job, tarea al arrancar | Partes puras hechas y probadas en la Mac. Job object, taskkill y guardián de WSL en `windows.py`, probados en la PC con `ops/prueba-pc.sh` (MOTOR 01 C) |
| C2 | Reglas puras de `router._run_owned` y clasificación de fallas por proveedor | Clasificación básica hecha. Portar el resto con sus pruebas queda para MOTOR 01 B |
| C2 | Nombre de la máquina al inicio de cada aviso | Hecho: "En la Mini · <plan>:" |

**Rechazado:** el estado en git de C3, porque es un repo compartido, trae fallas nuevas y equivale
a un tablero. También la extracción completa de `subscriptions/` de C2, porque trae arriendos y
latidos que no hacen falta con un solo escritor.

## La forma

```
 claw / Hermi / lead ── motor entrega plan.md ──► inbox/ (archivo con cmd_id; sirve con el daemon caído)
                                                     │
            servicio al arrancar (LaunchDaemon / tarea de Windows) ─► supervisor ─► daemon (sin modelo)
                                                     │ tick: conf → inbox → intentos terminados
                                                     ▼        → fold(registro) → decide (puro) → efectos
                              registro de eventos SQLite (solo se agrega; el estado es fold del registro)
                                                     │
   efectos idempotentes: runwrap (agente sin ventana) · Workspace (git) · GitHubPort · Notifier (Telegram)
```

- **Capas de configuración.** Un repo de conf compartida (`conf-ejemplo/` es la plantilla) con
  papeles, carriles, rutas (modelo → cuenta → comando), tiempos, avisos y reglas. Por repo,
  `.motor.toml` en la rama base, fijado al cargar el plan: rama principal, checks extra, receta
  de despliegue y env de pruebas. Por máquina, solo hechos y tokens.
- **Un solo camino al merge.** `MergeTicket` exige que en el mismo SHA haya entrega verificada,
  PR, APROBADO del revisor, PR marcado listo y checks en verde. Después corre
  `gh pr merge --match-head-commit`.
- **Un bloque no tiene estado FALLADO.** Una falla lleva a reintento, relevo o arreglo.
  Un corte de luz (cambió el `boot_id`) no cuenta como falla. El plan sí puede quedar en pausa
  o cancelado. Eso vive en el registro y no lo borra una nota ni otra entrega del mismo plan.
- **Cuota por cuenta** (Sonnet y Opus comparten Anthropic). La autoría y la exclusión del revisor
  van por modelo.
- **runwrap.** Lanza al agente separado: stdin nulo, grupo propio, tope de tiempo y corte si no
  avanza. Cada 5 minutos sube una foto a `motor-wip/<máquina>/<plan>/<bloque>` con un índice
  temporal, sin hooks y con filtro de secretos. El siguiente intento recupera la foto.
- **Avisos.** Plantillas cerradas, máximo 8 líneas, sin SHAs ni rutas ni jerga. Se envían al
  menos una vez y se guardan en una bandeja de salida.

## Probado en el prototipo (55 pruebas, 1 saltada)

`PYTHONPATH=. python3 -m unittest tests.test_puro tests.test_github_contrato tests.test_motor_e2e`

- Un plan completo en dos repos: cuota de Anthropic a media revisión, relevo del revisor a uno
  que no fue autor, despliegue y prueba real.
- Fallas de agentes: lee de la terminal, miente, se cuelga, CI en rojo, se atora.
- Apagón: `kill -9` al daemon, al runwrap y al agente, más un `boot_id` nuevo. Se retoma con el
  mismo modelo, el trabajo a medias llega a la rama principal y sale el aviso de "retomé".
- Un check de seguridad fijado desde la base sigue exigido aunque el agente edite `.motor.toml`.
- Los avisos llegan a un servidor HTTP local con el prefijo de la máquina. No se avisa del paso
  humano si la sonda ya lo vio hecho.
- Foto wip: no toca el índice ni la rama del agente, no corre hooks, no se repite si no cambió
  nada y no sube secretos.
- Contrato de GitHub contra el falso. El real solo corre con `MOTOR_GH_SANDBOX`.

**No probado:** la PC y WSL, GitHub real, Telegram real, reinicios reales de las máquinas y
agentes reales en el servicio.

## Respuestas de David (2026-10-20)

| # | Pregunta | Decisión | Cómo queda |
|---|---|---|---|
| 1 | MacBook con la tapa cerrada | Un plan largo en la Mac sigue con la tapa cerrada | `[no_dormir]` en `maquina.toml` de la Mac: el motor enciende `pmset disablesleep 1` mientras haya un plan en curso y lo apaga al terminar. Requiere una línea de sudoers para `pmset` (bloque F). Tras un apagón la Mac sigue esperando la contraseña de FileVault |
| 2 | Canal de avisos | Cada marca avisa por su bot | Mini: bot de OpenClaw; PC: bot de Hermes. Mac: bot de OpenClaw, porque el lead no se queda mirando cuando termina su sesión. Es solo el token en `secretos.env` |
| 3 | Contraseña de ehven en la tarea de Windows | Sí | Tarea con `LogonType Password` |
| 4 | Tokens en archivo protegido | Sí | `secretos.env` con modo 600 |
| 5 | Prender al volver la luz | Sí | PC: ya activado según David. Mini: hoy `autorestart 0`, falta `sudo pmset -a autorestart 1` |
| 6 | Trabajo a medias en repos públicos | David no decidió. El lead eligió | Repos privados: la foto se sube a GitHub cada 5 min. Repos públicos o de visibilidad desconocida: la foto se queda en el disco de la máquina (`refs/motor-wip/...`). Sobrevive un apagón y nunca se publica |
| 7 | Plan que nace de un "investiga" | David da su sí | La skill obliga a preguntar "¿quieres que lo implemente?" antes de entregar. Un plan que David dio o un cambio que pidió se entrega directo |
| 8 | Retirar la cadena de Hermes y Raíces | En cualquier momento | G no espera al canario |
| 9 | WSL en la PC para Muse | Sí, ya | `wsl --install` como administrador en la PC, luego el login de Muse dentro de WSL |

## Modelos

Por omisión, la tabla v6 de David: implementan Sonnet → Kimi K3 → GLM 5.3 → Grok 4.7; revisan
Opus → K3 → GLM → Grok. Si no hay revisor de la lista, revisa cualquier modelo que no tocó el bloque.

Un plan puede pedir cualquier combinación con `implementa:` y `revisa:` en su encabezado (orden
de David, 2026-10-20). Cuando lo hace:
- Se usan solo esos modelos. Si se quedan sin cuota, el plan espera; no entra otro.
- `revisa:` necesita al menos un modelo que no esté en `implementa:`.
- Un modelo sin ruta en la conf se rechaza al entregar, con la lista de los que hay.

**Agregar, quitar o reemplazar un modelo** es un cambio en el repo `goncloud-motor-conf`, nada más:
1. Agregar: una entrada en `rutas.toml` (cómo se lanza sin ventana y qué cuenta lo paga) y, si
   necesita llave, esa llave en `secretos.env` de cada máquina. Con eso ya se puede pedir en un
   plan (`implementa: nuevo`). Para que entre solo, se pone su nombre en `modelos.toml`, en el
   carril y la posición que toque.
2. Quitar: se borra su nombre del carril (la ruta puede quedarse).
3. Reemplazar: se cambia un nombre por otro en el carril.
Cada máquina trae la conf sola cada 15 minutos y la aplica solo si pasa `motor conf valida`; si no
la pasa, sigue con la anterior y avisa una vez.

Cada ruta puede traer `env` con `${VAR}` de `secretos.env`. Así DeepSeek corre por el CLI de
Claude con su propia llave, sin tocar la suscripción.

## Despliegue: un solo plan, "MOTOR 01"

Lo corre el prototipo en la Mac. Nadie usa el motor para trabajo real hasta que pasa el bloque I.
G (retirar la cadena de Hermes) puede ir en cualquier momento.

- **A** Repo `goncloud-motor-conf`: tabla de modelos v6, rutas, reglas, `motor conf valida` y CI.
- **B** Motor: `GhGitHub` probado contra un repo desechable, reglas del router de Hermes,
  rechazo por `requiere` y versión 1.0.
- **C** Motor en Windows: Job objects, WSL para Muse y `doctor` en la PC por ssh (solo lectura).
- **D** `.motor.toml` en cada repo que vaya a usarlo (Orbit: `rapido`, `completa`, `gate` y deploy).
- **E** Paso humano, login: David corre `claude setup-token` y llena `secretos.env` en las tres
  máquinas (incluye el token del bot de cada marca).
- **F** Instalar en la Mac, la Mini y la PC (`motor instala`). `doctor` corre como el servicio.
  En la Mini, `pmset autorestart 1`. En la Mac, sudoers para `pmset disablesleep`.
- **G** `goncloud-hermes`: skill `motor`, tabla generada, y se apagan `local_orchestrator`,
  Raíces, `avisos_sesion` y el cron `despertar-raices`. Se cierra el PR #37.
- **H** `goncloud-openclaw`: skill `motor`, tabla generada, `implementer` sin escritura, y se
  retiran `merge.py`, los loops y la skill `prompt-claw`.
- **I** Plan canario en un repo desechable, entregado por claw en la Mini y por Hermi en la PC.
  A media corrida se reinicia cada máquina. Los dos planes terminan, con un PR y un merge por
  bloque, y llegan los avisos.

Primer plan real después de I: INGESTA 01 de Orbit (PR #483).

## MOTOR 09: control antes de seguir

- La retención del home es `mantenimiento.json`, con generación e id de solicitud. Se lee antes
  de la configuración y de recuperarla sola. Un archivo ilegible sigue retenido. Una liberación
  vieja no borra una generación nueva. Solo el daemon compatible libera, y solo si nombra la
  generación vigente y ya no hay árboles propios.
- El control del plan es `Activo`, `PausaSolicitada`, `Pausado`, `CancelacionSolicitada` o
  `Cancelado`. Sale de los eventos. `Cancelado` no vuelve con una nota ni con otra entrega.
  Reanudar no levanta el mantenimiento del home y no manda `SIGCONT`.
- El formato 3 se eleva bajo candado antes de escribir semántica nueva. No se rebaja. `Store`,
  `aparte` y `runwrap` lo comprueban. El historial anterior se conserva.
- `Store.append_batch` escribe una transición de control y su recibo en una sola transacción.
  El mismo id de comando devuelve lo ya escrito.
- Un efecto externo queda `aplicado`, `no_aplicado` o `incierto`. Una pausa no lo repite ni lo
  da por deshecho. El revisor sigue siendo un modelo que no trabajó el bloque.
- En Windows hay una sola creación. El trabajador separado sobrevive al daemon. El comando
  contenido queda en un Job que mata el árbol al cerrarse, y solo corre después de entrar en
  ese Job. Si crear el proceso devuelve `winerror` 5, un comando contenido se reintenta una
  vez sin `CREATE_BREAKAWAY_FROM_JOB`. Un trabajador separado no nace en ese segundo intento.
  El entorno queda rechazado y el agente no corre. Otro error no crea un segundo proceso.
- La marca de arranque es `reservado`, `proceso_observado`, `creacion_fallida` o
  `arranque_por_conciliar`. Solo un proceso observado publica `AttemptStarted`. Una reserva
  ambigua o un `proc` viejo vacío no se relanza ni se explica con una causa inventada. Si no
  se puede mirar el Job o el árbol, la observación sigue en `incierto`. Si el wrapper arrancó
  y el agente no se pudo crear, el intento termina en `creacion_fallida` con la causa saneada.
  Eso no pausa el modelo ni se lo pasa a otro. Una nota o un cambio de máquina permiten otro
  intento.
- Aceptar un plan lee los pines antes de abrir la transacción. En un solo lote entran los
  pines nuevos, `PlanLoaded` si el texto cambió, y el recibo. El mismo `cmd_id` devuelve la
  decisión ya guardada. Un fallo de la base deja el archivo en la bandeja y no inventa un
  rechazo. Tomar un plan no manda un aviso nuevo.
- Un aviso pedido queda como `NoticeRequested` con id estable. Se materializa al arrancar y
  en cada tick. Los archivos viejos se adoptan; un `NoticeQueued` sin archivo queda
  desconocido y no se reenvía. La entrega se confirma solo si el transporte responde 200 y
  `ok` es exactamente verdadero. Red, 429 y 5xx siguen pendientes con una espera de a lo
  más 15 minutos. Otro 4xx queda fallido, y un reintento usa el mismo id. Si el proceso
  muere después de enviar y antes de archivar, el aviso puede salir otra vez. Una url de
  avisos sin esquema `http` o `https` no se usa. La causa queda visible y no incluye el token
  ni la url del bot. La recarga no sustituye el notifier si la url nueva no es válida.
