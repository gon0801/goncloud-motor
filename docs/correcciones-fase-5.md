# Correcciones de la fase 5

Los cuatro hallazgos de la revisión quedaron corregidos en código,
integrados por PR y desplegados por host. Cada despliegue es local.
Cada libreta sigue independiente y sin `autoridad.json`.

PR integrados en esta ronda: [#88](https://github.com/gon0801/goncloud-motor/pull/88),
[#89](https://github.com/gon0801/goncloud-motor/pull/89),
[#90](https://github.com/gon0801/goncloud-motor/pull/90) y
[#91](https://github.com/gon0801/goncloud-motor/pull/91).
La evidencia breve está en [evidence/fase-5/correcciones.json](evidence/fase-5/correcciones.json).

## Corrección 1. El servicio tiene ruta y no arranca en silencio

- `motor/__main__.py` e `instala-encargos` exigen `--agente`. Sin ruta
  no devuelve plantilla y sale con código 2.
- `motor/servicio.py` valida la ruta contra los agentes autorizados y
  la escribe en la definición nativa de cada host.
- `motor/encargos/cli.py` hace fallar el supervisor cuando no tiene
  ruta ni rutas persistidas en sus encargos.

Rutas por host: Mac `grok` con respaldo `codex`. Mini `grok` con
respaldo `codex`. PC `codex`, porque en Windows el supervisor no
puede usar Grok. Las tres rutas se eligieron por disponibilidad real
comprobada en cada máquina, no por preferencia.

## Corrección 2. Servicio conectado con la flota y el canal

- `vincular_base` en `motor/servicio.py` escribe `flota-home.json` y
  `canal.json` en la base. Valida todo antes de escribir nada.
- La instalación exige `--flota-home` y `--canal`.
- Los transportes de aviso quedan en la base de cada máquina y fuera
  del repo. Devuelven recibo y responden a la conciliación.
- Hallazgo durante el despliegue: el cuerpo del aviso solo aceptaba
  una huella exacta y los jueces dejan la evidencia en prosa. Eso
  dejaba cada aviso incierto para siempre. Corregido en el PR #91.

## Corrección 3. La candidata inválida conserva la generación vigente

- En `motor/flota/actualizaciones.py`, una candidata que retiraría
  una herramienta vigente se rechaza. Las herramientas vigentes se
  leen de los lanzadores reales de la generación asignada.
- Una candidata sin ninguna herramienta utilizable tampoco se marca
  preparada.
- Hallazgo durante el despliegue en PC: el árbol capturado incluyó
  procesos de Windows Update anteriores al trabajador y el encargo
  nunca cerraba. Un proceso anterior a la raíz no cuenta como su
  árbol. Corregido en el PR #90.

## Corrección 4. La migración nace detenida y sin colisión

- La entrega migrada nace `detenido` en su primer estado durable, en
  `motor/encargos/dominio.py`. No existe una ventana habilitada.
- La clave lleva el largo del host. Reimportar encuentra las
  entregas viejas, reutiliza la misma identidad y las detiene por la
  vía soportada. El conflicto de identidad se trata explícito.

## Despliegue por host

- Mac: servicio LaunchAgent con ruta grok y respaldo codex. Base
  vinculada a `/Users/dn/flota` y canal openbot. La máquina no se
  reinició.
- Mini: servicio LaunchAgent con ruta grok y respaldo codex. Base
  vinculada a `/Users/gon/flota` y canal telegram.
- PC: arranque por sesión del usuario con ruta codex. Base vinculada
  a `C:\Users\ehven\flota` y canal telegram. La tarea programada se
  retiró porque el propio lanzador del motor rechaza trabajadores
  que no pueden separarse del grupo de la tarea. Es un límite del
  entorno de la tarea, no del supervisor en sesión.

## Encargos ejecutados y entregados

Ver [evidence/fase-5/correcciones.json](evidence/fase-5/correcciones.json).

## Pendientes y límites

- El arranque tras reinicio de la Mac sigue sin demostrar. David
  pidió no reiniciarla.
- El ciclo completo de comprobación tarda mucho por diseño. Cada
  encargo pasa por trabajador, comprobador y juez, y cada uno
  arranca una sesión completa. Eso no se cambió en esta ronda porque
  el brief pedía cerrar los cuatro hallazgos, no rediseñar el ciclo.
- OpenBot sigue sin parada propia demostrada.
