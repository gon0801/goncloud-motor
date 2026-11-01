---
name: verificar-inspeccion
description: Verifica con TLA+ que un encargo nunca se queda parado sin aviso cuando el revisor pide evidencia por el buzón y el propietario para, continúa, corrige o responde; que una pregunta de autorización del propietario siempre se ve y su respuesta la mueve; que el freno de vueltas sin concluir deja una espera visible con salida; y que una herramienta asíncrona (ci.correr) sobrevive al turno y siempre entrega su resultado. Úsala antes de dar por bueno un cambio en motor/encargos que toque inspecciones, solicitudes, preguntas, herramientas del buzón, parar/continuar, ajustes, director responder o el freno TOPE_NO_CONCLUYENTES.
---

# Verificar el flujo de inspección

El modelo `verificacion/inspeccion/FlujoInspeccion.tla` recorre todos los órdenes posibles de estos pasos, dentro de la cota:

- el revisor termina su turno, pide evidencia o aprueba;
- el motor lee el juicio, liga y registra el pedido, atiende el buzón, lanza al revisor o registra un impedimento;
- el propietario para, el motor cierra al revisor y queda detenido;
- el propietario continúa, agrega un ajuste o responde;
- una herramienta asíncrona (`ci.correr`) se lanza, el turno del agente termina o lo matan, el intento cambia, y después la herramienta termina, o la detiene parar;
- un recurso con `autorizacion: "propietario"` hace que el pedido nazca como pregunta: el encargo pasa a `necesita_usuario`, el propietario contesta sí o no, y el sí ejecuta la herramienta y el no la deja `denegada`;
- el revisor repite `no_concluyente` sobre el mismo pedido: tras `TOPE_NO_CONCLUYENTES` vueltas seguidas el encargo pasa a `necesita_usuario`.

Para no multiplicar los estados, cada corrida explora uno de tres casos (la variable `caso`, que no cambia durante el encargo): `ci` (la herramienta asíncrona, sin freno ni autorización), `autoriza` (el recurso pide autorización) y `freno` (el revisor repite el pedido). Los dos últimos van sin `ci.correr`.

Comprueba nueve reglas:

| Regla | En una frase | El daño si se rompe |
|---|---|---|
| `SinAtascoMudo` | Un encargo abierto sin una espera visible siempre tiene algo que el motor hace solo (salvo una pregunta abierta, que tiene su regla). | El encargo queda en `recibido` para siempre y nadie se entera (traba 9, enc_f2ef6da03122b404, 31-oct). |
| `SinCarrera` | El revisor no vuelve a correr, sin un ajuste nuevo, mientras la inspección que pidió sigue sin despachar en el buzón. | Un turno de Opus gastado sin la evidencia que pidió, y la evidencia llega cuando ya nadie la espera. |
| `ImpedimentoConSalida` | Un impedimento `inspeccion:<id>` siempre lo puede levantar el propietario con un ajuste o una respuesta. | Callejón sin salida: el revisor no corre y solo cancelar lo saca (traba 7, enc_2958028bc8cec162, 30-oct). |
| `ResultadoEntregado` | Una herramienta que terminó siempre entrega su resultado a la fase. | La solicitud sigue `pendiente` con la corrida ya terminada y el revisor no corre: 18 minutos de CI perdidos (ci_base_1, enc_1b8916e8a0bac627, 1-nov). |
| `CorridaSobreviveAlTurno` | Una corrida de CI en marcha no muere porque muera el turno del agente; solo la detienen parar o cancelar. | Se mata la corrida que no dependía del turno y el agente la repite (ci_base_2, 1-nov). |
| `PreguntaVisible` | Una pregunta abierta siempre se ve: el encargo está en `necesita_usuario` con la pregunta, o hay un revisor, una herramienta o un paso del motor, o la quietud (`sin_nada_en_curso`) lo saca a `necesita_usuario`. | El encargo queda en `recibido` con la pregunta abierta y nadie la contesta, aunque se le dio un ajuste (traba 5, 31-oct). |
| `RespuestaMueve` | Contestar una pregunta siempre la mueve: se puede responder y la respuesta la saca de `pregunta`; una respuesta fuera del buzón vuelve a él; con la petición en el buzón y el encargo en marcha, un sí queda `resuelta` y un no `denegada`. | Se contesta y no pasa nada, o el sí se pierde y hay que contestarlo aparte (traba 5). |
| `FrenoVisible` y `AlTopeEspera` | Al llegar al tope, la vuelta que lo alcanza deja el encargo en `necesita_usuario` y, mientras la cuenta esté en el tope, no queda una inspección pendiente esperando evidencia. | El revisor repite el mismo pedido sin que nada lo cuente y gasta turnos sin pedir ayuda: cinco vueltas sobre la misma versión (traba 4, 30 y 31-oct). |
| `EsperaConSalida` | Todo encargo en `necesita_usuario` tiene salida por un ajuste o una respuesta del propietario. | La espera visible no lleva a ninguna parte y solo cancelar lo saca. |

## Cuándo correrla

Córrela si el cambio toca alguna de las funciones de la tabla de cobertura. Para verlo, compara el commit de sincronía con el árbol de trabajo (incluye lo que no tiene commit), con la función entera alrededor de cada cambio:

```sh
git --no-pager diff --no-ext-diff -W f44fa1e -- motor/encargos/dominio.py motor/encargos/ciclo.py motor/encargos/necesidades.py motor/encargos/cli.py
```

Con `-W` cada trozo trae la función entera: la función que contiene el cambio es la primera línea `def` del contexto. El encabezado `@@` nombra la función anterior; no lo uses. Si la salida de `git diff` llega resumida, mándala a un archivo y léela ahí. Busca esas funciones en la columna "Código" de la tabla de cobertura. `f44fa1e` es el commit de sincronía; ver abajo.

## Cómo correrla

`main.cfg` describe el código del commit de sincronía, no tu árbol de trabajo. Correrlo tal cual no revisa un cambio: sale verde aunque el cambio rompa una regla. Revisar un cambio son tres pasos:

1. **Tradúcelo al modelo.**
   - Si invierte una rama de la tabla de cobertura, copia `main.cfg` a un `.cfg` nuevo en `verificacion/inspeccion/` e invierte ahí esa constante.
   - Si cambia una acción, copia `FlujoInspeccion.tla` a otro nombre (por ejemplo `FlujoInspeccionCambio.tla`), cambia su primera línea a `---- MODULE FlujoInspeccionCambio ----` y edita ahí la acción. TLC exige que el nombre del módulo coincida con el del archivo; el script lo comprueba.
   - Si no cambia nada que el modelo distinga (un mensaje, un registro de más), dilo y sigue con el paso 2.
2. **Corre las configuraciones:**

   ```sh
   verificacion/inspeccion/verificar.sh              # main.cfg: el commit de sincronía, tiene que pasar
   verificacion/inspeccion/verificar.sh antes-139.cfg # el código de 72c30a8: tiene que fallar
   verificacion/inspeccion/verificar.sh antes-pregunta-muda.cfg # la pregunta muda de 72c30a8: PreguntaVisible
   verificacion/inspeccion/verificar.sh antes-140.cfg # el código antes del freno (ac41797): FrenoVisible
   verificacion/inspeccion/verificar.sh antes-e0f06a2.cfg # el código de e0f06a2 (CI sin sobrevivir): tiene que fallar
   verificacion/inspeccion/verificar.sh antes-ci-base-1.cfg # solo el resultado perdido: ResultadoEntregado
   verificacion/inspeccion/verificar.sh antes-ci-base-2.cfg # solo la corrida matada: CorridaSobreviveAlTurno
   verificacion/inspeccion/verificar.sh tu-cambio.cfg # tu traducción del paso 1 (solo constantes)
   verificacion/inspeccion/verificar.sh tu-cambio.cfg FlujoInspeccionCambio.tla # si copiaste el modelo
   ```

   Sin el segundo argumento el script corre `FlujoInspeccion.tla`: una copia editada del modelo que no le pases no se comprueba.

   - Las primeras comprueban que el modelo sigue distinguiendo el código roto del sano.
   - Si `main.cfg` falla, o si alguna de las `antes-*.cfg` pasa, el problema está en el modelo, no en tu cambio.
   - Los `hallazgo-*.cfg` son reglas estrictas que main rompe a propósito; ver "Hallazgos conocidos". Esos sí tienen que fallar con main.
3. **Decide.**
   - Si tu configuración rompe una regla, el cambio la rompe: repórtalo con el camino (ver "Si una regla se rompe") y no lo des por bueno.
   - Si pasa y el cambio es intencional, sigue "Si el cambio es intencional".
   - Al terminar, borra las copias que no vayan al commit.

Para la corrida:

- Necesita `mise` (`brew install mise`). La primera vez baja Java Temurin 25 y TLC 1.7.4 con checksum fijo. Sin `mise`, exporta `TLA2TOOLS` (ruta a `tla2tools.jar`) y `JAVA`.
- Tarda unos segundos con `MaxIds = 3` y `Tope = 3`: 249 701 estados distintos, unos 4 s con un worker. Antes de las preguntas y el freno eran 479 052; bajaron a la mitad porque `Detener` ahora exige el buzón atendido (ver `Detener` en la tabla).
- Sale 0 con `Model checking completed. No error has been found.`, 12 si una regla de estado se rompe y 13 si se rompe una propiedad de acción (`AlTopeEspera`, `RevisorEsperaLaRespuesta`).
- TLC se detiene en la primera regla rota. Si quieres saber si hay otra, quítale a una copia la regla que ya conoces. Es opcional y solo sirve para diagnosticar: un cambio que rompe una regla no queda bueno porque la copia sin esa regla pase.

Lee los resultados con la skill `/statechart` si la tienes. Es de pstack (cursor/plugins), no de este repo; en las máquinas de David está instalada como skill de usuario. Pásale el modelo, la configuración y la vista `verificacion/inspeccion/vista.mjs`. Así:

- una regla rota sale como el camino más corto, en palabras;
- una corrida limpia con `MaxIds = 3` ya no cabe en un diagrama (249 701 estados). La vista sirve para los caminos de las reglas rotas.

Sin `/statechart`, la salida de `verificar.sh` ya trae el camino. Cada `State N: <Acción ...>` nombra la acción del modelo, y la tabla de cobertura dice qué función del motor la hace. Debajo vienen las variables de ese estado. Basta para decidir; `/statechart` solo lo pone en palabras.

## Cobertura

Cada constante de `main.cfg` es una rama concreta del código. Cada acción del modelo es una función:

| Modelo | Código | Valor en main |
|---|---|---|
| `RegistraAlLigar` | `ciclo._ligar_inspeccion` llama `necesidades.registrar_pedida` después de `pedir_inspeccion` | TRUE |
| `ReligaSinSolicitud` | `ciclo._ligar_inspeccion`: el "ya ligada, no hago nada" exige `previa is not None` | TRUE |
| `LigaHuerfanaBloquea` | `dominio.inspeccion_bloquea`: rama `sol is None` | FALSE |
| `DetieneLaPendiente` | `necesidades.atender`, rama `if _detenida_ahora(...)`: `_anotar_resolucion(..., "detenida")` de una previa `pendiente` | TRUE |
| `AjusteAbre` | `dominio.inspeccion_bloquea` e `inspeccion_consumida` consultan `ajuste_sin_ver_por_inspeccion` | TRUE |
| `AjusteSuelta` | reducción `kind == "ajuste"` llama `_soltar_impedimento_de_inspeccion` | TRUE |
| `RespuestaReabre` | `dominio._respuesta` a una solicitud que no es `pregunta`: suma `tardias` (marca nueva en `marca_inspeccion_entregada`) y suelta el impedimento | TRUE |
| `RecuerdaCarpeta` | `Solicitud.carpeta` (evento `herramienta`, anotado por `necesidades._recordar_carpeta` antes de lanzar); `necesidades._recoger` y `_carpeta_de` buscan el resultado en esa carpeta y no en el intento vigente | TRUE |
| `SoloMataLoDelTurno` | `ciclo.barrer`, rama `elif decision["matar"]`: `necesidades.cerrar_herramientas(directorio, solo_del_turno=True)`, que salta `ci.correr` | TRUE |
| `AvisaPregunta` | `dominio._retomar` (llamado por la orden `continuar` y por la reducción `ajuste` desde `necesita_usuario`) deja `necesita_usuario` con la pregunta si `pregunta_de_inspeccion` sigue abierta; `dominio.sin_nada_en_curso` no da por sostenido al encargo por esa pregunta. En 72c30a8 `_retomar` dejaba `recibido` y `sin_nada_en_curso` la daba por sostenida (`inspeccion_bloquea`) | TRUE |
| `RespuestaVigente` | `dominio._respuesta` usa `respuesta_vigente` (basta la fase). En 72c30a8 usaba `solicitud_vigente`: la pregunta de un intento anterior ya no recibía la respuesta | TRUE |
| `FrenoCuenta` | `dominio._contar_no_concluyentes` (desde `dominio._revision`) y la guarda `enc.estado == "necesita_usuario" and enc.no_concluyentes >= TOPE_NO_CONCLUYENTES` de `ciclo._cerrar_con_juicio`. Antes de ac41797 (en la rama que se mergeó como #139) la cuenta no existía | TRUE |
| `Tope` | `dominio.TOPE_NO_CONCLUYENTES` | 3 |
| `Lanzar` | `dominio.decision_proceso` (orden: impedimento, `espera_inspeccion_consumida`, `inspeccion_bloquea`); al abrir el intento el ajuste queda visto y las herramientas lanzadas antes quedan en la carpeta del intento anterior; no corre mientras haya un resultado que recoger ni una respuesta del propietario con su petición en el buzón (`ciclo.barrer` llama a `atender` antes de decidir el lanzamiento) | |
| `Salta`, `VistaAlCambiar` | `ciclo._salta_inspeccion` (antes de incorporar): un juicio que vuelve a pedir la consulta ligada no gasta revisión y, si la solicitud ya tiene resultado o una respuesta, anota `inspeccion_entregada`; `ciclo._consumir_ligada` la anota al cambiar de consulta. Un revisor muerto sin juicio no anota nada y se relanza con el mismo dato | |
| `Incorporar`, `LigarPide` | guarda `por_incorporar` en `ciclo.barrer` (no incorpora con el encargo deteniendo, detenido o en `necesita_usuario`); `ciclo._incorporar_revisor`, rama `no_concluyente` con `inspeccion_faltante`: `_devolver(..., "inspeccionar")`; reducción `revision` (pone `inspeccion_pendiente` y cuenta con `_contar_no_concluyentes`: la `Tope`-ésima la vacía y deja `necesita_usuario`, y entonces no se devuelve ni se liga); `ciclo._ligar_inspeccion` con `ciclo._id_inspeccion`, `necesidades.pedir_inspeccion` y `necesidades.registrar_pedida`; si el recurso tiene `autorizacion: "propietario"`, `marcar_pregunta` (bloquea) deja `necesita_usuario`; con la liga ya puesta y la previa en `pregunta`, `marcar_pregunta` otra vez | |
| `Atender` | `necesidades.atender`: previa con resultado (responde y archiva), rama `_detenida_ahora` (con la reducción `solicitud_tardia`, que solo anota en `procedimiento`), `_registrar_solicitud`, y la herramienta síncrona que termina con `_anotar_resolucion` → reducción `resolucion` (`dominio._resolucion`, que llama `_reanudar_inspeccion`). Para `ci.correr`: `_recordar_carpeta` y `_ejecutar` la lanzan (`herr = corre`) y la solicitud sigue `pendiente` con la petición en el buzón. Con autorización: la rama `pregunta` (`capacidades.ejecutar` devuelve `pregunta` sin `autorizado`), el camino `autorizado` (previa en `respuesta` y `_afirmativa`: la herramienta corre y la solicitud queda `resuelta`) y el camino `denegada` (previa en `respuesta` no afirmativa: `_anotar_resolucion(..., "denegada")`) | |
| `Responder` | `director responder` → `necesidades.responder` → `dominio._respuesta`: una `pregunta` pasa a `respuesta` (sí o no) y `continuar_respuestas` repone la petición; en una solicitud que no es `pregunta` suma `tardias`. Además `_soltar_impedimento_de_inspeccion`, `_sin_repeticiones` y `_reanudar_inspeccion` (`necesita_usuario` → `recibido` si la ligada ya no bloquea) | |
| `ReponePeticion` | `cli.paso`: después de `barrer`, para cada encargo con una solicitud en `respuesta`, `necesidades.continuar_respuestas` → `_reponer_peticion` devuelve al buzón la petición que una parada archivó, y `atender` la ejecuta, la deniega o, parado, la archiva otra vez. Corre después del barrido: el revisor puede lanzarse antes | |
| `HerramientaTermina` | `capacidades.main` deja `resultado.json` en la carpeta de la herramienta; no depende del turno ni del estado del encargo | |
| `Recoger` | `necesidades._recoger` (primer paso de `atender`): resuelve con `_cerrar_con_resultado` la solicitud `pendiente` o `detenida` cuya herramienta terminó; un `ok` sobre una `detenida` la pasa a `resuelta`, un resultado que no es `ok` la deja `detenida` (el modelo solo trae el `ok`). El código de e0f06a2 además exigía la petición sin archivar; el modelo no | |
| `PedirCi` | el agente, con su turno vivo, pide `ci.correr` por el buzón (`cliente.py pedir`): la petición queda en `peticiones/` y `necesidades.atender` la registra y la lanza. No es la inspección: no la liga ni bloquea al revisor. Un solo pedido de este tipo (id `PedAg`, fuera de los de la inspección). Solo en el caso `ci` | |
| `MatarTurno` | `ciclo.barrer` con `decision["matar"]` sin parada: `dominio.decision_proceso` solo llega a la rama `colgada` (árbol vivo, sin herramienta ni hecho nuevo) si no hay impedimento, ni `espera_inspeccion_consumida`, ni `inspeccion_bloquea`; luego `cerrar(directorio, proc)` y `necesidades.cerrar_herramientas` (e0f06a2: solo las del `directorio` vigente, y la corrida de CI moría; su solicitud acababa `error` y el modelo la deja `detenida`, sin resultado). El intento cambia cuando el motor lanza el turno siguiente (`Lanzar`), no aquí | |
| `CerrarHerramientas` | `ciclo.barrer` con `parada` o `necesidades.detenida_ahora`: `necesidades.cerrar_herramientas_del_encargo`, todos los intentos, con la limpieza de rama y PR de la de CI | |
| `Impedir` | `dominio.decision_proceso` → evento `impedimento` de `espera_inspeccion_consumida` | |
| `Quietud`, `SinNadaEnCurso` | `ciclo._vigilar_quietud` con `dominio.sin_nada_en_curso`: un encargo abierto, sin proceso ni impedimento, que no se sostiene por una inspección que bloquea, una solicitud abierta ni una herramienta, pasa a `necesita_usuario` tras `_QUIETUD_VUELTAS` vueltas y `_QUIETUD_S` segundos. El modelo no tiene reloj: la acción está habilitada cuando el encargo está quieto | |
| `Parar`, `Continuar` | `dominio._orden` (`parar` vale desde cualquier estado no terminal, también `necesita_usuario`; `continuar` → `_retomar`, que no toca la cuenta del freno) | |
| `Matar` | `ciclo.barrer` con la parada: mata el árbol vivo | |
| `Detener` | `ciclo.barrer`: árbol ausente → `detenido`. Exige el buzón vacío porque en el mismo barrido `atender` corre antes, también con la parada (archiva o detiene cada petición) | |
| `Ajustar` | reducción `ajuste`: `_soltar_impedimento_de_inspeccion`, `_sin_repeticiones` (la cuenta del freno empieza de nuevo) y, desde `necesita_usuario`, `_retomar` | |

**Commit de sincronía: `f44fa1e`.** Ese es el commit en el que se comprobó por última vez que el modelo describe este código (`dominio._respuesta`, `_reanudar_inspeccion`, `_retomar`, `pregunta_de_inspeccion`, `sin_nada_en_curso`, `_contar_no_concluyentes`, `ciclo._ligar_inspeccion`, `_incorporar_revisor`, `necesidades.atender`, `continuar_respuestas` y `cli.paso`). Muévelo solo después de releer las funciones de la tabla en el commit nuevo y confirmar que el modelo sigue diciendo lo mismo.

Qué cuenta en el modelo:

- **"Espera visible"**: un impedimento registrado (`imped`), o un estado que no es `recibido` ni `ejecutando` (`necesita_usuario`, `deteniendo`, `detenido`, `terminado`). Lo que solo se ve si alguien consulta el estado (por ejemplo, la `necesidad_actual` de `seguimiento_de`) no cuenta: nadie recibe un aviso. `Abierto` deja fuera esos estados a propósito.
- **"Información nueva"** para el revisor, según `dominio.inspeccion_bloquea`: un ajuste, una respuesta del propietario o un resultado nuevo de la solicitud ligada. La variable `sinInfo` cuenta las revisiones consumidas desde la última. El contador del freno (`cuenta`) es el del código y solo lo reinician un ajuste o una respuesta.
- **`Tope`**: el modelo usa el valor del código (3). Si `dominio.TOPE_NO_CONCLUYENTES` cambia y el `.cfg` no, `verificar.sh` sale con 2 y `tests/test_tope_del_modelo.py` falla, y esa prueba corre en cada PR dentro del job `bateria` de `.github/workflows/pruebas.yml` (`python -m unittest discover -s tests -t .`), no en un job propio; cambia `Tope` en todos los `.cfg` y revisa el modelo. También se comprobó `main.cfg` con `MaxIds = 4`: 1 413 700 estados, unos 22 s.
- **Largo de un camino**: los estados que imprime TLC (`State 1` … `State N`). Hay N - 1 acciones.
- **Dos caminos con las mismas acciones** pueden ser fallos distintos: compara las variables del último estado. Por ejemplo, la traba 9 de `antes-139.cfg` termina con la solicitud ligada `ausente`; invertir `DetieneLaPendiente` termina con la misma secuencia, pero con la solicitud `pendiente` y sin petición en el buzón.

## Si una regla se rompe

1. Lee el camino, con `/statechart` o directo de la salida de TLC.
2. Repite cada paso contra el código, usando la tabla de cobertura para saber qué función hace cada paso.
3. Busca también, fuera de la tabla, si algún otro camino rescataría al encargo en el último estado. `PreguntaVisible` ya cuenta la quietud (`sin_nada_en_curso`) como rescate de una pregunta abierta; el detector no rescata una inspección que bloquea sin pregunta, que `sin_nada_en_curso` da por sostenida.
4. Si el código puede dar ese paso así y nada lo rescata, es un fallo real. Repórtalo con el camino en palabras del motor (por ejemplo: "el revisor pide evidencia, lo paras, el motor saca el pedido del buzón, continúas y queda atado a un pedido que no existe"). No cambies código del producto sin que lo pidan.
5. Si el código no puede dar ese paso, el modelo está mal. Corrige la acción o la constante y anota por qué en el comentario de la acción.
6. Nunca debilites una regla ni la quites del `.cfg` para que pase.

## Hallazgos conocidos

Tres reglas estrictas se rompen con el código de main. No se debilitaron ni están en `main.cfg`: cada una vive en su `.cfg` y tiene que fallar con main. Si dejan de fallar, alguien arregló el código y esa regla puede pasar a `main.cfg`.

| Configuración | Regla estricta | Camino en palabras del motor |
|---|---|---|
| `hallazgo-pregunta-callada.cfg` (8 estados) | `PreguntaNuncaCallada`: una pregunta abierta nunca deja al encargo en `recibido` o `ejecutando` con nada en marcha, ni un solo momento. | El revisor pide una inspección sobre un recurso que exige autorización; el encargo queda en `necesita_usuario`. Le das un ajuste, que abre el turno una vez. El revisor corre y repite el mismo pedido: `_salta_inspeccion` no lo consume y nada vuelve a marcar la pregunta. El encargo queda en `ejecutando` con la pregunta abierta hasta que la quietud lo pase a `necesita_usuario`. |
| `hallazgo-freno-continuar.cfg` (35 estados) | `FrenoRevisor`: el revisor no corre más de `Tope` veces seguidas sin información nueva. | El encargo llega al tope y queda en `necesita_usuario`. Lo paras y lo continúas: `_retomar` no toca `no_concluyentes` y el revisor corre otra vez sin ajuste, respuesta ni resultado. El freno vuelve a saltar en esa vuelta, pero cada parar y continuar cuesta un turno del revisor. |
| `hallazgo-respuesta-sin-ejecutar.cfg` (10 estados) | `RevisorEsperaLaRespuesta`: el revisor no se lanza con un sí o un no del propietario todavía sin ejecutar. | Paras con la pregunta abierta, respondes mientras está deteniendo: `atender` archiva la petición sin ejecutarla. Continúas y `cli.paso` llama a `barrer`, que lanza al revisor porque una solicitud en `respuesta` no bloquea, y solo después a `continuar_respuestas`, que repone la petición y ejecuta o deniega. El revisor corre un turno sin el resultado. |

## Si el cambio es intencional

- **El cambio invierte una rama de la tabla:** ajusta la constante en `main.cfg` y corre `main.cfg`.
- **El cambio agrega un comportamiento:** agrega o cambia la acción, con un comentario que nombre la función, y agrega su fila a la tabla.
- **Después, en los dos casos:**
  1. Corre `main.cfg` y las `antes-*.cfg`, y los `hallazgo-*.cfg` (tienen que seguir fallando, salvo que el cambio arregle ese hallazgo).
  2. Comprueba que la regla que tocaste todavía puede fallar: invierte en una copia de `main.cfg` la constante que la sostiene. La tabla de abajo dice qué se espera.
  3. Mueve el commit de sincronía.

Qué pasa al invertir cada constante en `main.cfg`, medido sobre el código de `f44fa1e` (largo en estados de TLC). Si tu cambio hace una de estas inversiones, la tabla ya te dice el resultado; córrelo igual para tener el camino:

| Constante invertida | Resultado |
|---|---|
| `RegistraAlLigar` | `SinCarrera` se rompe (4 estados) |
| `DetieneLaPendiente` | `SinAtascoMudo` se rompe (7 estados) |
| `AjusteSuelta` y `RespuestaReabre` juntas | `ImpedimentoConSalida` se rompe (8 estados) |
| `RecuerdaCarpeta` | `ResultadoEntregado` se rompe (7 estados; en el mismo estado se rompe también `SinAtascoMudo`) |
| `SoloMataLoDelTurno` | `CorridaSobreviveAlTurno` se rompe (5 estados) |
| `RecuerdaCarpeta` y `SoloMataLoDelTurno` juntas (`antes-e0f06a2.cfg`) | `CorridaSobreviveAlTurno` se rompe primero (5 estados) |
| `AvisaPregunta` (con `AjusteAbre` también: `antes-pregunta-muda.cfg`, 72c30a8) | `PreguntaVisible` se rompe: 8 estados sola (pides con autorización, paras y continúas con la pregunta abierta), 5 con `AjusteAbre` invertida (le das un ajuste y nada lo mueve: la traba 5) |
| `RespuestaVigente` | `RespuestaMueve` se rompe (6 estados: tras un ajuste el revisor corre y la respuesta a la pregunta del intento anterior no cuenta) |
| `FrenoCuenta` (`antes-140.cfg`, antes de ac41797) | `FrenoVisible` se rompe (17 estados: la tercera vuelta seguida deja la inspección pendiente sin pedir ayuda) |
| `ReponePeticion` fuera de `Motor` (copia del modelo) | `RespuestaMueve` se rompe (7 estados: el sí dado durante la parada se pierde) |
| `ReligaSinSolicitud`, `LigaHuerfanaBloquea`, `AjusteAbre`, o `AjusteSuelta` o `RespuestaReabre` sola | pasa |

Por qué pasan esas últimas:

- `ReligaSinSolicitud` y `LigaHuerfanaBloquea` solo importan para una liga sin solicitud. Con `RegistraAlLigar` esa liga ya no nace; quedan como defensa para encargos creados por código viejo.
- `AjusteSuelta` y `RespuestaReabre` son dos salidas, y basta una.
- `AjusteAbre` acelera la vuelta del revisor, pero ninguna de las reglas la exige; con `AvisaPregunta` también invertida sí rompe `PreguntaVisible`.
- `AvisaPregunta` tiene dos mecanismos redundantes, `_retomar` y `sin_nada_en_curso`; la constante invierte los dos a la vez, como estaban en 72c30a8.

## Lo que no cubre

- Una sola fase, solo el turno del revisor (el implementador no aparece) y un solo tipo de pedido, repetible. Cancelar, varias fases y varios encargos quedan fuera a propósito.
- Los tres casos van separados: la autorización y el freno se exploran sin `ci.correr`, y el freno sin autorización. Una herramienta asíncrona que pide autorización, o un freno que corre junto a una corrida de CI, no se exploran. El motivo es la cota (juntos eran unos 2 millones de estados y 30 s).
- Autorización: el recurso nunca está sin conceder. No se modela el pedido cuyo recurso no cubre la operación (la solicitud nace `pregunta` con "Ninguna capacidad concedida del puente la cubre"). La respuesta es un sí o un no; no hay respuestas ambiguas ni una autorización previa (`_autorizado` por recurso, operación, fase e intento) que evite la pregunta.
- Freno: solo se modela el mismo pedido repetido (misma versión y mismo texto), con la identidad que avanza por el reintento de una `detenida`. No se modelan otra versión, otro pedido ni un avance de bloqueantes, que empiezan la cuenta de nuevo, ni un recurso que cambió de huella.
- La quietud no tiene reloj (`_QUIETUD_S`, `_QUIETUD_VUELTAS`): se modela como un paso que está habilitado, no como el tiempo que tarda.
- De la herramienta asíncrona: una sola operación (`ci.correr`) por solicitud y solo el resultado `ok`. No hay resultado de error: una corrida muerta por el turno o por parar queda `detenida` sin resultado (en el código acaba `error`), y un resultado que no es `ok` tras la detención no se modela (el código lo deja `detenida`).
- El turno del responsable aparte del revisor: el modelo tiene un solo turno de agente (`proc`), el del primer turno y el del revisor. ci_base_1 y ci_base_2 fueron del responsable; el modelo reproduce el mismo mecanismo (una corrida viva, un intento que cambia, un turno matado) con ese turno único: ci_base_2 con `PedirCi` y `MatarTurno`, ci_base_1 con el revisor que un ajuste reabre mientras la corrida sigue. Tras `continuar`, el modelo supone que el siguiente turno es el del revisor; en el código `_retomar_trabajo` puede dar el turno al responsable.
- El reuso de una corrida viva (`necesidades._corrida_igual`, mismo recurso, job, versión del producto y configuración): lo cubre `tests/test_ci_asincrono.py`, no el modelo.
- La petición sin archivar: el código de e0f06a2 solo encontraba el resultado si la petición seguía en el buzón; el modelo no lo exige, así que es más favorable a ese código y aun así rompe `ResultadoEntregado`.
- La solicitud que `_vigente` da por tardía (otro intento que no es el de la inspección ligada) y su respuesta `tardia`: el modelo no la distingue, salvo para la respuesta a una pregunta (`RespuestaVigente`).
- Que parar cierre solo las herramientas del intento vigente (e0f06a2 solo cerraba `directorio`); el modelo cierra las de todos los intentos.
- Ningún job de CI corre TLC; hay que correrlo a mano según "Cuándo correrla". La prueba `tests/test_tope_del_modelo.py` del job `bateria` solo comprueba que el `Tope` de los `.cfg` sea el del motor.
- La cota: hasta `MaxIds = 3` identidades del mismo pedido (249 701 estados distintos); con `MaxIds = 4` también pasa (1 413 700 estados). No se volvió a medir con 5. Un pase cubre el modelo dentro de la cota, no todo el motor.
