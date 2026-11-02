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
- `jaula`: el mecanismo de aislamiento que el motor usaría aquí. En macOS y Linux los
  agentes corren sin aislamiento, como el propio usuario, con el entorno completo
  (decisión del 2-nov): la fila es `directo` con estado ok y no se exige `sandbox-exec`
  ni `bubblewrap`. En Windows hay dos y se reportan por
  separado: la jaula nativa (psec o AppContainer, según el adaptador de cada agente) y la
  jaula de WSL, que la decide la sonda del motor sobre la distro
  (`jaula_wsl.causa_no_disponible`) y no el hecho de que haya una distro listada. Si falta
  uno de los dos la sección queda en aviso; si no hay ninguno, falta.
- `agentes`: los CLI de agentes del catálogo (codex, grok, muse, kimi, claude, opencode,
  qwen), solo presencia y su `--version`. Nunca consulta autenticación ni gasta cuota: que
  el binario esté no dice que la suscripción esté viva, y eso lo descubre el encargo al
  arrancar. En Windows, los agentes de shell (hoy claude) corren dentro de la distro WSL:
  su fila la decide la sonda de la jaula de WSL del motor (jaula y ejecutable de la
  distro), no el PATH de Windows; los demás exigen su jaula nativa (psec o AppContainer)
  antes de buscar el binario.
- `java_tlc`: si `verificacion/inspeccion/verificar.sh` puede correr aquí. Con `TLA2TOOLS`
  y Java utilizable, o con mise en PATH (verificar.sh instala las herramientas solo), está;
  si no, un encargo de verificación formal no puede ir a esta máquina. En Windows también
  mira las variables de usuario persistentes (`HKCU\Environment`, donde escribe `setx`):
  valen en las consolas que se abran después, no en la que corrió `prepara_pc.py`, y el
  reporte lo dice cuando las toma de ahí.
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

## Qué hacer con lo que falta

Lo que el reporte marca como falta es exactamente lo que hay que dejar en la máquina
antes de mandarle un encargo: Java y TLC para verificación formal, `capacidades.json`
con sus recursos y su carpeta, la llave Run apuntando al vigilante, `gh` con sesión
para el CI, y el canal de avisos y la señal en el home del motor.

En la PC (Windows), `ops/prepara_pc.py` deja lo que faltaba (Java y TLC,
`capacidades.json`, arranque por el vigilante). Después de prepararla ya no deben faltar
`java_tlc`, `capacidades`, `evidencia-ci` ni `arranque`; `gh` con sesión y el home del
motor (avisos, señal) dependen de la instalación de la PC y el reporte lo dice si falta.
