# taller: diseño

Fecha: 2026-10-20. Sale de una arena de tres diseños (supervisor local, GitHub como
máquina de estados, un bot por computadora al estilo GrokBot) y un juez independiente.
Base: el supervisor local (24 puntos contra 20 y 18). Grafts abajo.

## El problema

Hoy un modelo (claw) hace de "agenda": relee un PROMPT.md de 30 KB, teclea encargos
en ventanas de tmux y espera que cada agente lo despierte. De ahí salen las diez
familias de fallas medidas entre septiembre y octubre de 2026: prompts de permiso que
paran carriles horas, entregas que nadie recibió, "LISTO" falsos, un PR que escribe en
Amazon mergeado sin revisión, vigías caros con falsas alarmas, pausas que duraron más que
la cuota, estado perdido en reinicios, compuertas humanas de 11 horas y avisos que David
no entiende.

La causa común: el estado vive en pantallas y en la memoria de un modelo, y avanzar
depende de que alguien vigile o se acuerde de despertar a otro.

## La forma

```
          lead (Claude) + David
                 │ plan en Markdown (filas con "Acepta", bloques con "prueba-real")
                 ▼
   taller valida / arranca ──► inbox/ ──► daemon (launchd, siempre encendido, sin modelo)
                                            │  tick = ingerir → fold → decide → ejecutar
                                            ▼
          log de eventos append-only (SQLite)  ← única verdad; el estado es fold(log)
                                            │
         ┌──────────────┬───────────────────┼───────────────┬──────────────┐
         ▼              ▼                   ▼               ▼              ▼
   agente sin       verificación       revisor sin        CI / merge     despliegue y
   ventana          del SHA            ventana            (MergeTicket)  prueba real
   (runwrap)        (Acepta)           (veredicto JSON)                  (scripts del merge)
         └──────────────┴───────────────────┴───────────────┴──────────────┘
                                            ▼
                               avisos/ ──► Telegram (plantillas, ≤8 líneas)
```

- **El scheduler es código.** El tick no usa ningún modelo: vigilar cuesta cero tokens.
- **Un solo escritor.** El daemon escribe el log; el CLI deja archivos en `inbox/`; los
  agentes dejan efectos en git y un `result.json`.
- **No existe FALLADO ni PAUSADO.** Un fallo produce otra acción: reintento, relevo,
  arreglo. La única espera es un paso humano declarado en el plan (login, 2fa, captcha,
  pago), y el resto del plan sigue.
- **Agentes sin ventana**, cada uno en su rama y su worktree, lanzados por `runwrap`:
  stdin cerrado, sesión propia, tope de tiempo y detector de falta de avance. Un prompt
  de permiso no puede colgar nada. Al terminar por la razón que sea, `runwrap` commitea y
  empuja lo que haya (`wip:`): un relevo o una caída no pierden trabajo.
- **Se verifican efectos, no texto.** "Entregado" exige que el SHA exista en origin y sea
  la punta de la rama; luego se corre cada `Acepta` en una copia limpia de ese SHA.
- **El merge solo con `MergeTicket`**, que solo construye `merge_gate`: entrega
  verificada, veredicto APROBADO y CI verde, todo sobre el mismo SHA.
- **Relevo por clasificación**, sin modelo: cuota (con hora de reinicio), auth,
  transitorio, falla, sin avance, mintió, sin cambios. Cuota → cooldown con fecha →
  siguiente de la escalera en el mismo worktree. El reloj devuelve al proveedor solo.
- **Avisos por plantilla.** `lint_plain` rechaza SHAs, rutas, jerga y más de 8 líneas.

## Cada familia de fallas

| # | Familia | Cómo queda |
| --- | --- | --- |
| 1 | Prompts de permiso | Sin TTY y stdin en `/dev/null`; banderas sin preguntas en la plantilla del proveedor; `taller doctor` revisa que existan; tope y detector de avance matan cualquier cuelgue. Probado: `test_stdin_no_cuelga`, `test_agente_colgado_se_detiene_y_releva`. |
| 2 | Entrega no confirmada | No hay tecleo: el agente es un proceso con su archivo de encargo. |
| 3 | LISTO falso, "cerrado" sin cerrar | El SHA se verifica contra origin; `Acepta` corre en una copia limpia; "hecho" exige la prueba real verde. Probado: `test_agente_que_miente_nunca_pasa_la_verificacion`. |
| 4 | Coordinador sin herramientas, PR sin revisión | El coordinador es código; sin `MergeTicket` no hay merge. Probado: `test_no_hay_merge_sin_veredicto_y_ci_sobre_el_mismo_sha` y el recorrido del log en cada prueba de punta a punta. |
| 5 | Costo de vigilar | Cero modelos en el tick. |
| 6 | Cuota y pausas largas | Cooldown con fecha y relevo inmediato; sin PAUSADO. Probado: `test_cuota_a_media_noche_releva_y_termina`, `test_cuota_releva_y_el_reloj_devuelve_al_proveedor`. |
| 7 | Reinicios | Log append-only; efectos idempotentes; agentes fuera del daemon. Probado: `test_matar_el_daemon_a_la_mitad_no_duplica_nada` (kill -9 a la mitad). |
| 8 | Esperas humanas | Solo pasos `humano:` del plan; la sonda los detecta sola; la regla 4 (sin comando de reproducción no bloquea) está en el código. |
| 9 | Merge adelantado | `merge_gate` sobre el mismo SHA. En Orbit además: PR draft, `gh pr ready`, `completa` y bot ai-review del SHA final. |
| 10 | Avisos técnicos | Plantillas y `lint_plain`, con prueba sobre todas. |

Un bloque sin salida (todos los agentes fallan) no se queda callado: avisa una vez que se
atoró y que Claude lo revisa, y el resto del plan sigue. Probado:
`test_sin_salida_avisa_que_se_atoro_y_no_espera`.

## Decisión de síntesis

- **Base:** supervisor local (candidato 1). Es el único donde las reglas viven en tipos y
  en un solo escritor, con menos piezas nuevas, y su prototipo se puede probar entero en
  la Mac.
- **De GitHub (candidato 2):** GitHub es la frontera, no el cerebro: la protección de
  `master` ya exige `gate` y `reconcile` también a administradores; el CI de Orbit se usa
  como hoy (draft → ready dispara `completa`). `runwrap` guarda y empuja el trabajo en
  cualquier salida. El host de cada agente puede ser otra máquina más adelante.
- **De GrokBot (candidato 3):** sonda que detecta sola un paso humano hecho; el plan se
  rechaza si no trae comandos de aceptación ni prueba real, o si la escalera tiene menos
  de dos implementadores; una calibración en sombra del revisor sin ventana antes del
  primer plan real; "mismo bloqueante dos veces" cambia de implementador.
- **Rechazado:** un modelo revisando cada comando de los agentes (si cae, todo se
  bloquea); buzones sincronizados por git entre máquinas; el orquestador junto al gateway
  de claw; runners de GitHub con App y usuario aparte como columna; `gh workflow run`
  para `completa`; un replan automático que pueda ampliar lo autorizado.

## Lo que falta para correr un plan de Orbit

1. `effects.run_ci` y `effects.merge` contra GitHub: PR draft al entregar, `gh pr ready`
   tras APROBADO, esperar `rapido`/`completa`/`gate` y el comentario del bot del SHA final,
   `gh pr merge --squash --match-head-commit`.
2. `deploy` por `ssh goncloud` con los scripts del SHA de `master`.
3. Telegram con el token en el Keychain de la Mac (sin depender del código retirado de la
   ruta de encargos).
4. launchd (`ejemplos/com.goncloud.taller.plist`) y `pmset -a disablesleep 1` mientras
   haya trabajo: `caffeinate` no evita que la Mac duerma con la tapa cerrada.
5. Calibración: correr el revisor sin ventana sobre un bloque ya mergeado y comparar con el
   veredicto real antes del primer plan vivo.
6. Shims de `gh`, `git` y `ssh` en el PATH de los agentes como barandal; la frontera real
   es la protección de rama y que solo el daemon mergee y despliegue.

## Preguntas para David

1. ¿La revisión del lead puede ser Claude sin ventana, con las mismas reglas y memorias,
   en lugar de la ventana abierta? Es lo que quita el tecleo (familia 2).
2. Cuando la cuota de Claude se acabe, ¿esperamos al reinicio (los implementadores siguen
   con otros bloques) o se paga Claude por API para la revisión?
3. ¿Se retira claw de los loops y se queda solo como chat?
