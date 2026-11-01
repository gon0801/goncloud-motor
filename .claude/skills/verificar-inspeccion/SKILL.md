---
name: verificar-inspeccion
description: Verifica con TLA+ que un encargo nunca se queda parado sin aviso cuando el revisor pide evidencia por el buzón y el propietario para, continúa, corrige o responde. Úsala antes de dar por bueno un cambio en motor/encargos que toque inspecciones, solicitudes, el buzón, parar/continuar, ajustes o director responder.
---

# Verificar el flujo de inspección

El modelo `verificacion/inspeccion/FlujoInspeccion.tla` recorre todos los órdenes posibles de estos pasos, dentro de la cota:

- el revisor termina su turno, pide evidencia o aprueba;
- el motor lee el juicio, liga y registra el pedido, atiende el buzón, lanza al revisor o registra un impedimento;
- el propietario para, el motor cierra al revisor y queda detenido;
- el propietario continúa, agrega un ajuste o responde.

Comprueba tres reglas:

| Regla | En una frase | El daño si se rompe |
|---|---|---|
| `SinAtascoMudo` | Un encargo abierto sin una espera visible siempre tiene algo que el motor hace solo. | El encargo queda en `recibido` para siempre y nadie se entera (traba 9, enc_f2ef6da03122b404, 31-oct). |
| `SinCarrera` | El revisor no vuelve a correr, sin un ajuste nuevo, mientras la inspección que pidió sigue sin despachar en el buzón. | Un turno de Opus gastado sin la evidencia que pidió, y la evidencia llega cuando ya nadie la espera. |
| `ImpedimentoConSalida` | Un impedimento `inspeccion:<id>` siempre lo puede levantar el propietario con un ajuste o una respuesta. | Callejón sin salida: el revisor no corre y solo cancelar lo saca (traba 7, enc_2958028bc8cec162, 30-oct). |

## Cuándo correrla

Córrela si el cambio toca alguna de las funciones de la tabla de cobertura. Para verlo, compara el commit de sincronía con el árbol de trabajo (incluye lo que no tiene commit), con la función entera alrededor de cada cambio:

```sh
git --no-pager diff --no-ext-diff -W e0f06a2 -- motor/encargos/dominio.py motor/encargos/ciclo.py motor/encargos/necesidades.py
```

Con `-W` cada trozo trae la función entera: la función que contiene el cambio es la primera línea `def` del contexto. El encabezado `@@` nombra la función anterior; no lo uses. Si la salida de `git diff` llega resumida, mándala a un archivo y léela ahí. Busca esas funciones en la columna "Código" de la tabla de cobertura. `e0f06a2` es el commit de sincronía; ver abajo.

## Cómo correrla

`main.cfg` describe el código del commit de sincronía, no tu árbol de trabajo. Correrlo tal cual no revisa un cambio: sale verde aunque el cambio rompa una regla. Revisar un cambio son tres pasos:

1. **Tradúcelo al modelo.**
   - Si invierte una rama de la tabla de cobertura, copia `main.cfg` a un `.cfg` nuevo en `verificacion/inspeccion/` e invierte ahí esa constante.
   - Si cambia una acción, edita la acción en una copia del `.tla`.
   - Si no cambia nada que el modelo distinga (un mensaje, un registro de más), dilo y sigue con el paso 2.
2. **Corre las tres configuraciones:**

   ```sh
   verificacion/inspeccion/verificar.sh              # main.cfg: el commit de sincronía, tiene que pasar
   verificacion/inspeccion/verificar.sh antes-139.cfg # el código de 72c30a8: tiene que fallar
   verificacion/inspeccion/verificar.sh tu-cambio.cfg # tu traducción del paso 1
   ```

   - Las dos primeras comprueban que el modelo sigue distinguiendo el código roto del sano.
   - Si `main.cfg` falla, o si `antes-139.cfg` pasa, el problema está en el modelo, no en tu cambio.
3. **Decide.**
   - Si tu configuración rompe una regla, el cambio la rompe: repórtalo con el camino (ver "Si una regla se rompe") y no lo des por bueno.
   - Si pasa y el cambio es intencional, sigue "Si el cambio es intencional".
   - Al terminar, borra las copias que no vayan al commit.

Para la corrida:

- Necesita `mise` (`brew install mise`). La primera vez baja Java Temurin 25 y TLC 1.7.4 con checksum fijo. Sin `mise`, exporta `TLA2TOOLS` (ruta a `tla2tools.jar`) y `JAVA`.
- Tarda unos segundos.
- Sale 0 con `Model checking completed. No error has been found.` y sale 12 si una regla se rompe.
- TLC se detiene en la primera regla rota. Si quieres saber si hay otra, quítale a una copia la regla que ya conoces. Es opcional y solo sirve para diagnosticar: un cambio que rompe una regla no queda bueno porque la copia sin esa regla pase.

Lee todo resultado con la skill `/statechart`. Pásale el modelo, la configuración y la vista `verificacion/inspeccion/vista.mjs`. Así:

- una regla rota sale como el camino más corto, en palabras;
- una corrida limpia sale como el diagrama de cómo se mueve el encargo.

Con `MaxIds = 3` el grafo pasa de 500 estados y `/statechart` solo dibuja los caminos de las reglas rotas. Para ver el diagrama completo, usa una copia de la configuración con `MaxIds = 2`.

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
| `Lanzar` | `dominio.decision_proceso` (orden: impedimento, `espera_inspeccion_consumida`, `inspeccion_bloquea`); `ciclo` anota `inspeccion_entregada` | |
| `Incorporar`, `LigarPide` | guarda `por_incorporar` en `ciclo.barrer` (no incorpora con el encargo deteniendo o detenido); `ciclo._incorporar_revisor`, rama `no_concluyente` con `inspeccion_faltante`: `_devolver(..., "inspeccionar")` deja el encargo `recibido`; reducción `revision` (pone `inspeccion_pendiente`); `ciclo._ligar_inspeccion` con `ciclo._id_inspeccion`, `necesidades.pedir_inspeccion` y `necesidades.registrar_pedida` | |
| `Atender` | `necesidades.atender`: previa con resultado (responde y archiva), rama `_detenida_ahora` (con la reducción `solicitud_tardia`, que solo anota en `procedimiento`), `_registrar_solicitud`, y la herramienta que termina con `_anotar_resolucion` → reducción `resolucion` (`dominio._resolucion`) | |
| `Impedir` | `dominio.decision_proceso` → evento `impedimento` de `espera_inspeccion_consumida` | |
| `Parar`, `Continuar` | `dominio._orden` (`parar`, `continuar` → `_retomar`) | |
| `Matar`, `Detener` | `ciclo.barrer` con la parada: mata el árbol vivo; árbol ausente → `detenido` | |
| `Ajustar`, `Responder` | reducción `ajuste`; `dominio._respuesta` vía `director responder` | |

**Commit de sincronía: `e0f06a2`.** Ese es el commit en el que se comprobó por última vez que el modelo describe este código. Muévelo solo después de releer las funciones de la tabla en el commit nuevo y confirmar que el modelo sigue diciendo lo mismo.

Qué cuenta en el modelo:

- **"Espera visible"**: un impedimento registrado (`imped`), o un estado que no es `recibido` ni `ejecutando` (`deteniendo`, `detenido`, `terminado`). Lo que solo se ve si alguien consulta el estado (por ejemplo, la `necesidad_actual` de `seguimiento_de`) no cuenta: nadie recibe un aviso. `Abierto` deja fuera esos estados a propósito. El camino de `necesita_usuario` (preguntas al propietario) no está en el modelo; ver "Lo que no cubre".
- **Largo de un camino**: los estados que imprime TLC (`State 1` … `State N`). Hay N - 1 acciones.
- **Dos caminos con las mismas acciones** pueden ser fallos distintos: compara las variables del último estado. Por ejemplo, la traba 9 de `antes-139.cfg` termina con la solicitud ligada `ausente`; invertir `DetieneLaPendiente` termina con la misma secuencia, pero con la solicitud `pendiente` y sin petición en el buzón.

## Si una regla se rompe

1. Lee el camino con `/statechart`.
2. Repite cada paso contra el código, usando la tabla de cobertura para saber qué función hace cada paso.
3. Busca también, fuera de la tabla, si algún otro camino rescataría al encargo en el último estado. El detector de quietud no lo hace: `dominio.sin_nada_en_curso` da por sostenido un encargo con una inspección que bloquea.
4. Si el código puede dar ese paso así y nada lo rescata, es un fallo real. Repórtalo con el camino en palabras del motor (por ejemplo: "el revisor pide evidencia, lo paras, el motor saca el pedido del buzón, continúas y queda atado a un pedido que no existe"). No cambies código del producto sin que lo pidan.
5. Si el código no puede dar ese paso, el modelo está mal. Corrige la acción o la constante y anota por qué en el comentario de la acción.
6. Nunca debilites una regla ni la quites del `.cfg` para que pase.

## Si el cambio es intencional

- **El cambio invierte una rama de la tabla:** ajusta la constante en `main.cfg` y corre `main.cfg`.
- **El cambio agrega un comportamiento:** agrega o cambia la acción, con un comentario que nombre la función, y agrega su fila a la tabla.
- **Después, en los dos casos:**
  1. Corre las dos configuraciones.
  2. Comprueba que la regla que tocaste todavía puede fallar: invierte en una copia de `main.cfg` la constante que la sostiene. La tabla de abajo dice qué se espera.
  3. Mueve el commit de sincronía.

Qué pasa al invertir cada constante en `main.cfg`, medido en `e0f06a2` (largo en estados de TLC). Si tu cambio hace una de estas inversiones, la tabla ya te dice el resultado; córrelo igual para tener el camino:

| Constante invertida | Resultado |
|---|---|
| `RegistraAlLigar` | `SinCarrera` se rompe (4 estados) |
| `DetieneLaPendiente` | `SinAtascoMudo` se rompe (7 estados) |
| `AjusteSuelta` y `RespuestaReabre` juntas | `ImpedimentoConSalida` se rompe (8 estados) |
| `ReligaSinSolicitud`, `LigaHuerfanaBloquea`, `AjusteAbre`, o `AjusteSuelta` o `RespuestaReabre` sola | pasa |

Por qué pasan esas últimas:

- `ReligaSinSolicitud` y `LigaHuerfanaBloquea` solo importan para una liga sin solicitud. Con `RegistraAlLigar` esa liga ya no nace; quedan como defensa para encargos creados por código viejo.
- `AjusteSuelta` y `RespuestaReabre` son dos salidas, y basta una.
- `AjusteAbre` acelera la vuelta del revisor, pero ninguna de las tres reglas la exige.

## Lo que no cubre

- Una sola fase, solo el turno del revisor (el implementador no aparece) y un solo tipo de pedido, repetible.
- Recursos concedidos: el camino de `pregunta` y autorización del propietario no está modelado.
- El freno `TOPE_NO_CONCLUYENTES` (necesita_usuario tras N no concluyentes), cancelar, varias fases y varios encargos.
- Una herramienta asíncrona (`ci.correr`) que termina después de que la solicitud quedó `detenida`.
- El cambio de intento: `_vigente` deja vigente la solicitud ligada, así que no hace falta para estas reglas.
- La cota: hasta `MaxIds = 3` identidades del mismo pedido; también se comprobó con 5. Un pase cubre el modelo dentro de la cota, no todo el motor.
