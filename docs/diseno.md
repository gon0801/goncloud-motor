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
7. Revisor: Claude. Si no está, **cualquier modelo que no trabajó el bloque.**
8. Un solo canal de avisos, en lenguaje llano.
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
| C2 | Código de procesos de Windows de Hermes `process.py`: shims `.cmd`, grupo propio fuera del Job, tarea al arrancar | Partes puras hechas y probadas en la Mac. Job objects y WSL sin probar en la PC (MOTOR 01 C) |
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
- **No existen los estados FALLADO ni PAUSADO.** Una falla lleva a reintento, relevo o arreglo.
  Un corte de luz (cambió el `boot_id`) no cuenta como falla.
- **Cuota por cuenta** (Sonnet y Opus comparten Anthropic). La autoría y la exclusión del revisor
  van por modelo.
- **runwrap.** Lanza al agente separado: stdin nulo, grupo propio, tope de tiempo y corte si no
  avanza. Cada 5 minutos sube una foto a `motor-wip/<máquina>/<plan>/<bloque>` con un índice
  temporal, sin hooks y con filtro de secretos. El siguiente intento recupera la foto.
- **Avisos.** Plantillas cerradas, máximo 8 líneas, sin SHAs ni rutas ni jerga. Se envían al
  menos una vez y se guardan en una bandeja de salida.

## Probado en el prototipo (37 pruebas, 1 saltada)

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

## Despliegue: un solo plan, "MOTOR 01"

Lo corre el prototipo en la Mac. Nadie usa el motor para trabajo real hasta que pasa el bloque I.

- **A** Repo `goncloud-motor-conf`: tabla de modelos v6, rutas, reglas, `motor conf valida` y CI.
- **B** Motor: `GhGitHub` probado contra un repo desechable, reglas del router de Hermes,
  rechazo por `requiere` y versión 1.0.
- **C** Motor en Windows: Job objects, WSL para Muse y `doctor` en la PC por ssh (solo lectura).
- **D** `.motor.toml` en cada repo que vaya a usarlo (Orbit: `rapido`, `completa`, `gate` y deploy).
- **E** Paso humano, login: David crea el bot "Motor", corre `claude setup-token` y llena
  `secretos.env` en las tres máquinas.
- **F** Instalar en la Mac, la Mini y la PC (`motor instala`). `doctor` corre como el servicio.
  En la Mini, `pmset autorestart 1`.
- **G** `goncloud-hermes`: skill `motor`, tabla generada, y se apagan `local_orchestrator`,
  Raíces, `avisos_sesion` y el cron `despertar-raices`. Se cierra el PR #37.
- **H** `goncloud-openclaw`: skill `motor`, tabla generada, `implementer` sin escritura, y se
  retiran `merge.py`, los loops y la skill `prompt-claw`.
- **I** Plan canario en un repo desechable, entregado por claw en la Mini y por Hermi en la PC.
  A media corrida se reinicia cada máquina. Los dos planes terminan, con un PR y un merge por
  bloque, y llegan los avisos.

Primer plan real después de I: INGESTA 01 de Orbit (PR #483).
