# Cierre de fase 5: activación del sucesor

## Resultado general

PARCIAL SEGURO. La integración se mergeó en los cuatro repos y el sucesor
quedó corriendo en la autoridad con los encargos trasladados. La política
común y las instrucciones vivas de los directores quedan pendientes con su
causa registrada. Ningún paso dejó dos escritores activos sobre el mismo
recurso.

## Integración

| Repo | PR | Merge |
| --- | --- | --- |
| goncloud-motor | [#85](https://github.com/gon0801/goncloud-motor/pull/85) | d8f4539e557c |
| goncloud-hermes | [#94](https://github.com/gon0801/goncloud-hermes/pull/94) | 8e5fb050fd3b |
| goncloud-openclaw | [#371](https://github.com/gon0801/goncloud-openclaw/pull/371) | 1daa415466cf |
| goncloud-motor-conf | [#28](https://github.com/gon0801/goncloud-motor-conf/pull/28) | ab9dc8fe9da0 |

La revisión independiente aprobó los cuatro antes del merge. Los gates
rojos fueron falsos positivos de gitleaks sobre ids y rutas de la
evidencia, ignorados por fingerprint, y expectativas v6 de la tabla en
dos archivos de prueba de Hermes, actualizadas a v7. La rama principal
de Hermes es feat/hermes-team.

## Pasos

- 1. Alcance e inventario. REALIZADO. Ver
  [evidence/fase-5/inventario.json](evidence/fase-5/inventario.json).
  Resguardos SQLite coherentes en Mac y Mini bajo
  ~/.motor-resguardo/fase-5-20261028.
- 2. Operaciones de producción. PARCIAL. El servicio del sucesor es un
  LaunchAgent en el dominio del usuario. launchd no tenía sudo sin
  contraseña para un LaunchDaemon nuevo. El arranque tras reinicio
  completo sin sesión iniciada no está demostrado.
- 3. Migración repetible. REALIZADO. Importador con identidad estable,
  probado sobre copia coherente y aplicado en Mini. Repetirlo devuelve
  los mismos encargos.
- 4. Integración. REALIZADO. PR, revisión independiente y merge en los
  cuatro repos, con los SHA de arriba.
- 5. Despliegue. PARCIAL. Mini es la autoridad con el servicio corriendo
  sobre ~/.motor-encargos. La Mac consulta y promueve contra esa
  autoridad por SSH. La PC tiene los repos integrados clonados, pero su
  servicio de encargos no se instaló en esta pasada.
- 6. Traslado de encargos. REALIZADO. Mini aportó diez encargos y la Mac
  quince, todos detenidos y sin escrituras. MOTOR 06 quedó como encargo
  detenido con sus bloques B y C pendientes. Los PR 26 y 27 no se
  reaplicaron.
- 7. Política común y réplicas. PENDIENTE. El ciclo de flota en PC falló
  en un instalador node durante la preparación de componentes. No se
  pausó vigia-modelos porque el reemplazo todavía no corre. Cuando el
  ciclo corra, se pausa el cron d710c8527b7f y se crea la entrada única
  del controlador por el mismo mecanismo de cron de Hermes.
- 8. Sistema viejo, directores y retiro. PARCIAL. La admisión del motor
  viejo se cerró con retención en Mac y Mini. Su daemon sigue solo como
  lector histórico. El claw-autoupdate de Mini quedó desactivado con su
  definición guardada. Las skills vivas de Hermes y Claw no se
  reinstalaron en los perfiles en producción en esta pasada. El vigía
  de la Mac se conservó porque es observador.
- 9. Pruebas como usuario final. PARCIAL. Desde la Mac se creó un
  encargo directo con un ejecutor real, se observó su cierre, se
  promovió a durable en la autoridad y la consulta canónica devolvió
  durable. El recorrido completo con trabajo durable ejecutado por la
  autoridad y aviso al canal quedó demostrado en las rondas de
  corrección. La prueba en ventana con David no se hizo.

## Reversa

- Encargos: los importados están detenidos. Desactivarlos no reejecuta
  nada. El almacén viejo nunca se modificó.
- Servicio Mini: launchctl bootout gui/501/ai.goncloud.motor-encargos y
  borrar su plist. El motor viejo conserva su retención reversible con
  su propio comando de mantenimiento.
- Claw-autoupdate: launchctl enable gui/501/ai.goncloud.claw-autoupdate
  y bootstrap con el plist guardado en el resguardo.
- Política: como vigia-modelos nunca se pausó, no hay reversa que
  aplicar en PC todavía.

## Residuos y pendientes con dueño

- PC: home de flota parcialmente preparado en C:\Users\ehven\flota. Es
  del ensayo de despliegue y se conserva hasta que el ciclo corra.
- Mini: /Users/gon/ensayo-flota-r4 y la copia del historial Mac en
  /Users/gon/copia-mac. Son productos de ensayo declarados.
- El siguiente paso con dueño es corregir el instalador node que el
  ciclo de flota invoca en PC, o declarar ese componente pendiente en
  el propio ciclo, y después ejecutar el paso 7 completo.
