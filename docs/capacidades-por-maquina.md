# Qué puede correr cada máquina, antes de mandar un encargo

`motor director capacidades --base B [--json]` mira la máquina donde corre y la base de
encargos que le des, y contesta la pregunta que hoy se descubre cuando algo falla: ¿puede
esta máquina tomar este encargo? Funciona en macOS, Linux y Windows.

## Cómo leerlo

Cada sección dice `ok`, `aviso` o `falta`. El comando sale 0 solo si nada de lo que la
máquina declara falta; con cualquier falta sale 1. Con `--json` el mismo reporte sale como
JSON (`"ok": true|false` y la lista `faltas`). Antes de mandar un encargo:

1. Correr el comando contra la base destino: `python -m motor director capacidades
   --base <base> --json`.
2. Si sale 0, la base puede recibir encargos en esa máquina.
3. Si sale 1, `faltas` nombra lo que falta; mandar el encargo igual es apostar a que el
   hueco no toque.

`--home` señala el home del motor instalado (por omisión `~/.motor`) para mirar el canal de
avisos y la señal de Kuma; si no existe, esas dos secciones salen como falta.

## Qué mira cada sección

- `maquina`: sistema, nombre de `maquina.toml` si se puede leer, y versión de Python.
- `jaula`: el mecanismo de aislamiento que el motor usaría aquí. En macOS, seatbelt
  (`sandbox-exec`); en Linux, bubblewrap; en Windows, WSL (la jaula de los agentes corre
  dentro de la distro). Sin mecanismo, un encargo que necesite aislar al trabajador no
  puede correr.
- `agentes`: los CLI de agentes del catálogo (codex, grok, muse, kimi, claude, opencode,
  qwen), solo presencia en PATH y su `--version`. Nunca consulta autenticación ni gastan
  cuota: que el binario esté no dice que la suscripción esté viva, y eso lo descubre el
  encargo al arrancar.
- `java_tlc`: si `verificacion/inspeccion/verificar.sh` puede correr aquí. Con `TLA2TOOLS`
  y Java utilizable, o con mise en PATH (verificar.sh instala las herramientas solo), está;
  si no, un encargo de verificación formal no puede ir a esta máquina.
- `recursos`: los recursos de `capacidades.json` de la base y si son usables: un sqlite se
  abre en solo lectura y se le hace una consulta mínima; un remoto por fichero exige su
  `raiz` presente y legible; un `ci` se sondea con `gh repo view` contra su repo (usa la
  sesión de gh del host; es una llamada de lectura, sin cuota). Sin `capacidades.json` los
  agentes no pueden pedir recursos: falta. Un remoto http no se sondea: aviso.
- `avisos`: el canal de Telegram del motor (chat en la conf y `token_env` presente en
  `secretos.env`). Nunca imprime el token.
- `senal`: si `MOTOR_KUMA_URL` está presente en el entorno o en `secretos.env`. Nunca
  imprime su valor.
- `arranque`: el relanzamiento al prender. En macOS, un LaunchDaemon del motor con
  `KeepAlive` verdadero; en Windows, la llave `HKCU\...\Run\GoncloudMotorEncargos` lanzando
  el vigilante (`ops/iniciar_encargos_pc.py`) y no `supervisor.ps1` directo. En Linux no
  hay política declarada: aviso, no falta.

## Qué hace falta en cada máquina hoy

- Mini (macOS): completa; el reporte debe salir 0.
- PC (Windows): `ops/prepara_pc.py` deja lo que faltaba (Java y TLC, `capacidades.json`,
  arranque por el vigilante); después de prepararla, el reporte debe salir 0.
