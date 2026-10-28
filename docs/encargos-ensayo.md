# Ejecutar un encargo de ensayo

> El protocolo de cierre que describe este documento (`procedimiento.json`
> obligatorio, juicio por clase global con cobertura) fue reemplazado por el
> [flujo de revisiones](flujo-revisiones.md). Este texto queda como registro
> del ensayo de la entrega 1.0.35.

Esta entrega conserva el flujo 1.0.35 y usa una base separada para los encargos nuevos. No instala servicios. La receta anterior de `ops/ensayo_encargos.py` por ejemplos y confirmaciones inyectadas fue retirada.

## Entregar un pedido normal

Ejecuta desde este checkout con Codex autenticado por suscripción. El adaptador usa `gpt-6.1-sol` con esfuerzo `xhigh`.

```sh
python3 -B ops/ensayo_encargos.py \
  --base /tmp/encargo-personal \
  --archivo /ruta/al/pedido.txt \
  --clave mensaje-original-1
```

También admite `--texto`. La clave y el contenido originales identifican la entrega. El script consulta el supervisor hasta observar un resultado o agotar `--tiempo`. Si el trabajo sigue pendiente al salir, solicita su parada y conserva la evidencia en la base. No inserta confirmaciones ni interpreta el contenido del pedido como comandos.

Para operar la instancia directamente, usa estos comandos. Sustituye `ID` por el identificador devuelto al recibir.

```sh
python3 -B -m motor encargar --base /tmp/encargo-personal --archivo /ruta/al/pedido.txt --clave mensaje-original-1
python3 -B -m motor supervisor --base /tmp/encargo-personal --agente codex --umbral 300
python3 -B -m motor ver ID --base /tmp/encargo-personal
python3 -B -m motor parar ID --base /tmp/encargo-personal --clave orden-parar-1
python3 -B -m motor corregir ID --base /tmp/encargo-personal --clave ajuste-1 --texto 'Ajuste con el trabajo detenido'
python3 -B -m motor continuar ID --base /tmp/encargo-personal --clave orden-seguir-1
```

Una retransmisión de una orden conserva su recibo y no repite la transición. `parada-local --base ... --clave ...` cierra la admisión local aunque la autoridad no responda. Una operación ya enviada puede terminar después de aceptar parar.

## Recuperar la ruta autorizada

El supervisor conserva la cadena solicitada y el agente efectivo en el registro. Si reinicia sin `--agente`, recupera esa cadena. Un respaldo debe enumerarse explícitamente y tener runtime, autenticación por suscripción y aislamiento administrado disponibles en ese host.

```sh
python3 -B -m motor supervisor --base /tmp/encargo-personal --agente codex --respaldo grok
```

Este ejemplo autoriza Grok como respaldo; omite `--respaldo` para mantener solo Sol. Una cuota conserva el reset o Retry-After informado. Si no queda una ruta disponible, el motor guarda el motivo y la fecha de reanudación. Un timeout de trabajo o una herramienta pendiente no provoca por sí solo un cambio de modelo. El relevo espera al cierre observado del árbol anterior. Los errores terminales de Codex y Grok y el stderr de un CLI que falla se distinguen del texto producido por herramientas.

## Conectar una autoridad de ensayo

`--autoridad /ruta/autoridad.json` copia la configuración a la base del ensayo. También puede prepararse `BASE/autoridad.json` antes de recibir. El host remoto ejecuta argumentos fijos y recibe los datos por stdin.

Ejemplo para las rutas temporales utilizadas en la demostración de esta entrega.

```json
{
  "host": "mini",
  "argv": [
    "--", "env",
    "PYTHONPATH=/tmp/motor-fase2/src",
    "MOTOR_ENCARGO_BASE=/tmp/motor-fase2/autoridad",
    "/Users/gon/.local/bin/python3", "-B", "-m", "motor", "encargos-aplicar"
  ]
}
```

Esas rutas son copias de ensayo, no un despliegue. La autoridad concede el conductor antes del arranque. Un candidato denegado conserva la identidad y el directorio del conductor anterior. Una configuración existente ilegible impide escrituras y no crea otra autoridad. La parada y el cierre local siguen funcionando.

En Windows, el transporte elige automáticamente el OpenSSH de Git instalado en `ProgramFiles/Git/usr/bin/ssh.exe`. Se comprobó OpenSSH 10.5p1 con entrada, salida y error mediante handles de archivo y argv directo, sin shell. El OpenSSH nativo 9.5p2 de esta PC se atascó al transportar evidencia grande. Si no existe el cliente compatible, el motor informa la capacidad ausente. `autoridad.json` admite `ssh` como ruta absoluta para un cliente compatible explícito. macOS conserva `ssh`. La selección del cliente no cambia host, argv remoto ni identidad del efecto. No se instala ni modifica un cliente global.

El destino implementado es un directorio de ensayo de la autoridad. No hay integraciones de producción en esta entrega. El agente recibe el formato interno de `pedido-efecto.json`: `id`, `operacion`, `clave` y `cuerpo`, todos textos no vacíos, como objeto o lista de objetos con IDs distintos. Una solicitud inválida conserva un error de instrumento accionable; no se confunde con ausencia de solicitudes. El pedido del usuario sigue siendo texto normal.

La identidad del efecto incluye destino, operación, clave y cuerpo. Un recibo incierto se concilia por esa misma identidad. El revisor puede emitir `producto_aprobado` con los IDs exactos de los efectos pendientes. Ese juicio aprueba el producto y sus comprobaciones; no afirma que el efecto ya ocurrió. El supervisor media la solicitud y compone un `cierre_comprobado` distinto, con el juicio original inmutable y los recibos reales. La Mini lo recompone contra su registro y consulta el destino. Un recibo de publicación de texto demuestra identidad y contenido; un resultado que necesite inspección adicional no queda acreditado solo por ese recibo.

Un resultado comprobado conserva su concesión mientras falte el ACK de su publicación canónica. Una incertidumbre de efecto también la conserva. Un conflicto definitivo devuelve trabajo reparable y libera el conductor; no retiene el token por una aprobación anterior.

## Inspeccionar el recorrido

El pedido, las restricciones, las órdenes y los recibos se reconstruyen desde `registro.sqlite`. Los productos viven en `espacio/ID`. Los procesos y las observaciones del supervisor viven en `ejecucion/ID/intento-*`. `actual` señala el intento concedido. Cada bootstrap publica su identidad antes de liberar al trabajador o al comprobador.

El responsable conserva su procedimiento interno en `procedimiento.json`. El supervisor ejecuta los argv dentro de un comprobador administrado y guarda `observaciones.json`. Un revisor recibe el pedido completo, esas ejecuciones y acceso de lectura al producto. Su juicio debe explicar la cobertura y vincular ejecuciones reales. Una nota o un código cero aislados no terminan el encargo. Los errores de procedimiento o de JSON vuelven como trabajo concreto reparable.

Los eventos estructurados del CLI y los procesos observados distinguen herramientas ocupadas de sesiones sin actividad. El tamaño del log no acredita progreso. `contexto.md` y el registro conservan el trabajo entre turnos. Cada intento usa un HOME privado con autenticación de suscripción; no hereda configuración, credenciales de producción ni el agente SSH.

En macOS, Seatbelt niega escrituras globales y la lectura del HOME original. El responsable puede escribir su espacio y runtime; el revisor tiene el producto de solo lectura.

En Windows, Codex conserva `Experimental_CreateProcessInSandbox` de `processmodel.dll` y el contrato SBOX 0.1.0. Muse y Kimi usan `CreateProcessSecurityEnvironment` con PSEC 1.1 para permitir consultas y enumeración de directorios antecesores sin conceder lectura de sus archivos. Ambos caminos conservan el Job, la estación y el escritorio privados. Las ejecuciones comprobadas de la PC se inician en la sesión normal del usuario, fuera del Job de OpenSSH.

El ensayo comparativo comprueba las denegaciones sobre registro, destino, credenciales privadas y otros espacios, y que el revisor no puede escribir el producto. Un fichero ajeno publicado por su ACL para `ALL APPLICATION PACKAGES` sigue legible en ambos caminos; no se anuncia una garantía de token LPAC que ese comportamiento no demuestra. La enumeración PSEC no autoriza leer los demás archivos de esos directorios.

Cada proceso se crea suspendido, entra en un Job y publica PID, inicio y Job antes de liberar al modelo o comprobador. El Job controla la vida del árbol. AppContainer y BFS controlan sus permisos. El revisor puede escribir su runtime, pero no el producto. Registro, destino, otros espacios y credenciales ajenas quedan fuera de las raíces concedidas. La estación privada permite inicializar USER32 sin abrir un escritorio ajeno. El auxiliar de consola del sistema se distingue por imagen e identidad para observar actividad, y permanece dentro del árbol que debe cerrarse.

En Windows, los comandos del agente permiten TCP 443 hacia las direcciones públicas del proveedor al preparar el intento: `chatgpt.com` y `auth.openai.com` para Codex, `api.meta.ai` para Muse, y `api.kimi.com` y `auth.kimi.com` para Kimi. Este límite opera por direcciones IP, que pueden compartirse entre dominios. Los comprobadores no tienen red. Se comprobaron denegaciones reales hacia la Mini, otro destino de Internet y un listener de loopback. No se modifican ACL de instalaciones, reglas globales de firewall, usuarios, servicios ni la selección guardada de sandbox de Codex. Un host sin la capacidad de aislamiento necesaria no ofrece esa ejecución administrada. Grok no se ofrece como respaldo en Windows.

Para repetir la comprobación de permisos y cierre en otra base propia de Windows, ejecuta lo siguiente. La segunda variante usa el perfil de red del agente sin invocar un modelo.

```powershell
py -3 ops/ensayo_aislamiento.py --base C:\ruta\propia\jaula-comprobador
py -3 ops/ensayo_aislamiento.py --base C:\ruta\propia\jaula-agente --internet-suscripcion --tcp IP_DE_ENSAYO:PUERTO
py -3 ops/ensayo_aislamiento.py --base C:\ruta\propia\jaula-psec --psec
```

```sh
python3 -B ops/ensayo_encargos.py --remoto-local /ruta/temporal/nueva
```

Este modo acredita identidad, cierre del padre y su hijo, e ingreso por stdin. No acredita aislamiento ni razonamiento de un agente.

## Evidencia de cierre de fase 2

La evidencia nueva está en `/Users/dn/.motor-resguardo/fase2-cierre-sol`. El registro de decisiones es `decisions.tsv`. Los manifiestos `fuente-final.json`, `fuente-cierre-corregido.json`, `fuente-esquema-corregido.json` y `fuente-transporte-corregido.json` conservan las huellas de los archivos Python copiados a las máquinas. El último acredita el código `2ec01d4c046bd239770793109d2d7ae568db9a79`, idéntico en PC y Mini.

- `permisos-red-final.jsonl`, `red-restringida-final.json` y `agente-aislado-real.json` acreditan permisos de responsable y revisor, herencia a hijos, red restringida y herramientas reales del modelo, incluidas shell y `apply_patch`.
- `cierre-fallo-acl.json` y `perfiles-corregidos.json` acreditan cierre del árbol, limpieza tras fallo de arranque y eliminación de perfiles entre comandos. Las ACL de Python, Windows y Codex conservaron sus huellas. `actividad-despues.json` acredita que una herramienta deja de aparecer activa al terminar.
- `rutas-control.json` y `protocolo-runtime.json` usan proveedores dobles declarados para comprobar cuota, reset, retry y errores terminales. Los procesos y el protocolo son reales. No acreditan inferencias de un respaldo.
- `cierre-efectos-control.json` usa un juicio doble declarado y un destino real: rechaza cierre sin recibo, recibo incierto, confirmación prematura y cuerpo distinto; concilia tras perder la respuesta con `n=1`. `liberacion-despues.json` distingue el conflicto definitivo de un ACK pendiente. `solicitudes-control.json` ejecuta el parser y el paquete de revisión con solicitudes dobles declaradas.
- `cli-utf8.json` acredita el transporte Windows stdin/stdout UTF-8 con JSON y Unicode exactos. Las primeras capturas del controlador tenían sustituciones al decodificar la codepage local; la corrección no altera el registro ni el producto.
- `transporte-grande-final.json` acredita que el transporte Windows final selecciona Git OpenSSH y entrega 210.013 bytes con Unicode y SHA256 exactos, sin recortar observaciones.

El recorrido real es `e2e-1d54ca1`, encargo `enc_64688cce4677b389`, con responsable y revisor Sol por suscripción y sin respaldo. Empezó en `1d54ca1`; encontró el ciclo aprobación/efecto y la falta del contrato de solicitud. Se detuvo y continuó por las órdenes públicas después de `e361771` y `391e4d5`. El resultado ya comprobado se publicó después de corregir el transporte en `2ec01d4`, sin otra inferencia. Se conservaron los intentos y fallos anteriores. No se restauraron fixtures. No hubo ediciones manuales de SQLite, `actual`, producto, procedimiento, juicio o recibos para forzar el cierre. Las correcciones del producto y sus instrumentos las realizó el agente.

Las bases permanecen separadas de cualquier servicio instalado:

- PC: `C:\Users\ehven\AppData\Local\Temp\motor-f2-e2e-1d54ca1`.
- Autoridad Mini: `/tmp/motor-f2-cierre-sol-20261027/autoridad-1d54ca1`.
- Código PC: `C:\Users\ehven\AppData\Local\Temp\motor-f2-final-sol\src`.
- Código Mini: `/tmp/motor-f2-cierre-sol-20261027/src`.

El puente de ensayo ejecuta la CLI real con argv fijos y datos por stdin. Sus controles externos simulan desconexión antes del envío o pérdida de una respuesta después de ejecutar una operación real. No fabrica resultados ni modifica el registro. Las capturas `02` a `19` conservan el cierre y reinicio del supervisor sin duplicar al trabajador, parada del árbol, retransmisión de la orden antigua sin reaplicarla, recuperación de un CLI suspendido solo después de cerrar el escritor anterior y corte/reconexión de la autoridad conservando el producto.

Las capturas `49` a `54` y `fin.json` acreditan el cierre canónico en generación 11. Se perdieron deliberadamente las respuestas del efecto y del resultado después de ejecutarlos realmente. El motor concilió el mismo efecto con `n=1`, recibo `dst_d31bbd7d2f54f8e2`, clave `f2-catalogo-20261027` y cuerpo `catalogo verificado fase2`. La Mini muestra `terminado` y conserva la causa y la evidencia real del cierre; no solo la vista local. La recuperación del ACK no abrió otra inferencia.

La revisión independiente está en `/Users/dn/.motor-resguardo/fase2-cierre-lead`: `pc-permisos.json`, `pc-registro.json`, `catalogo-comprobado-pc.json` y `autoridad-comprobada-mini.json`. Comprobó las consultas `manual` y `guia`, Unicode, errores por ID ausente o inexistente y preservación del catálogo con SHA256 `e3200d141f0f0499f3809006a8039e439f1a4f03e206083860f17da61b53bb80`. La lectura final de la Mini fue de solo lectura sobre SQLite y destino. `producto-huellas-final.json` confirma que los archivos de la aplicación conservaron las huellas observadas antes de la última revisión.

Los runtimes contienen copias privadas de autenticación mientras se ejecutan. No archives ni compartas raíces completas. Conserva únicamente productos, registros, observaciones, juicios y evidencia seleccionada sin autenticación.

`limpieza-pc.json`, `limpieza-mac.json` y `limpieza-mini.json` acreditan el cierre: árbol y procesos propios ausentes, canario Mini ausente, perfiles propios retirados y copias privadas de autenticación eliminadas. Se conservaron los perfiles ajenos y la autenticación original, además del producto y su evidencia. Las bases permanecen para inspección; no quedó un supervisor instalado.

## Evidencia histórica anterior

Los artefactos históricos están en `/Users/dn/.motor-resguardo/sol-encargos-1-2`. La corrida restaurada descrita en esta sección no acredita el nuevo recorrido de cierre de fase 2. La evidencia versionada excluye autenticación. El runtime local contiene copias privadas de `auth.json` para la suscripción y no forma parte del paquete publicable. No archives ni compartas esa raíz completa.

- `real` conserva el encargo de agenda con responsable y revisor Sol reales. El supervisor ejecutó los casos de fechas y el ejemplo de consola. `receta-vigente.jsonl` acredita la receta actual sobre la misma entrega terminada, sin otra inferencia.
- `canonico-real` conserva el incidente del comprobador Python defectuoso, el producto TOML válido y el efecto previo. `canonico-final.json` acredita el resultado local y la vista terminada de la Mini, con la misma huella del producto.
- `restauracion-fixture.json` declara la restauración del ensayo afectado por código intermedio. Se revalidaron el juicio real y las observaciones existentes sin editarlos. Esa restauración no demuestra una migración automática de estados antiguos.
- `espera-conexion-confirmada.json` acredita la espera durante el corte del ejecutor. `reconexion-selectiva.json` usa el juicio real conservado para comprobar pérdida selectiva del ACK del resultado, retención de la concesión y publicación posterior sin inferencia.
- `control-final.json` conserva las reproducciones de destino, cuerpo, configuración inválida, autoridad única, ingreso de archivo, caída del bootstrap y orden efectivo de parada frente al envío.
- `comprobador-timeout.json` acredita el límite real de 30 segundos y el hijo cerrado. La revisión independiente conserva `comprobador-lacv_ltq/revision-lead.json` bajo `/Users/dn/.motor-resguardo/reparacion-encargos-sol` para la parada sin escritura tardía.
- `runtime-colgado.json` acredita recuperación de un CLI Sol real con su launcher congelado mediante SIGSTOP. Sus hijos continuaron trabajando y el supervisor esperó sus herramientas. Después cerró el árbol sin herramienta ni hecho nuevo. No acredita un cuelgue espontáneo de Grok.
- `remotos.json` acredita procesos nativos y stdin en Mini y Windows. `autoridad-remota.json` acredita Mac y PC hacia la Mini. La revisión independiente leyó el destino y conservó `recibo-mini-lead.json`, con `n=1` y el mismo recibo de `pr-26-ensayo`.

La regresión `tests.test_encargo_motor06` usa un doble explícito del juicio del revisor, comandos reales y un efecto de ensayo. La reproducción independiente `ack-efecto-7fj4mc66/resultado.json` usa también un juicio doble y un efecto real para demostrar conciliación tras perder su respuesta, con una sola aplicación. Ninguna de esas dos pruebas demuestra razonamiento de un modelo. Los encargos de agenda y TOML sí usaron el modelo real.

El adaptador Grok recibe autenticación aislada y procesa `streaming-json`, pero esta ronda usó Sol. No se validó aquí un cuelgue real de Grok. No se conectaron bots, servicios instalados, actualizadores ni destinos de producción.
