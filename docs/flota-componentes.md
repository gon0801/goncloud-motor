# Actualizar componentes en una generación aislada

El controlador consulta el distribuidor oficial, fija una versión exacta y prepara sus ejecutables y configuración en un directorio de generación. El cambio de versión usa la misma operación protegida del motor; no instala globalmente ni reinicia servicios operativos.

## Entrada

Las funciones públicas están en motor.flota.actualizaciones:

- descubrir(componentes): consulta las versiones publicadas. Admite openclaw, codex, claude, grok, qwen, kimi y hermes. Devuelve componentes disponibles y capacidades cuyo canal no pudo comprobarse.
- preparar(home, catalogo=None, motor_origen=None, repos=None): crea una candidata y la comprueba, sin activarla. Los repositorios hermes, claw y conf se descubren entre los checkouts vecinos o se reciben explícitamente por máquina.
- actualizar: prepara y llama a control.aplicar. Si hay trabajo sobre la generación vigente, la candidata queda preparada y la activación responde ocupada.
- comprobar_runtime(generacion): comprueba el recibo, hashes de ejecutables/configuración/skills, versiones realmente ejecutadas y configuración nativa de los directores presentes.
- estado(home): muestra la generación aplicada, componentes y recibos de preparación.

Un catálogo conservado permite reintentar las mismas versiones. No exige que David escriba nombres ni versiones a mano. Una selección parcial conserva los componentes ya aplicados; sus artefactos se reutilizan desde la caché comprobada y se instalan en la nueva generación para no depender de rutas antiguas.

## Fuentes e integridad

Los paquetes se consultan en registry.npmjs.org: openclaw, @openai/codex, @anthropic-ai/claude-code, @xai-official/grok, @qwen-code/qwen-code y @moonshot-ai/kimi-code. Se comprueba el SRI de cada tarball antes de invocar npm sobre un prefijo propio. El paquete instalado debe declarar el nombre y la versión esperados. Codex se vincula a su binario nativo compatible; no a node ni a un shim cmd.

Hermes se descubre en el release estable de github.com/NousResearch/hermes-agent. La referencia Git fija su revisión exacta. Se usa setup-hermes.sh --runtime-only o setup-hermes.ps1 -RuntimeOnly, según la máquina. El controlador consulta committed_venv y venv_command de pm.environments: no adivina una ruta .venv antigua. Para ejecutar Hermes conserva el lanzador oficial src/.hermes/bin/hermes[.exe]. PM mantiene sus propias comprobaciones de los artefactos fijados por Hermes.

El configurador usa el intérprete del controlador con su PyYAML existente y el código/dependencias elegidos por PM, dentro del entorno de ensayo. Si PyYAML falta, prepara esa utilidad en herramientas/config-utils con uv --target y hashes publicados por PyPI; nunca instala paquetes dentro del entorno administrado de Hermes. La configuración registra versión, procedencia y rutas de esa utilidad. Un reintento conserva la instalación Hermes ya completada y consulta el contrato de PM antes de volver a invocar setup.

Los instaladores y comprobaciones reciben HOME, USERPROFILE, APPDATA, LOCALAPPDATA, XDG, TEMP y rutas UV propios de la generación. HERMES_HOME/HERMES_RUNTIME_DIR y OPENCLAW_STATE_DIR/OPENCLAW_CONFIG_PATH también están aislados. El configurador no extrae la credencial interactiva de Muse desde WSL. Ningún token se registra en el manifiesto, los recibos o la evidencia.

## Configuración común

La política se carga del manifiesto del home o se siembra desde flota/manifesto.toml del checkout Conf. El controlador añade automáticamente las herramientas con su versión e integridad; conserva las cadenas, esfuerzos y preferencias existentes. Lectura, modificación y publicación comparten el candado del controlador.

La generación contiene manifesto.toml y flota-runtime.json fijados en un commit local del clon de suministro. Los checkouts de configuración se exportan por una lista de rutas permitidas, sin copiar archivos de autenticación, TLS ni estado operativo. La tabla de Hermes se proyecta desde el manifiesto, preservando bindings y metadatos de sus adaptadores.

El paquete poteto completo se valida mediante comprobar_copia del plugin versionado. Para Hermes se crea el perfil director con la API nativa, se instalan los plugins externos en los commits de director-plugins.json y configure_team aplica sus parches fijados antes de comprobar los perfiles. Para Claw, claw_models deriva y compara las cadenas; el ejecutable preparado valida el openclaw.json resultante sin arrancar el gateway. La configuración de ensayo no habilita canales ni cron.

runtime.json guarda lanzadores y configuración relativos a la generación, los ejecutables externos imprescindibles y sus hashes. El lanzador del motor debe resolverlos respecto a MOTOR_GENERACION_RUTA. Una preparación reutilizada vuelve a comprobar la integridad y las versiones; un archivo ausente o cambiado rechaza esa candidata y conserva la instalación vigente.

## Recuperación y reversa

Los recibos de preparación viven en preparaciones/SHA.json. La selección aplicada usa los recibos de instalación y la operación de recuperación/reversa ya existentes. Una descarga truncada, instalación fallida o configuración incompatible queda rechazada; no sustituye la generación actual.

No se ejecutan hermes_autoupdate.promote, claw_autoupdate.promote ni el watcher operativo durante esta fase: esos caminos pueden escribir por separado, desplegar o enviar avisos. La activación de fase5 debe asignar sus responsabilidades al único controlador y detener los escritores anteriores antes de habilitarlo.

La reversa independiente requiere conservar conjuntamente ops/reversa_ensayo.sh, ops/reversa_ensayo.py y motor/flota/cerrojo.py. Funciona aunque no pueda arrancar el motor que se retira.

## Verificación de esta implementación

python3 -m unittest tests.test_actualizaciones_flota -q ejecuta instalación npm real sobre un tarball local declarado, rechazo de integridad incorrecta y aplicación/reversa de una candidata con recibos. Ese artefacto local no demuestra una publicación upstream; la comprobación real de cada distribuidor y host se registra por separado en la evidencia final.

Fuentes primarias consultadas: [instalación de OpenClaw](https://docs.openclaw.ai/install), [validación de configuración](https://docs.openclaw.ai/cli/config), [instalación de Hermes](https://github.com/NousResearch/hermes-agent/blob/v0.22.5/website/docs/getting-started/installation.md) y [gestión de paquetes de Hermes](https://github.com/NousResearch/hermes-agent/blob/v0.22.5/website/docs/reference/package-management.md).
