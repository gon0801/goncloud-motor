# Fases 3 y 4

Muse implementó con Grok 4.7, por la suscripción de esta sesión. No hubo Cursor ni cambio a una API de pago. Los delegados salen con el mismo modelo, porque el lanzador no recibe otro. La revisión cruzada con otro modelo no se corrió. Codex revisa esta entrega.

No hay push, merge ni despliegue. Los actualizadores vivos siguen. El gateway de la PC no se reinició. No se migran encargos viejos ni se escribe en destinos de producción.

## Ramas

| Repo | Worktree | Rama | Base | SHA de código |
| --- | --- | --- | --- | --- |
| goncloud-motor | `/Users/dn/dev/goncloud-motor-encargos-3-4` | `feat/encargos-3-4` | `2ea73cdebaf50e4ec41c636efa757f895c30c7ca` | `add95abc74b42b5330f93042a08e389b08bb6c73` |
| goncloud-hermes | `/Users/dn/dev/goncloud-hermes-encargos-3-4` | `feat/encargos-3-4` | `acb653bea54c3293e15c0fd3d4e8e8bfa93fa044` | `f3bef23dc591015aa159a0910c8ca0c14bd3e212` |
| goncloud-motor-conf | `/Users/dn/dev/goncloud-motor-conf-encargos-3-4` | `feat/encargos-3-4` | `0f95b5ed8f78764d43eff2d57b0e8bec98578134` | `6b9e58f7c296d295ecd31b18462d695d273b4035` |

El código comprobado de la fase 2 es `2ec01d4c046bd239770793109d2d7ae568db9a79`. La base de esta rama es el commit que documenta ese cierre. `goncloud-openclaw` se consume en `75d51353282f741c263592f9408e2c1731c0c3ab` y no se brancho. El contrato es `scripts/sistema_agentes/contrato.py`, por `PYTHONPATH`.

Commits del motor, en orden:

- `f4133adca982f9ad6223c35de1dab44cc650072b` Controlar la flota por generaciones y revertir sin arrancar el motor nuevo
- `54ca565aa3bc0eea83a919515cf2c99affccead0` Conectar directores, la vía del encargo y el paquete poteto
- `add95abc74b42b5330f93042a08e389b08bb6c73` Recorrer las fases 3 y 4 en homes de ensayo

Este informe es un commit posterior. Su SHA es `git rev-parse HEAD` en el worktree del motor.

Dependencias. El recorrido llama a `scripts/claw_models.py` y `scripts/configure_team.py` del worktree de Hermes. Siembra `flota/manifesto.toml` en el worktree de conf. Valida la proyección con el contrato de openclaw. Hermes solo aprende a leer otra fuente. La tabla viva `model-routing.source.json` sigue siendo la de siempre cuando no hay `GONCLOUD_ROUTING_SOURCE` ni `--source`.

## Comportamiento comprobado

El recorrido es `python3 ops/ensayo_fases_3_4.py` desde el worktree del motor. Salió 0. La evidencia es `docs/evidence/fase-3-4.json`. No trae `fallo`. `limpieza.procesos` quedó `[]`. La bitácora es `docs/evidence/encargos-3-4.tsv`.

Pedido igual por Hermes y Claw. Directa `enc_58f942c5f36dd0bc` y `enc_5e3fcba50d2a264a`, con el mismo texto. Durable de Hermes `enc_15c0017fe57da863`. Plan externo de Claw `enc_951630d83fedc354`, igual al pedido. Promover con la sesión directa abierta devuelve `el ejecutor directo sigue abierto` y no crea `ejecucion/`. Después del cierre, el mismo id queda `durable`. Parar llega a `detenido`, continuar a `recibido`, cancelar a `cancelado`. Una fila `entrega`.

Poteto. El encargo `enc_ac36d67aaf29d2ef` escribió `poteto cargado` y quedó `detenido` antes del revisor. El agente `ensayo` ejecuta `espacio/trabajo.sh`. Es un doble. No acredita Hermes ni Claw.

Aviso. `enc_0525ccad9faa7a0d` queda `incierto` si el canal se corta después de escribir. La pasada siguiente publica, deja un archivo y no suma otra entrega.

OpenBot. Entrega y consulta están. Parada, skills y publicación de sesión no. El timeout no cancela. `run --wait --timeout 2s --json` salió 4, con `state` `timed-out` y `status` `running`. El `status` posterior salió 0. `director parar` responde `OpenBot no tiene parada demostrada; el trabajo sigue`. El CLI es `node` más `/Applications/OpenBot.app/Contents/Resources/cli/openbot-cli.mjs`. El HTTP es un servidor local de ensayo. Doble declarado. No se llamó `set-model`, `set-account` ni `run` contra la app viva.

Política. Nueve puestos. `dirigir` y `dirigir-claw` comparten cadena. `claw_models.py desired` coincide con y sin `--source`. El contrato `sistema-agentes.flota.v1` validó la proyección. La discrepancia medida dice que `codex --version` reporta `codex-cli 0.164.0` y la cadena de dirigir dice `anthropic/claude-opus-5-5 (high)`. Es el CLI real de esta Mac, no un proceso del plugin. Un respaldo de cuota en el manifiesto del home de ensayo no cambia el modelo 0 de dirigir.

Flota de ensayo, solo en la Mac. El trabajo activo conservó `96a17381a3622bb3b0ed41a9080065be615bd555`. La generación asignada después fue `33a9ae24eaca2640aa3d4b7bce194071a244089c`. La candidata inválida quedó `rechazada`. Un corte en `worktree` se recuperó a `completo`. La segunda recuperación imprimió `nada`. `ops/reversa_ensayo.sh` imprimió `revertido 33a9ae24eaca2640aa3d4b7bce194071a244089c`, no recreó la marca del motor roto y la segunda pasada imprimió `nada`.

## Máquinas

Mac. Python 3.14.7. `codex-cli 0.164.0`. `grok 1.0.70 (f50904eaee6c) [stable]`. `openclaw` y `hermes` no están en el `PATH`.

Mini, SSH rc 0. `Gons-Mac-Mini-2.lan`. Python 3.9.6. OpenClaw 2026.10.7 (`157d8c2`). No se instaló nada. Python 3.9 no trae `tomllib`, así que el controlador de ensayo no corrió ahí.

PC, SSH rc 0, límite 45 s. Python 3.14.7. Hermes Agent v0.22.5 (2026.9.24), stable, local `bf0203a794a0`, Python del install 3.14.9, OpenAI SDK 2.24.0, al día. El propio Hermes avisa que un `hermes update` anterior no reinició los gateways. No se reiniciaron. No se instaló nada.

## Comandos

```sh
cd /Users/dn/dev/goncloud-motor-encargos-3-4
python3 -m unittest tests.test_reversa_flota tests.test_encargo_motor06 -q
python3 ops/ensayo_fases_3_4.py
python3 -m unittest tests.test_puro.PlanTest.test_formato_de_la_skill_valida tests.test_puro.ServicioTest.test_todo_el_motor_lee_y_escribe_utf8 -q
python3 -m unittest tests.test_puro.FeedbackRevisorTest.test_un_fin_que_el_siguiente_revisor_no_puede_corregir_no_se_le_pasa -q
cd /Users/dn/dev/goncloud-motor-conf-encargos-3-4
python3 -m unittest tests.test_rutas -q
```

Resultados de esta corrida. Reversa y encargo: 6 tests, OK, 1.384 s. Al cerrar el intérprete avisó `ResourceWarning` de los subprocess 45391 y 45401. Un `ps` posterior ya no los mostró. Recorrido: exit 0. Skill y UTF-8: 2 tests, OK, 0.013 s. Rutas de conf: 4 tests, OK.

El test de fines ajenos falla y además lanza `StopIteration` cuando `kind` es `creacion_fallida`. `motor/machine.py` no está en este diff. Su último commit es `4e816c9`. Es un fallo previo.

No se corrieron `tests.test_github_contrato` ni `tests.test_motor_e2e`. No se repitió el trío del README ni la evidencia de transporte de la fase 2. `motor/encargos/transporte.py` no cambió. Ese código ya elige Git OpenSSH en Windows. `motor flota observar` registra ejecutable, versión y `sha256` del argv que se le da.

## Fallos que ya se corrigieron

La reversa de prueba buscaba `__main__.py` en la raíz del repo de ensayo. El commit roto ahora trae el paquete `motor/`. El recorrido exigía que el pedido no contuviera la palabra Acepta, y el pedido la nombra. La comprobación quedó en la igualdad con el pedido. El timeout JSON no imprime la palabra continues. Exige `timed-out` y `running`. Un commit de git salía vacío. El extra escribe `marca.txt`. `sembrar_tabla` comparaba las cadenas después de un `return`. El chequeo quedó antes. El `.mjs` de OpenBot no es ejecutable. Se invoca con `node`. La skill ya no enseña `` `lane: fast` `` y el test de formato la sigue. Dos `write_text` de `motor/flota/control.py` partían `encoding` en otra línea y el scanner UTF-8 los marcaba. Quedaron en una línea.

comment-sicko borró 16 comentarios. Los dos pendientes se actuaron antes del recorrido final. `motor flota` traduce `OSError`, `ValueError`, `RuntimeError` y `subprocess.CalledProcessError`. `motor director` traduce `OSError`, `ValueError`, `RuntimeError` y `sqlite3.Error`. El doble local se llama `DobleOpenBot`.

## Muse

`~/.zshenv`, `~/.zprofile`, `~/.zshrc` y `~/.tmux.conf` no exportan `TBH_CREDENTIAL_BACKEND`. El daemon vivo es `/Library/LaunchDaemons/com.goncloud.motor.plist`. Corre como `UserName` `dn`, con `python3.14 -m motor servicio --home /Users/dn/.motor`. Su entorno solo declara `HOME` y `PATH`. No se reinició.

`~/.config/muse/auth.json` existe, modo 600. Su `providers.meta.storage` es `keychain`, `mechanism` es `oauth` y `obtained_via` es `device_code`. No se copió.

Con `TBH_CREDENTIAL_BACKEND=file`, `muse usage --json` sale 1 y pide `muse login`. En esta sesión, sin esa variable, sale 0. `[rutas.muse]` de la conf compartida sigue forzando el archivo. Se probó quitarlo en el worktree y se revirtió. El daemon no está en la sesión gráfica, y no quedó demostrado que sin la variable Muse entre desde ahí. La conf viva es `/Users/dn/motor-conf` (`~/.motor/conf`) y no se editó.

## Fase 5

1. Apuntar la fuente con `GONCLOUD_ROUTING_SOURCE` o `python scripts/claw_models.py --source PROYECCION`. Hasta entonces Hermes y Claw siguen en `model-routing.source.json`.
2. Dejar un solo escritor. Sustituir `scripts/hermes_autoupdate.py`, `scripts/claw_autoupdate.py` y `scripts/vigila_modelos.py` de Hermes. En esta entrega siguen instalados y no se desactivaron.
3. Activar el manifiesto `flota/manifesto.toml` como fuente. Lo escribe `motor flota sembrar`. No se rellena a mano.
4. La reversa de ensayo es `sh ops/reversa_ensayo.sh --home DIR`. No llama a `python -m motor`. La reversa viva `ops/vuelve-a-la-anterior.sh`, cuando la decisión de formato es `puede_volver`, corre `python -m motor formato` en la PC y aborta si eso falla. No uses el shell de ensayo contra `$HOME`.
5. No correr `ops/instala-todo.sh` contra los homes instalados en esta fase.
6. La instalación paralela son `generaciones/<sha>` y el symlink `actual`. Cada trabajo guarda su sha en `trabajos/<id>.json`.
7. Muse. No volver a poner `TBH_CREDENTIAL_BACKEND=file` en el shell interactivo. El acceso del daemon queda pendiente. Hay que resolverlo en su contexto, sin copiar el llavero al repo y sin reinstalación global.

## Limpieza

El `finally` del recorrido borra las bases temporales y mata procesos cuyo comando las nombra. No quedaron directorios `encargos-3-4` en `/tmp`. No se copió `auth.json`. El token del API de ensayo vive en el script y no está en el JSON. Los SHA de las generaciones de ensayo son de repos temporales ya borrados.
