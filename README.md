# motor

Motor de planes que corre igual en la Mac, la Mac Mini y la PC. Recibe un plan en Markdown,
lo implementa con agentes sin ventana, lo hace revisar por un modelo que no lo escribió, lo
mergea, lo despliega, lo prueba con datos reales y avisa a David en lenguaje llano. Sigue
solo después de un apagón. No le pide aprobaciones a nadie, salvo login, 2FA, captcha o pago.

El diseño y sus decisiones están en [docs/diseno.md](docs/diseno.md). La guía para claw y
Hermi está en [skill/motor/SKILL.md](skill/motor/SKILL.md).

## Verbos

`motor` = `python3 -m motor` desde esta carpeta.

```sh
motor valida plan.md --home ~/.motor                 # ¿el plan está listo para correr solo?
motor entrega plan.md --home ~/.motor --json --por claw   # tomado | encolado | rechazado
motor estado --home ~/.motor [--json]                # una línea por bloque
motor recibo <cmd_id> --home ~/.motor                # qué pasó con una orden
motor hecho <plan> <bloque> --home ~/.motor          # David terminó un paso humano
motor nota <plan> <bloque> "texto" --home ~/.motor   # llega al siguiente encargo
motor doctor --home ~/.motor [--json]                # conf, máquina, rutas y papeles
motor instala --maquina mac|mini|pc --usuario U --home H --imprime   # plist o tarea de Windows
motor servicio --home ~/.motor                       # supervisor; lo arranca el servicio
```

## Archivos de una máquina

- `~/.motor/maquina.toml`: nombre, ruta de la conf, `path_extra`, `env`, `wsl`, `github`, `tiene` (capacidades como `ssh-goncloud`, que los repos piden en `[deploy] requiere`) y, en la MacBook, `[no_dormir]`.
  Rechaza papeles, modelos, rutas y reglas, que viven en la conf compartida.
- `~/.motor/secretos.env` con modo 600: `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`,
  `MOTOR_TELEGRAM_TOKEN`, etc.
- Conf compartida: ver `conf-ejemplo/`.
- Por repo: `.motor.toml` en su rama principal.

## Probarlo

```sh
PYTHONPATH=. python3 -W ignore -m unittest tests.test_puro tests.test_github_contrato tests.test_motor_e2e
```

Las pruebas crean repos desechables, un GitHub falso, agentes y revisores simulados, un
servidor que imita a Telegram, y simulan un apagón. No tocan Orbit, OpenClaw, Hermes, GitHub
ni producción. `MOTOR_GH_SANDBOX=<dueño/repo>` corre además el contrato contra GitHub real.
