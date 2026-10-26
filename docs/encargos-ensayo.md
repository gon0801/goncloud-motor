# Ejecutar un encargo de ensayo

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

## Conectar una autoridad de ensayo

`--autoridad /ruta/autoridad.json` copia la configuración a la base del ensayo. También puede prepararse `BASE/autoridad.json` antes de recibir. El host remoto ejecuta argumentos fijos y recibe los datos por stdin.

Ejemplo para las rutas temporales utilizadas en la demostración de esta entrega.

```json
{
  "host": "mini",
  "argv": [
    "--", "env",
    "PYTHONPATH=/tmp/motor-sol-f12-20261026-2329/src",
    "MOTOR_ENCARGO_BASE=/tmp/motor-sol-f12-20261026-2329/autoridad-real",
    "/Users/gon/.local/bin/python3", "-B", "-m", "motor", "encargos-aplicar"
  ]
}
```

Esas rutas son copias de ensayo, no un despliegue. La autoridad concede el conductor antes del arranque. Un candidato denegado conserva la identidad y el directorio del conductor anterior. Una configuración existente ilegible impide escrituras y no crea otra autoridad. La parada y el cierre local siguen funcionando.

El destino implementado es un directorio de ensayo de la autoridad. No hay integraciones de producción en esta entrega. La identidad del efecto incluye destino, operación, clave y cuerpo. Un recibo incierto se concilia por esa misma identidad. Un resultado comprobado conserva su concesión hasta publicarse canónicamente.

## Inspeccionar el recorrido

El pedido, las restricciones, las órdenes y los recibos se reconstruyen desde `registro.sqlite`. Los productos viven en `espacio/ID`. Los procesos y las observaciones del supervisor viven en `ejecucion/ID/intento-*`. `actual` señala el intento concedido. Cada bootstrap publica su identidad antes de liberar al trabajador o al comprobador.

El responsable conserva su procedimiento interno en `procedimiento.json`. El supervisor ejecuta los argv dentro de un comprobador administrado y guarda `observaciones.json`. Un revisor recibe el pedido completo, esas ejecuciones y acceso de lectura al producto. Su juicio debe explicar la cobertura y vincular ejecuciones reales. Una nota o un código cero aislados no terminan el encargo. Los errores de procedimiento o de JSON vuelven como trabajo concreto reparable.

Los eventos estructurados del CLI y los procesos observados distinguen herramientas ocupadas de sesiones sin actividad. El tamaño del log no acredita progreso. `contexto.md` y el registro conservan el trabajo entre turnos. Cada intento usa un HOME privado con autenticación de suscripción; no hereda configuración, credenciales de producción ni el agente SSH.

En macOS, la jaula niega las escrituras globales y la lectura del HOME original. Solo permite el espacio concedido y el runtime del intento. El revisor tiene el producto de solo lectura. Windows no tiene una jaula equivalente en esta entrega y rechaza lanzar trabajadores administrados. Su transporte y control nativos pueden comprobarse por separado.

```sh
python3 -B ops/ensayo_encargos.py --remoto-local /ruta/temporal/nueva
```

Este modo acredita identidad, cierre del padre y su hijo, e ingreso por stdin. No acredita aislamiento ni razonamiento de un agente.

## Evidencia de esta reparación

Los artefactos locales están en `/Users/dn/.motor-resguardo/sol-encargos-1-2`. La evidencia versionada excluye autenticación. El runtime local contiene copias privadas de `auth.json` para la suscripción y no forma parte del paquete publicable. No archives ni compartas esa raíz completa.

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
