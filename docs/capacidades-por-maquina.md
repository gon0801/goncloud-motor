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
- `jaula`: el mecanismo de lanzamiento que el motor usaría aquí. Desde el 2-nov los
  agentes corren sin aislamiento, como el propio usuario, con el entorno completo en
  macOS, Linux y Windows: la fila es `directo` con estado ok y no se exige
  `sandbox-exec`, `bubblewrap`, psec, AppContainer ni WSL.
- `agentes`: los CLI de agentes del catálogo (codex, grok, muse, kimi, claude, opencode,
  qwen, antigravity), solo presencia y su `--version`. La ruta sale de
  `agentes.resolver_binario`, la misma función con la que el supervisor de encargos
  decide y lanza: el lanzador de la generación vigente de la flota (`runtime.json` de su
  `actual`) o, sin generación, el PATH del proceso. Si esa generación no trae `agy`,
  Antigravity sigue en el PATH; un lanzador `agy` declarado cuyo archivo no existe no se
  sustituye por el PATH. `path_extra` de `maquina.toml` no
  entra aquí porque el servicio de encargos no lo aplica a los agentes. Nunca consulta
  autenticación ni gasta cuota: que el binario esté no dice que la suscripción esté viva,
  y eso lo descubre el encargo al arrancar. Antigravity es la excepción de texto: si
  `agy` está, el detalle añade `con sesión` o `sin sesión`. La carpeta
  `~/.gemini/antigravity-cli` (en Windows, `%USERPROFILE%\.gemini\antigravity-cli`) y su
  caché no cuentan: `agy changelog` las crea sin login. `GEMINI_API_KEY` con
  `modelProvider` `gemini` habla con la API de Gemini y no es la suscripción. Hay sesión si el archivo
  `antigravity-oauth-token` existe y no está vacío (no se abre), o si el llavero del
  usuario del proceso tiene el ítem de servicio `gemini` y cuenta `antigravity` (en macOS `security` sin
  `-w` ni `-g`, en Windows el nombre en `cmdkey /list`, en Linux `secret-tool lookup`
  con la salida a `/dev/null` y solo el código de salida). El valor del secreto nunca pasa por
  el motor. El estado de la fila sigue al ejecutable, como el resto.
  En Windows el cliente es `agy` y el shim resuelve `agy.exe`. No se exige jaula ni distro.
- `java_tlc`: si `verificacion/inspeccion/verificar.sh` puede correr aquí. Con `TLA2TOOLS`
  y Java utilizable, con las herramientas de la base en `<base>/herramientas`
  (`tla2tools/1.7.4/tla2tools.jar` y `java/actual/bin/java`, la ruta que la preparación
  deja y que tanto el reporte como `verificar.sh` miran sin `TLA2TOOLS` ni mise), o con
  mise en PATH (verificar.sh instala las herramientas solo), está;
  si no, un encargo de verificación formal no puede ir a esta máquina. En Windows también
  mira las variables de usuario persistentes (`HKCU\Environment`, donde escribe `setx`):
  valen en las consolas que se abran después, no en la que corrió `prepara_pc.py`, y el
  reporte lo dice cuando las toma de ahí.
- `recursos`: los recursos de `capacidades.json` de la base y si son usables: un sqlite se
  abre en solo lectura y se le hace una consulta mínima; un remoto por fichero exige su
  `raiz` presente y legible; un `ci` con `repo` fijo se sondea con `gh repo view` contra
  ese repo (usa la sesión de gh del host; es una llamada de lectura, sin cuota) y uno sin
  `repo` fijo solo exige `gh`: el repo sale del encargo. Sin `capacidades.json` los
  agentes no pueden pedir recursos: falta. Un remoto http no se sondea: aviso.
- `avisos`: el canal de Telegram del motor (chat en la conf y `token_env` presente en
  `secretos.env`). Nunca imprime el token.
- `senal`: si `MOTOR_KUMA_URL` está presente en el entorno o en `secretos.env`. Nunca
  imprime su valor.
- `arranque`: el relanzamiento al prender. En macOS, el LaunchAgent de encargos
  `~/Library/LaunchAgents/ai.goncloud.motor-encargos.plist`, con `KeepAlive` o
  `RunAtLoad`, y cargado en launchd (`launchctl print gui/<uid>/ai.goncloud.motor-encargos`);
  un LaunchDaemon viejo ya no alcanza. En Windows, la llave
  `HKCU\...\Run\GoncloudMotorEncargos` lanzando el vigilante
  (`ops/iniciar_encargos_pc.py`) y no `supervisor.ps1` directo. En Linux no hay política
  declarada: aviso, no falta.

## Qué hacer con lo que falta

Lo que el reporte marca como falta es exactamente lo que hay que dejar en la máquina
antes de mandarle un encargo: Java y TLC para verificación formal, `capacidades.json`
con sus recursos y su carpeta, la llave Run apuntando al vigilante, `gh` con sesión
para el CI, y el canal de avisos y la señal en el home del motor.

En la PC (Windows), `ops/prepara_pc.py` deja lo que faltaba (Java y TLC,
`capacidades.json`, arranque por el vigilante). En macOS el mismo script toma su rama
(`ops/prepara_pc.py --base ~/.motor-encargos`) y deja Java y TLC en
`<base>/herramientas`, `capacidades.json` con `ci-linux` y `ci-github`, la carpeta
`evidencia-ci` y el enlace `herramientas/java/actual`; no toca el LaunchAgent ni los
secretos. Después de prepararla ya no deben faltar `java_tlc`, `capacidades`
ni `evidencia-ci`; `arranque` sigue dependiendo del LaunchAgent de encargos
instalado en `~/Library/LaunchAgents`, que la preparación no toca, y `gh` con
sesión y el home del motor (avisos, señal) dependen de la instalación de la
máquina y el reporte lo dice si falta.
