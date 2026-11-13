# Modelo del centro del motor de encargos

Borrador para aprobación de David. Estado: propuesta, sin implementar.

El centro del motor es la parte que lleva la cuenta de en qué va cada encargo y
decide el paso siguiente. Hoy vive en `motor/encargos/ciclo.py` y
`motor/encargos/dominio.py`. Este documento fija cómo debe funcionar ese centro
antes de reconstruirlo. Ningún cambio de código de la reconstrucción empieza
hasta que David apruebe las decisiones de la última sección.

Los números de este documento salen de `origin/main` en `eac9899` y de una copia
del registro de la Mac tomada el 2026-11-12. El inventario completo y los guiones
que lo regeneran no viven en este repo, porque nombran repos y encargos privados.
David los tiene en su máquina.

## Lo que el motor tiene que cumplir

Esta lista la confirmó David el 2026-11-12. Es la vara con la que se da por
buena la reconstrucción.

1. Recibe un encargo y lo termina solo, de punta a punta.
2. Lo que reporta es verdad. "Entregado" significa entregado, desplegado y
   limpio. No deja ramas, temporales ni basura.
3. Aguanta un apagón sin perder trabajo y sin repetirlo.
4. Un cambio chico cuesta poco.
5. Cuando algo no se puede, se detiene y dice por qué. No se queda en bucle.
6. La PC, la Mac y la Mini corren la misma versión.

## Por qué hace falta un modelo escrito

El centro nació el 26 de octubre con 495 líneas y 18 tipos de evento. Hoy
`ciclo.py` tiene 11,810 líneas y `dominio.py` 5,121. Nunca hubo una lista
cerrada de eventos, de estados ni de esperas, así que cada caso nuevo entró como
una rama más.

| Hecho medido | Valor |
|---|---|
| Tipos de evento que maneja el reductor | 80, en una función de 977 líneas |
| Campos de `Encargo` | 109 (eran 32 el primer día) |
| Funciones de `ciclo.py` que escriben más de un evento sin transacción común | 56, con 467 sitios de escritura |
| Llamadas a `registro.anotar` en `ciclo.py` que revisan su resultado | 7 de 148 |
| Manejadores que comprueban fase e intento del evento | 12 de 80 |
| Esperas sin tope | checks en marcha, PR no consultable, comprobador, autoridad, Kimi, árbol incierto |
| Estados del PR sin tratar | cerrado sin merge, y mergeado a mano con nivel `pr` |
| Ramas `encargo/*` en GitHub que ya son de un PR mergeado o cerrado | 100 de 112 |
| Peso de `espacio/` en la Mac, todo de encargos cerrados | 819 MB |

Dos ejemplos del registro de la Mac muestran el costo. Un encargo figura como
`terminado` con 5 de sus 11 fases sin trabajar. Cuatro PR de nivel `pr` se
mergearon o cerraron en GitHub y el registro todavía cree que esperan revisión.

## Las reglas del centro

Cada regla nombra el fallo que impide y la comprobación que la sostiene.

### Un hecho tiene un solo dueño

Cada hecho vive en un campo y lo escribe un solo tipo de evento. El texto que se
muestra nunca decide el flujo. El caso que lo motivó: la orden de cancelar vivía
en el texto `motivo`, otros eventos lo pisaban, y un encargo cancelado se podía
reanudar.

Lo que se deriva no se guarda. `Encargo` conserva solo los hechos primarios. Los
contadores, las copias y los textos para mostrar se calculan al plegar.

### Un paso decide primero y escribe después, todo junto

Un paso del barrido tiene tres partes.

1. Observar. Toma una foto del encargo plegado, de GitHub y de git.
2. Decidir. Una función pura recibe la foto y devuelve la lista de eventos y,
   como máximo, un efecto externo.
3. Escribir y ejecutar. El registro valida la lista completa y la guarda en una
   sola transacción. Después corre el efecto y guarda su recibo.

`registro.anotar` falla en voz alta cuando el registro rechaza un evento. Hoy lo
devuelve como texto y casi nadie lo lee. El caso que lo motivó: un resultado
rechazado ya había dejado escrita una revisión aprobada.

### Plegar un registro guardado nunca falla

La validación corre solo al escribir. Aplicar un evento ya guardado es total y
no lanza. Hoy un cambio de código puede dejar ilegible un registro viejo, y eso
quedó reproducido.

El mismo registro da el mismo estado en cualquier máquina. El reductor no lee el
nombre del equipo ni el reloj.

### Todo evento dice de qué fase y de qué intento habla

Un solo constructor arma el evento con su encargo, su fase, su intento y, cuando
habla de código, su SHA. El mismo constructor arma la clave que evita repetirlo.
El reductor recibe la fase ya resuelta. Hoy 37 arreglos del historial fueron por
un dato pegado a la fase, la ronda o el SHA equivocados.

### Una lectura tiene tres resultados

Leer GitHub, el CI o a DeepSeek devuelve leído, pendiente o error. Un error nunca
cuenta como verde ni como ausencia. Hoy hay cinco caminos distintos para leer a
DeepSeek, cada uno con su propia idea del error.

### Las decisiones salen de datos, no de prosa

El motor no decide leyendo texto libre. Eso retira el analizador de comandos de
shell hecho a mano, las listas de palabras para restricciones y la lectura de la
salida de las recetas. Lo que hoy se adivina del texto se pide como dato: los
permisos y prohibiciones del encargo, el bloque estructurado de DeepSeek y el
código de salida de la receta.

### Toda espera tiene salida

Cada clase de espera tiene una fila en una sola tabla: qué la abre, qué la
cierra, qué pasa si falla la lectura que la cerraría, su plazo, su tope y qué
ocurre al llegar al tope. No existe una espera sin tope. Al llegar al tope el
encargo se detiene con la causa escrita y avisa.

Una espera es un hecho con plazo. Volver a mirar no escribe otro evento. Hoy
cada vuelta de espera escribe dos, y el registro de la Mac tiene 851 de esos.

### El tamaño tiene límite

El CI falla si un archivo de `motor/` o una función superan el límite, con una
línea base que solo puede bajar. `ciclo.py` y `dominio.py` solo pueden
encogerse.

## El modelo

### Estados del encargo

Los ocho nombres de hoy se conservan, porque los directores ya los leen. Cambia
lo que cada estado lleva consigo: un dato tipado en lugar de banderas y texto.

| Estado | Dato que lleva | Sale por |
|---|---|---|
| `recibido` | nada | empieza un turno, o llega una orden |
| `ejecutando` | fase e intento abiertos | termina el intento, o se abre una espera |
| `esperando_reintento` | clase de espera, plazo y tope | el hecho que la cierra, o el tope |
| `conciliando` | el efecto externo incierto | el recibo del efecto |
| `deteniendo` | la orden: parar o cancelar | los procesos ya no existen |
| `detenido` | la causa: orden del dueño, tope de una espera o falta de avance | `continuar` |
| `cancelado` | nada | la limpieza |
| `terminado` | el resultado: completo, o parcial con las fases que faltan | la limpieza |

Se agrega un hecho final, `limpio`. Un encargo `terminado` o `cancelado` queda
`limpio` cuando el motor comprobó que ya no existe nada de lo que creó.

### Qué significa cada palabra del reporte

El motor usa estas palabras y ninguna otra para decir cómo quedó un encargo.

| El motor dice | Solo cuando |
|---|---|
| entregado | Todas las fases llegaron al nivel pedido, comprobado contra GitHub y contra el recibo del despliegue, y el encargo está `limpio` |
| listo para tu merge | El nivel pedido es `pr`. El PR está abierto, listo, revisado y sin bloqueantes. Falta el merge de David |
| entrega parcial | Alguna fase quedó bloqueada o sin trabajar. El reporte nombra cada fase y su causa |
| detenido | Se paró por una orden o por un tope. El reporte dice la causa y qué lo reanuda |
| cancelado | Lo ordenó David. El PR quedó cerrado y la rama borrada |
| falta limpiar | El trabajo terminó y la limpieza no. El reporte nombra lo que queda |

### Estados del PR y qué hace el motor en cada uno

El registro guarda el estado observado del PR. Ninguna celda queda sin tratar.

| Estado del PR | Nivel `pr` | Nivel `integrado` o `desplegado` |
|---|---|---|
| Sin PR | Abre el PR | Abre el PR |
| Borrador | Lo marca listo | Lo marca listo |
| Abierto | Lo revisa y reporta "listo para tu merge". Lo sigue observando | Lo revisa y lo mergea |
| Mergeado en la base pedida | Anota que lo integró David y limpia | Anota integrado y despliega si se pidió |
| Mergeado en otra base | Se detiene y dice cuál base | Se detiene y dice cuál base |
| Cerrado sin merge | La fase queda cancelada por David. Borra la rama y lo reporta | Igual |

### Limpieza: qué crea el motor y cuándo lo borra

La limpieza es parte de la entrega. Cada recurso tiene una regla, y el motor
comprueba que el recurso ya no existe antes de anotar `limpio`.

| Lo que el motor crea | Hoy | Regla propuesta |
|---|---|---|
| Rama remota `encargo/*` | Nunca se borra | Se borra al mergear o cerrar el PR, y al cancelar |
| PR de un encargo cancelado | Queda abierto | Se cierra al cancelar |
| `espacio/<id>`, la copia del repo | Nunca se borra | Se borra 7 días después del estado final |
| `ejecucion/<id>` | Queda el directorio | Se borra al cerrar la ventana de sondeo |
| Archivos de credenciales del turno | Viven hasta que cierra el encargo | Se borran al terminar cada turno |
| `despliegues/<clave>` | Queda completo | Se conserva el recibo y se borra lo demás |
| Referencias a ramas remotas en el clon del servicio | 132 de 163 ya no existen en GitHub | `fetch --prune` en cada actualización |

Si un borrado falla, el motor lo reintenta con tope y reporta "falta limpiar".
El contador de reintentos vive en el registro, no en la memoria del proceso.

### Eventos

- Una tabla liga cada tipo de evento con su manejador. La fila declara si el
  evento es del encargo, de una fase o de un intento, y si corta las series de
  reintentos. Reemplaza la función de 977 líneas.
- Seis tipos tienen manejador y ningún emisor. Se borran, y plegar un registro
  viejo que los traiga los salta y lo anota.
- Seis tipos se escriben y el reductor los ignora. Cada uno pasa a ser un hecho
  con efecto o deja de escribirse.
- La actividad de las herramientas y los cursores de sondeo son observaciones,
  no hechos del encargo. Salen del registro de eventos. Hoy la actividad es el
  65 % del registro.
- `ciclo.py` deja de leer el registro crudo como estado. Hoy lo hace en 14
  sitios. Todo lo que decide sale del plegado.

## Comprobaciones permanentes

Estas cuatro comprobaciones dicen si el modelo se sostiene. Corren en CI o antes
de activar una versión.

1. Plegado de registros reales. Antes de activar una versión en una máquina, esa
   versión pliega el registro completo de esa máquina. Si un encargo deja de
   plegarse, la versión no se activa.
2. Invariantes sobre secuencias generadas de eventos. Aplicar nunca lanza. Una
   cancelación es definitiva. Un resultado completo implica todas las fases
   preparadas. El mismo registro da el mismo estado.
3. Pruebas de tabla para la decisión de entrega, una fila por cada celda de la
   tabla de estados del PR.
4. Un encargo de punta a punta en cada máquina, con `ops/canario`. El resultado
   se compara con la lista de la primera sección.

Una prueba por combinación reportada deja de ser la norma.

## Orden de trabajo

Cada paso es un PR que no cambia el comportamiento observable, salvo donde este
documento lo dice.

1. Red de seguridad. El plegado de registros reales, las invariantes y los
   límites de tamaño en CI.
2. Escritura. `Registro.append_many`, `anotar` estricto, y los dos pasos con más
   estados a medias: recibir un resultado y cerrar con el juicio.
3. Reductor por tabla, validación separada y estados con dato tipado.
4. Entrega. Observar, decidir y ejecutar, con el estado del PR, las palabras del
   reporte y la limpieza.
5. Tabla de esperas.
6. Borrado. El analizador de shell, las heurísticas de prosa, los tipos de
   evento muertos y el modo planes, que está congelado desde el 30 de octubre.
7. Flota. Los tres caminos que dejan una máquina en otra versión.

## Qué queda fuera

- El puerto de GitHub, la capa de plataforma, los adaptadores de agentes y los
  catálogos de modelos.
- El formato del registro. Sigue siendo SQLite que solo agrega.
- Los nombres de los estados que leen los directores.
- Las reglas de David que ya están en `motor/encargos/politica.py`.

## Decisiones que necesita David

Cada una trae la recomendación y lo que cambia si David decide otra cosa.

1. **Palabras del reporte.** Recomendación: la tabla de arriba. "Entregado" solo
   con todo mergeado, desplegado y limpio. Con nivel `pr` el motor dice "listo
   para tu merge".
2. **Limpieza.** Recomendación: la tabla de arriba, con 7 días para la copia del
   repo, y activar en GitHub el borrado de la rama al mergear como segunda red.
   Un plazo más corto libera disco antes y obliga a volver a clonar si llega una
   corrección tardía.
3. **PR cerrado a mano.** Recomendación: significa que David cancela esa fase.
   El motor no la reabre, borra la rama y lo reporta. La alternativa es tomarlo
   como "rehazla" y abrir un PR nuevo.
4. **PR mergeado a mano con nivel `pr`.** Recomendación: cuenta como integrado
   por David, y el motor limpia.
5. **Tope de las esperas técnicas.** Recomendación: 60 minutos por omisión para
   las que hoy no tienen tope. Al llegar, el encargo se detiene y avisa. Las
   reglas que ya tienen su valor, como los 30 minutos de DeepSeek, no cambian.
6. **Durante la reconstrucción.** Recomendación: no entran funciones nuevas a
   `ciclo.py` ni a `dominio.py`, y los hallazgos Medium de DeepSeek sobre esos
   dos archivos van a una lista en vez de a otro parche. Sin esto la
   reconstrucción persigue un código que cambia 14 veces al día.
7. **Quién reconstruye.** Recomendación: sesiones directas con revisión del
   flujo completo en cada paso. El motor no se reconstruye a sí mismo.

## Cuándo se da por bueno

La reconstrucción termina cuando encargos reales cumplen los seis puntos de la
primera sección en las tres máquinas. No termina cuando un revisor deja de
encontrar hallazgos.
