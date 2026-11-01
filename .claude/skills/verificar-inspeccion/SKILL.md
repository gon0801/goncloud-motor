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

Córrela si el cambio toca alguna de las funciones de la tabla de cobertura. Para verlo, compara el commit de sincronía con el árbol de trabajo (incluye lo que no tiene commit):

```sh
git diff e0f06a2 -- motor/encargos/dominio.py motor/encargos/ciclo.py motor/encargos/necesidades.py
```

Busca en ese diff los nombres de la columna "Código". `e0f06a2` es el commit de sincronía; ver abajo.

## Cómo correrla

```sh
verificacion/inspeccion/verificar.sh              # main.cfg: describe el código actual
verificacion/inspeccion/verificar.sh antes-139.cfg # el código de 72c30a8: tiene que fallar
```

- Necesita `mise` (`brew install mise`). La primera vez baja Java Temurin 25 y TLC 1.7.4 con checksum fijo. Sin `mise`, exporta `TLA2TOOLS` (ruta a `tla2tools.jar`) y `JAVA`.
- Tarda unos segundos.
- Sale 0 con `Model checking completed. No error has been found.` y sale 12 si una regla se rompe.
- Las dos corridas son la prueba completa:
  - `main.cfg` tiene que pasar;
  - `antes-139.cfg` tiene que fallar en `SinAtascoMudo` con la traba 9. Si de pronto pasa, el modelo ya no distingue el código roto del sano: el problema está en el modelo, no en el código.

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
| `Incorporar`, `LigarPide` | guarda `por_incorporar` en `ciclo.barrer`; `ciclo._incorporar_revisor` (rama `no_concluyente` con `inspeccion_faltante`); `ciclo._ligar_inspeccion`; `ciclo._id_inspeccion` | |
| `Atender` | `necesidades.atender` (previa con resultado, `_detenida_ahora`, `_registrar_solicitud`, resolución) | |
| `Impedir` | `dominio.decision_proceso` → evento `impedimento` de `espera_inspeccion_consumida` | |
| `Parar`, `Continuar` | `dominio._orden` (`parar`, `continuar` → `_retomar`) | |
| `Matar`, `Detener` | `ciclo.barrer` con la parada: mata el árbol vivo; árbol ausente → `detenido` | |
| `Ajustar`, `Responder` | reducción `ajuste`; `dominio._respuesta` vía `director responder` | |

**Commit de sincronía: `e0f06a2`.** Ese es el commit en el que se comprobó por última vez que el modelo describe este código. Muévelo solo después de releer las funciones de la tabla en el commit nuevo y confirmar que el modelo sigue diciendo lo mismo.

## Si una regla se rompe

1. Lee el camino con `/statechart`.
2. Repite cada paso contra el código, usando la tabla de cobertura para saber qué función hace cada paso.
3. Si el código puede dar ese paso así, es un fallo real. Repórtalo con el camino en palabras del motor (por ejemplo: "el revisor pide evidencia, lo paras, el motor saca el pedido del buzón, continúas y queda atado a un pedido que no existe"). No cambies código del producto sin que lo pidan.
4. Si el código no puede dar ese paso, el modelo está mal. Corrige la acción o la constante y anota por qué en el comentario de la acción.
5. Nunca debilites una regla ni la quites del `.cfg` para que pase.

## Si el cambio es intencional

- **El cambio invierte una rama de la tabla:** ajusta la constante en `main.cfg` y corre `main.cfg`.
- **El cambio agrega un comportamiento:** agrega o cambia la acción, con un comentario que nombre la función, y agrega su fila a la tabla.
- **Después, en los dos casos:**
  1. Corre las dos configuraciones.
  2. Comprueba que la regla que tocaste todavía puede fallar: invierte en una copia de `main.cfg` la constante que la sostiene. La tabla de abajo dice qué se espera.
  3. Mueve el commit de sincronía.

Qué pasa al invertir cada constante en `main.cfg`, medido en `e0f06a2`:

| Constante invertida | Resultado |
|---|---|
| `RegistraAlLigar` | `SinCarrera` se rompe en 4 pasos |
| `DetieneLaPendiente` | `SinAtascoMudo` se rompe en 7 pasos |
| `AjusteSuelta` y `RespuestaReabre` juntas | `ImpedimentoConSalida` se rompe en 8 pasos |
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
