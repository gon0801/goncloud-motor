# Cierre de fase 5: activación del sucesor

## Resultado general

PARCIAL SEGURO. La integración se mergeó en los cuatro repos y el sucesor
quedó corriendo en la autoridad con los encargos trasladados. La política
común v7 ya se publica desde PC y converge en Mini y Mac. Las skills
vivas de los directores ya se reinstalaron en los perfiles que las
reciben. La prueba final en ventana con David ya se hizo en Hermi y
en Claw. Ningún paso dejó dos escritores activos sobre el mismo
recurso.

## Integración

| Repo | PR | Merge |
| --- | --- | --- |
| goncloud-motor | [#85](https://github.com/gon0801/goncloud-motor/pull/85) | d8f4539e557c |
| goncloud-hermes | [#94](https://github.com/gon0801/goncloud-hermes/pull/94) | 8e5fb050fd3b |
| goncloud-openclaw | [#371](https://github.com/gon0801/goncloud-openclaw/pull/371) | 1daa415466cf |
| goncloud-motor-conf | [#28](https://github.com/gon0801/goncloud-motor-conf/pull/28) | ab9dc8fe9da0 |
| goncloud-motor (fix) | [#86](https://github.com/gon0801/goncloud-motor/pull/86) | 71e8e313fb0a |

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
  contraseña para un LaunchDaemon nuevo. El arranque tras reinicio sí
  se demostró el 2026-10-27. Mini reinició y el servicio
  ai.goncloud.motor-encargos levantó solo con pid 583 a los 32
  segundos del arranque. El registro durable conservó sus 98 eventos.
  Sigue en el dominio del usuario, así que depende de que la sesión
  de gon exista al arrancar, como ocurre hoy con su inicio automático.
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
- 7. Política común y réplicas. REALIZADO. Ver
  [evidence/fase-5/politica-comun.json](evidence/fase-5/politica-comun.json).
  El publicador de PC generó el snapshot v7 (manifiesto con sha256
  89daac5f). Las réplicas de Mini y Mac lo importaron y aplicaron la
  misma generación de herramientas bddd4ee5, con fuente Gon-PC. Los
  fallos previos venían de un instalador node y de un repo Hermes
  local en una rama vieja. Ambos se corrigieron antes de activar el
  reemplazo. Se creó el cron único
  flota-controlador b7d546da8335 en el perfil claw, su primer ciclo
  disparado terminó ok y apenas entonces se pausó vigia-modelos
  d710c8527b7f por el subcomando nativo. Queda un solo escritor.
- 8. Sistema viejo, directores y retiro. PARCIAL. La admisión del motor
  viejo se cerró con retención en Mac y Mini. Su daemon sigue solo como
  lector histórico. El claw-autoupdate de Mini quedó desactivado con su
  definición guardada. El vigía de la Mac se conservó porque es
  observador. Las skills vivas sí se reinstalaron el 2026-10-27, solo
  en los perfiles que las reciben. En Hermes, el director claw quedó
  con la skill motor nueva con sha256 6c30cd9d y la sonda de poteto en
  vivo dio OK. En Claw de Mini, los agentes main, implementer e
  ingenieria quedaron con la skill motor nueva con sha256 1a0e7bfd.
  Las versiones anteriores quedaron en el resguardo de cada máquina.
  El redespliegue completo de configure_team no se corrió. Su
  validación choca con el SOUL local de claw y su bandera para
  conservarlo tiene un defecto. No se forzó ningún SOUL ajeno.
- 9. Pruebas como usuario final. REALIZADO. Desde la Mac se creó un
  encargo directo con un ejecutor real, se observó su cierre, se
  promovió a durable en la autoridad y la consulta canónica devolvió
  durable. El recorrido completo con trabajo durable ejecutado por la
  autoridad y aviso al canal quedó demostrado en las rondas de
  corrección. La prueba en ventana con David se hizo el 2026-10-27.
  Hermi respondió como director listo para recibir el pedido y sin
  cambiar archivos. Claw respondió lo mismo tras el reinicio de Mini.
  Claw tardó unos cinco minutos porque su gateway seguía preparando
  las bases de sus agentes. Su salud respondió bien a las 13:56.

## Reversa

- Encargos: los importados están detenidos. Desactivarlos no reejecuta
  nada. El almacén viejo nunca se modificó.
- Servicio Mini: launchctl bootout gui/501/ai.goncloud.motor-encargos y
  borrar su plist. El motor viejo conserva su retención reversible con
  su propio comando de mantenimiento.
- Claw-autoupdate: launchctl enable gui/501/ai.goncloud.claw-autoupdate
  y bootstrap con el plist guardado en el resguardo.
- Política en PC: pausa el controlador y reactiva el vigía. Nunca
  dejes ambos activos. Corre
  `hermes -p claw cron pause b7d546da8335` y luego
  `hermes -p claw cron resume d710c8527b7f`. La definición previa del
  cron quedó guardada fuera del repo en el resguardo de PC.
- Réplicas: borra el home de flota de cada réplica o revierte su
  generación con `ops/reversa_ensayo.py --home <home-flota>`. Esa
  reversa no toca los encargos ni la autoridad de Mini.

## Residuos y pendientes con dueño

- PC: el home C:\Users\ehven\flota ya es la instalación estable del
  publicador. No es un residuo.
- Mac: el home estable de la réplica es /Users/dn/flota.
- Mini: el home estable de la réplica es /Users/gon/flota. Los
  residuos de ensayo /Users/gon/ensayo-flota-r4 y
  /Users/gon/copia-mac se borraron el 2026-10-27 con autorización de
  David. La copia era eso, una copia. El historial original no se
  tocó. Los resguardos quedan fuera del repo.
- PC: se borraron el home de ensayo .hermes-ensayo34d, los scripts
  temporales y las carpetas npm-p-* y prueba-* dentro de
  C:\Users\ehven\flota. El home estable del publicador quedó intacto.
- Decisión posterior de David, ya tomada el 2026-10-27: las tres
  máquinas son independientes y cada una tiene su propia libreta de
  encargos. Ver
  [decision-maquinas-independientes.md](decision-maquinas-independientes.md).
  La autoridad única de Mini contradice esa regla y debe retirarse
  en un paso nuevo. Ese paso no es parte de lo ya desplegado y queda
  pendiente de implementar. Hoy PC publica la política y no tiene
  servicio de encargos local. La Mac consulta a Mini como autoridad.
- OpenBot sigue sin una parada propia demostrada. Un timeout no
  cancela su entrega. Esa capacidad no se le atribuye.
- Por ese pendiente de decisión la fase 5 queda PARCIAL SEGURA. No
  se declara LISTO.
