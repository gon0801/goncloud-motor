# Entrega integrada y despliegue de encargos

`--entrega desplegado` autoriza ejecutar las recetas de las fases que despliegan.
Una fase del plan con `lane: fast` o `lane: gate`, sin línea `despliegue:`, termina
en `integrado`. Una fase `release`, o `gate` con receta explícita, exige ejecutar
el despliegue. Si el motor no puede identificar el contrato de la fase, conserva
la exigencia global y espera; no deduce que la fase sea documental por su título.

El supervisor lee el bloque correspondiente del plan mencionado en el encargo
o adjuntado explícitamente. La política queda registrada por fase. Para encargos
nuevos, `.interno/fases.json` también admite `requiere_despliegue` y un objeto
`despliegue` con `desplegar`, `checklist` y `rollback`. El plan declarado tiene
prioridad sobre esa propuesta.

Primero se busca un bloque cuyo ID coincida con la fase. Si ninguno coincide,
se admite la correspondencia por posición entre la lista completa de fases y
los bloques del plan: deben tener igual cantidad, IDs únicos y ninguna
coincidencia de nombres que contradiga el orden. Debe haber un único documento
que permita esa correspondencia. Al adoptar la primera política se registra
el mapa completo fase→bloque y el archivo del plan; reordenar las fases o los
bloques no cambia ese mapa. La coincidencia explícita por ID mantiene prioridad
sobre el mapa posicional al adoptar las siguientes fases. La fuente conserva
el bloque y su posición inicial,
por ejemplo `#Bloque A (posición 1/9)` para la fase `propuesta`. Si la asociación
es ambigua o un bloque asociado ya no existe, la política sigue desconocida.

Una receta contiene tres rutas relativas a scripts del repositorio:

```text
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
```

Si una fase de despliegue omite la receta, se usa `[deploy]` de `.motor.toml`.
Una receta explícita inválida bloquea la ejecución; no se sustituye por otra.
El motor ejecuta los scripts desde una copia del **commit integrado**, con
`MOTOR_SHA` igual al SHA del merge y `MOTOR_RAIZ` apuntando a esa copia.
Primero ejecuta `desplegar`, después `checklist`. Ambos deben salir con código 0.
El checklist es responsable de comprobar que producción contiene `MOTOR_SHA`.

La máquina debe declarar `ssh-goncloud` en `maquina.toml`: primero se busca en la
base del encargo; después en `MOTOR_HOME`, o en `~/.motor` si no está definido.
Se reutilizan el Bash, PATH y entorno de esa configuración. Las credenciales
permanecen en la máquina, fuera del repositorio. El supervisor de esta versión
ejecuta la receta en su propio host: no envía una receta a otro host ni la
sustituye por el destino de ensayo. Sin la capacidad o los scripts, espera con
la causa concreta y no consume otro turno de revisión.

Un trabajador separado ejecuta la receta, con un tope de 900 segundos por script.
Si falla el despliegue o el checklist, incluido código 75 o timeout, intenta
`rollback` con el mismo SHA. No marca la fase como desplegada. Una parada corta
el comando en curso y permite completar la reversa; el supervisor no confirma
`detenido` mientras siga vivo ese trabajador o su comando. Si se detuvo antes
de enviar el primer script, continuar puede reintentar sin repetir producción.

La evidencia queda fuera del checkout en
`<base>/despliegues/<identidad>/recibo.json`: encargo, fase, repositorio, SHA,
receta, código de salida, stdout y stderr de cada paso. El cierre vuelve a
comprobar ese recibo. Un JSON de `DestinoEnsayo`, una afirmación del agente o
un pedido de efecto no acreditan despliegue.

Una ejecución fallida o interrumpida después de enviar un efecto necesita
conciliación; el motor no vuelve a desplegar el mismo SHA a ciegas. Los recibos
y los procesos registrados permiten comprobar qué ocurrió antes de decidir
una nueva entrega. No se borra un recibo para forzar un reintento.

Cuando GitHub rechaza un merge por checks requeridos en curso, el encargo espera
y vuelve a consultar el mismo SHA. Un check requerido fallido o una revisión
humana obligatoria pide intervención. No se usa `--admin`. Una aprobación
repetida que no resuelve el mismo bloqueo tras un relevo tampoco lanza
revisores indefinidamente: conserva el encargo abierto y pide intervención.

Una parada del operador conserva el intento como interrumpido: su salida no
consume un relevo ni se clasifica como falla del proveedor. Al cambiar de fase
se reinician los contadores de bloqueo y los modelos usados para relevar ese
rol. El historial de modelos se conserva para auditoría e independencia de la
revisión. Si se agotan los modelos autorizados, el encargo queda en
`necesita_usuario`; una cuota temporal mantiene su espera. El agotamiento no
autoriza una entrega con pendientes. Tampoco se abre un PR sin commits sobre
su base.

Para un encargo legado detenido en una fase ya integrada, el contrato se adopta
al reanudar. No hace falta reescribir su historial ni repetir la fase. Después
de instalar este arreglo en la PC, el operador puede continuar con una clave
nueva:

```text
motor director continuar enc_fb7358452cdfe01b --base C:/Users/ehven/.motor-encargos --clave <nueva>
```

Si una versión anterior lo terminó mediante `cierre_forzado`, el operador
puede recuperar la fase que estaba abierta:

```text
motor director reabrir enc_fb7358452cdfe01b --base C:/Users/ehven/.motor-encargos --clave <clave-recuperacion>
```

`reabrir` restaura el estado de esa fase anterior al cierre forzado y conserva
las fases ya integradas, sus revisiones y recibos. Queda **detenido**: el
operador revisa el resultado y después usa `continuar` con otra clave.
La orden no revierte efectos externos ni permite reabrir un cierre normal o
una cancelación. El evento vincula la operación a la fase y firma del cierre;
repetir la clave es idempotente. Si la autoridad remota no confirma, devuelve
`autoridad_pendiente`; repetir esa misma clave reintenta el mismo envío.

Este cambio no modifica el plan de Orbit ni activa interruptores de producto.
El encendido de la pausa publicitaria sigue siendo una acción de David.
