# taller

Prototipo de un sistema para que los agentes implementen, se revisen, prueben y
entreguen solos, cada uno en su computadora, sin que nada los detenga. David y el lead
planean; `taller` hace el resto y avisa por Telegram en lenguaje llano.

Diseño completo y decisiones: [docs/diseno.md](docs/diseno.md).

## Cómo se usa (lead)

```sh
taller valida plans/mi-plan.md --home ~/.local/state/taller   # revisa el plan antes de arrancar
taller arranca plans/mi-plan.md --home ~/.local/state/taller  # lo encola
taller estado --home ~/.local/state/taller                    # una línea por bloque
taller historia mi-plan B --home ~/.local/state/taller        # qué pasó en el bloque B
taller hecho mi-plan E --home ~/.local/state/taller           # David terminó un paso humano
taller nota mi-plan B "usa la función X" --home ...           # llega al siguiente encargo
taller doctor                                                 # qué agentes reales pueden correr sin ventana
```

(`taller` = `python3 -m taller` desde esta carpeta.)

El daemon (`taller daemon --home ...`) corre siempre, bajo launchd en la Mac.

## Formato del plan

Ver [proto/plan_demo.md](proto/plan_demo.md). Cada bloque es una rama y un PR. Cada fila
trae su comando `Acepta`. Cada bloque de código trae `prueba-real:`. Los bloques de
despliegue traen `despliegue:` (desplegar, checklist, rollback). Un paso que solo David
puede hacer va como `humano: login|2fa|captcha|pago <qué hacer>`.

## Probarlo

```sh
PYTHONPATH=. python3 -m unittest tests.test_unit tests.test_e2e
```

Las pruebas de punta a punta crean un repo desechable, agentes y revisor simulados, y
recorren las fallas del catálogo: cuota a media noche, agente que lee de la terminal,
agente que miente, agente colgado, CI en rojo, daemon muerto a la mitad y bloque sin
salida. No tocan Orbit, claw, GitHub ni producción.

Demo a mano:

```sh
python3 proto/sandbox.py /tmp/taller-demo
python3 -m taller arranca proto/plan_demo.md --home /tmp/taller-demo/home
touch /tmp/taller-demo/home/login-hecho        # el "paso humano"
python3 -m taller daemon --home /tmp/taller-demo/home --intervalo 0.3 --hasta-terminar
python3 -m taller historia demo --home /tmp/taller-demo/home
ls /tmp/taller-demo/home/avisos/pendientes      # los avisos que recibiría David
```

## Agentes reales

`ejemplos/taller.toml` trae las escaleras con Muse, GLM (opencode), Kimi, Grok, Codex y
Cursor como implementadores y Claude como revisor, todos sin ventana.
