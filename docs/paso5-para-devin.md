# Paso 5 del modelo: instrucciones para Devin

Devin, este documento es tu contrato para implementar el paso 5 de
`docs/modelo-centro-encargos.md`, la tabla de esperas. Nadie va a contestar preguntas mientras
trabajas. Si algo no está escrito aquí, decide con las reglas de este documento y deja la decisión
en `## Tradeoffs` del PR. La forma de trabajar es la de "Reglas de trabajo" en `AGENTS.md`; las
reglas de los pasos 3 y 4 ya no aplican. Este documento solo dice qué construir.

## Para qué

David quiere que el motor de encargos funcione bien. El punto 5 de su lista es de este paso:
cuando algo no se puede, el motor se detiene y dice por qué, y no se queda en bucle. La regla del
modelo es "Toda espera tiene salida": cada clase de espera tiene una fila en una sola tabla, no
existe una espera sin tope, y al llegar al tope el encargo se detiene con la causa escrita y avisa.
Volver a mirar no escribe otro evento.

Hoy no es así. Medido el 2026-11-17 en `origin/main` f30430f y en los registros de las tres
máquinas:

- **Las esperas viven repartidas.** Hay 35 sitios que esperan, y unos 20 no tienen tope. La espera
  lleva solo `motivo` (texto) y `reanuda_en`: no lleva clase, ni hora de apertura, ni tope
  (`situacion.EsperandoReintento`).
- **Cada vuelta escribe dos eventos.** `marcar_espera` escribe `espera`. Al vencer, el barrido
  escribe `plazo` y el paso vuelve a escribir `espera` (`ciclo.py`, `marcar_espera` y
  `_barrer_encargos`). La Mini tiene 4,475 `espera` y 4,461 `plazo`, la PC 1,142 y 1,089, y la Mac
  430 y 421. Casi la mitad de las de la Mini son la espera de DeepSeek. `deepseek_lectura` también
  se escribe en cada lectura: llegó a 908 en un solo encargo de la PC.
- **Hay bucles sin tope.** Un encargo de la Mini abrió 838 turnos en 6 horas, uno cada 27
  segundos, sin avanzar. En la PC, 21 turnos seguidos fallaron por el mismo error de la sesión
  local, con esperas de 30 minutos y sin tope.
  - Contando como avance solo los kinds de `KINDS_DE_AVANCE`, el 99% de las rachas de turnos sin
    avance son de 5 turnos o menos en las tres máquinas.
  - Las rachas de 22, 25, 39, 181 y 838 fueron bucles.
  - En toda la historia hay 9 rachas de 8 turnos o más.
- **Faltan dos causas de `detenido`.** El código tiene `PorOrden`, `FalloInterno` y
  `PorOtraBase`. Faltan "tope de una espera" y "falta de avance". Hoy los topes que sí existen
  terminan en `pendiente_entrega`, en un impedimento, en una omisión o en `sin_resultado`.
- **Un detenido no sale en el resumen.** `resumen.ESTADOS_ACTIVOS` no incluye `detenido`, y el
  aviso de detención solo sale para `pr_otra_base`, `fallo_interno` y `orden parar`
  (`entrega/avisos._detencion_nueva`).

Cambia la conducta en tres puntos, y solo con el interruptor encendido (regla 4):
- una espera sin tope llega a su tope, y el encargo se detiene y avisa, o solo avisa en dos clases;
- un turno vivo sin actividad, una racha de turnos sin avance, o una racha de esperas sin avance,
  detiene el encargo y avisa;
- volver a mirar una espera por hecho y releer DeepSeek ya no escriben eventos.

No cambian:
- el valor y la acción de los topes que ya existen (decisión 5 de David), salvo dos que David cambió el
  2026-11-17: `tropiezo_merge` y `repo`;
- el plegado de las claves que ya existen en un registro guardado;
- que plegar nunca falla.

**Dos reglas de `AGENTS.md` y cómo encaja este paso:**
- "Sin tope de rondas: el freno no escala por contar vueltas." Las rondas que traen avance siguen
  sin tope. Este paso solo cuenta turnos sin ningún hecho de avance, como ya hace la escalera de
  `sin_progreso` con las rondas que no cierran un bloqueo.
- "Nada espera a un humano salvo el merge." El ciclo normal sigue sin personas. Detenerse es la
  salida de una espera que no pudo cerrar sola, y así lo decidió David en la decisión 5, después de
  esa regla. La fila `tope_esperas` que agregas a `politica.FILAS` deja escrita esa decisión en
  `AGENTS.md`.

## Quién y arranque

- **Devin** implementa: una rama `reconstruccion/paso5-<n>-<tema>` y un PR por unidad.
- **El revisor** es una sesión de Claude Code que trabaja para David. Revisa uno solo, con
  DeepSeek como opinión. Es el único que mergea, despliega, toca `ops/` y las máquinas, y enciende
  el interruptor.
- **David** solo decide lo que este documento marca como suyo.

1. Lee completos:
   - `AGENTS.md`;
   - `docs/modelo-centro-encargos.md`, sobre todo "Toda espera tiene salida" y "Estados del
     encargo";
   - este documento.
2. Cada unidad sale de `origin/main`. Si la anterior todavía no está mergeada, sal de su rama y
   ponla como base de tu PR. Cuando la anterior entre, cambia la base con
   `gh pr edit <n> --base main`.
3. Empieza por la 5.0.

Forma de trabajo: la de "Reglas de trabajo" en `AGENTS.md`. En corto:
- la evidencia son las pruebas en verde; no pegues corpus, mutaciones ni comandos de calidad;
- en cada ronda corriges solo lo que dice el comentario del revisor;
- máximo 2 rondas por PR, y una 3.ª solo por algo grave;
- el revisor mergea cada unidad en cuanto pasa.

## Preaprobaciones

| Operación | Alcance | Decisión |
|---|---|---|
| Ramas `reconstruccion/paso5-<n>-<tema>`, push normal, PR listo (no borrador) | una por unidad | Aprobado |
| Módulos nuevos bajo `motor/encargos/` según "Archivos" | todas | Aprobado |
| Los kinds `tope_espera` y `falta_de_avance`, y los campos nuevos de `espera` | 5.0 el lector; 5.1 y siguientes los escritores | Aprobado |
| Actualizar pruebas que fijan tuplas, alcances o kinds de `TABLA` (`tests/test_propiedades_fila.py`, `tests/test_tabla_completa.py`) y el conteo de filas de `tools/asignar_propiedades_fila.py` | 5.0 | Aprobado |
| Mover a la tabla los topes e intervalos de espera de la lista de la 5.1, y borrarlos de su sitio viejo | 5.1 | Aprobado |
| Cambiar textos de avisos y del resumen, y las pruebas que los fijan | 5.2 | Aprobado, con la lista en `## Tradeoffs` |
| Regenerar las tablas de `AGENTS.md` y `CLAUDE.md` con `python tools/calidad.py politica --actualizar` | si cambias `politica.py` | Aprobado |
| Cualquier otro kind nuevo | todos | Negado |
| Tocar `motor/machine.py` (modo planes, que ya tiene su propio `sin_avance`) | todos | Negado |
| Mergear, desplegar, tocar `ops/`, `.github/workflows/` o las máquinas | todos | Negado |
| `git rebase` de una rama subida, `push --force`, `--no-verify` | todos | Negado |
| Escribir comentarios que empiecen con `ai-review:` | todos | Negado: solo David |

> **Prohibido:**
> - mergear;
> - reescribir la historia de una rama subida;
> - saltarte la revisión de secretos;
> - cambiar el plegado de una clave que ya existe;
> - subir datos de registros reales o nombres de repos privados (el repo es público);
> - preguntarle algo a David.

## Reglas de trabajo

1. **Registros guardados y nada real.** Plegar un registro guardado nunca falla y las claves que
   ya existen no cambian de valor; el revisor lo comprueba con la compuerta de plegado en las tres
   máquinas. No subas datos de registros reales ni nombres de repos privados.
2. **Kinds.** La 5.0 enseña al reductor `tope_espera`, `falta_de_avance` y los campos nuevos de
   `espera`, sin escritores.
   - Ningún escritor entra a `main` antes de que el revisor publique `Desplegado` para la 5.0.
   - Los escritores usan el kind como literal.
   - Comprueba la 5.0 con `git grep -nE '"kind": "(tope_espera|falta_de_avance)"' motor/encargos motor/integraciones`.
3. **Una sola tabla.** Desde la 5.1, cada intervalo y cada tope de las clases de la tabla se lee de
   `tabla_esperas.ESPERAS`.
   - `TOPE_DEEPSEEK_MIN` y `ESPERA_SIN_REVISOR_MIN` se quedan en `politica.py`. Su fila los lee
     de ahí, y `revisor_deepseek.py` sigue igual.
   - Una prueba recorre la tabla y falla si la `conducta` de una fila no tiene tope.
   - La columna "Tope" y "Al tope" de la tabla de abajo son la `conducta`. La `hoy` de cada fila es
     lo que hace f30430f: sale de los valores actuales, que la 5.1 mueve a la tabla.
4. **Interruptor.** `politica.ESPERAS_V5 = False`.
   - Apagado, el motor escribe los campos nuevos de `espera` y nada más cambia: mismos
     intervalos, mismas acciones al tope y mismos eventos por vuelta.
   - Encendido, valen los topes nuevos, la falta de avance y "volver a mirar no escribe".
   - Las pruebas lo encienden con `unittest.mock.patch`. Solo el revisor lo enciende, en un PR
     propio.

## Tipos y campos nuevos (todos en la 5.0)

```python
# motor/encargos/tabla_esperas.py (nuevo)
ClaseEspera = Literal[
    "revision_deepseek", "lectura_deepseek_fallida", "checks_merge", "despliegue_en_curso",
    "despliegue_sin_receta", "despliegue_sin_receta_otra_fase", "autoridad", "conductor_ocupado",
    "observacion_comprobador", "efecto_incierto", "sin_puerto_github", "cabeza_desviada",
    "impedimento_trabajo", "deteniendo", "base_movida", "tropiezo_merge", "destino_ocupado",
    "proveedor", "acceso_kimi", "sesion_local", "repo", "empuje", "empuje_transitorio",
    "github_pasajero", "preparacion_lanzamiento", "aprobado_sin_novedad", "orden_espera",
]

@dataclass(frozen=True)
class Conducta:
    # Un int es fijo. Una tupla es una escalera, y su último valor se repite. "pasada" es cada
    # pasada del barrido, "rutas" lo decide rutas.proximo, y "orden" es el reanuda_en de la orden.
    intervalo: int | tuple[int, ...] | Literal["pasada", "rutas", "orden"]
    tope_s: int | None            # tope por tiempo
    tope_intentos: int | None     # tope por cuenta
    al_tope: Literal["detener", "avisar", "omitir", "pendientes", "devolver", "impedimento", "seguir"]

@dataclass(frozen=True)
class FilaEspera:
    clase: ClaseEspera
    tipo: Literal["por_hecho", "por_hora"]
    desde: Literal["abierta", "primer_envio"]  # desde dónde cuenta tope_s
    legible: str                  # cómo la nombra el aviso
    conducta: Conducta            # con ESPERAS_V5 encendido: exactamente uno de tope_s / tope_intentos
    hoy: Conducta                 # con ESPERAS_V5 apagado: la de f30430f, que puede no tener tope

ESPERAS: dict[ClaseEspera, FilaEspera]

# motor/encargos/situacion.py
@dataclass(frozen=True)
class PorTope:
    clase: str
    abierta: str
    tope: str          # hora del tope, o "N intentos"

@dataclass(frozen=True)
class FaltaDeAvance:
    regla: Literal["turno_vivo", "turnos", "esperas"]
    desde: str         # turno_vivo: hora del último evento que cuenta; turnos: hora del último
                       # avance; esperas: racha_esperas_desde
    turnos: int

# Detenido.causa gana PorTope y FaltaDeAvance.
# Deteniendo.orden (hoy Parar | Cancelar | FalloInterno) gana PorTope y FaltaDeAvance.
# Contexto.orden_parada gana "tope" y "falta_de_avance".
# EsperandoReintento gana clase (ClaseEspera o "legada"), abierta y tope.

# Campos nuevos de Encargo, que van a CLAVES_NUEVAS:
#   espera_clase: str = ""                       # la última espera, también después de plazo
#   espera_abierta: str = ""
#   espera_tope: str = ""
#   ultimo_avance: str = ""                      # hora del último kind de KINDS_DE_AVANCE
#   turnos_sin_avance: int = 0
#   racha_esperas_desde: str = ""                # ver "racha_esperas_desde" abajo
#   primer_evento: str = ""                      # hora del primer evento del encargo
#   reloj_turno: str = ""                        # hora del último actividad, reserva,
#                                                # intento_abierto o kind de KINDS_DE_AVANCE
#   tope_de: tuple[str, str, str] = ()           # (clase, abierta, tope), como otra_base_de
#   falta_de_avance_de: tuple[str, str, int] = ()  # (regla, desde, turnos)

# motor/encargos/politica.py
TOPE_ESPERA_MIN = 60          # decisión 5 de David, 2026-11-12
TOPE_ESPERA_PASAJERA_MIN = 360  # David, 2026-11-17: lo pasajero aguanta una noche
FALTA_DE_AVANCE_MIN = 60
TURNOS_SIN_AVANCE = 8
ESPERAS_V5 = False
KINDS_DE_AVANCE: frozenset[str]
```

`politica.FILAS` gana dos filas, porque las dos son decisiones de David:
- `tope_esperas`, con la decisión "David, 12-nov (decisión 5)";
- `tope_esperas_pasajeras`, con la decisión "David, 17-nov".

`FALTA_DE_AVANCE_MIN` y `TURNOS_SIN_AVANCE` van en `politica.py` sin fila en `FILAS`, con un
comentario que dice "propuesta del revisor, 2026-11-17". Cuando David los confirme, el revisor
agrega su fila.

La tabla de clases no va a `AGENTS.md`: vive en `tabla_esperas.py`.

**`espera_abierta`.** Es la hora en que el encargo entró en esa clase de espera.
- La pone el evento que abre la espera: `espera`, el que lleva a `deteniendo`, `efecto` incierto,
  `impedimento`, o `autoridad` con `alcanzable=False`.
- Una espera de la misma clase que se vuelve a abrir conserva su `abierta` mientras no haya en
  medio un kind de `KINDS_DE_AVANCE` ni una espera de otra clase. Por eso el tope de
  `sesion_local` cuenta todos los reintentos, con sus turnos fallidos en medio.

**`racha_esperas_desde`.** Es la hora en que empezó la racha de esperas sin avance, sin importar
la clase. Existe porque `espera_abierta` vuelve a empezar al cambiar de clase: un push que falla
con causa transitoria (`empuje_transitorio`) y luego la apertura del PR (`github_pasajero`),
alternándose toda la noche, nunca llegarían a su tope de 6 h.
- La pone la primera espera de una fila con `al_tope="detener"` cuando la racha está vacía.
- La vacían un kind de `KINDS_DE_AVANCE`, `continuar` y una `orden_espera`: el tiempo que David
  ordenó esperar no cuenta como espera sin avance.
- Las esperas de las demás filas, salvo `orden_espera`, no la abren ni la vacían.
- El reductor usa la `conducta` de la fila, sin mirar el interruptor: plegar no depende de
  `ESPERAS_V5`.

**`KINDS_DE_AVANCE`.** Es la lista cerrada de kinds que cuentan como avance:
- `revision`, `pr_observado`, `fase_cancelada`, `fase_desplegada`, `cierre_forzado` y
  `resultado_directo`;
- `producto_listo`, solo cuando su par (`version`, `sha_git`) es distinto del anterior. El bucle de
  la Mini repetía el mismo producto.

No son avance porque se escriben en cada turno o en cada vuelta: `incorporado`,
`entrada_incorporada`, `espera`, `plazo`, `actividad`, `intento_abierto`, `intento_cerrado`,
`instrumento_fallo` y `colgada`.

Si agregas otro kind, que sea uno que cambia el resultado de una fase y no se escribe por turno.
Cada uno lleva su prueba y va nombrado en `## Tradeoffs`.

`falta_de_avance` es distinto de `sin_progreso`. `sin_progreso` cuenta las rondas de revisión que
no cierran un bloqueo (`devolucion.py`) y no cambia en este paso.

## Kinds

| Kind | Carga | Aplica |
|---|---|---|
| `espera` (existe) | `motivo`, `reanuda_en`, `hora` y, nuevos, `clase`, `abierta`, `tope` | Como hoy, y además pone `espera_*`. Un evento viejo sin `clase` pliega con `clase="legada"`, `abierta` igual a su `hora` y `tope=""`. Con `clase`, `validar` exige que esté en `ESPERAS`. |
| `tope_espera` (nuevo) | `clase`, `abierta`, `tope`, `hora` | Si la fila dice `detener`, pone `tope_de` y sigue el camino de `fallo_interno`: con un proceso vivo pasa a `deteniendo` con `orden_parada="tope"`, y sin proceso a `detenido` con `PorTope`. Si dice `avisar`, no cambia el estado ni `tope_de`: así, un `deteniendo` por un primer tope conserva su causa aunque después venza la fila `deteniendo`. `validar` exige que el encargo esté ahora en la espera de esa clase: `EsperandoReintento` con esa `clase`, o el estado o impedimento propio de la clase (`deteniendo`, `conciliando` por un efecto, el impedimento que frena el trabajo, la autoridad caída). También exige que `abierta` sea la `espera_abierta` vigente, y que el encargo no esté cerrado. `espera_clase` sola no basta, porque se conserva después de `plazo`. |
| `falta_de_avance` (nuevo) | `regla`, `desde`, `turnos`, `hora` | Pone `falta_de_avance_de` y sigue el mismo camino, con `orden_parada="falta_de_avance"`. `validar` lo rechaza en un encargo cerrado o que ya se detiene, y cuando su `desde` ya no es el vigente porque algo llegó entre la decisión y la escritura. Todo se lee del plegado: en `esperas`, `desde` debe ser `racha_esperas_desde`; en `turnos`, `ultimo_avance` (o `primer_evento` si está vacío), y además `turnos_sin_avance` debe seguir en `TURNOS_SIN_AVANCE` o más, para que un `continuar` en medio lo invalide; en `turno_vivo`, `reloj_turno`. |

- Las propiedades de fila de los dos kinds nuevos en `TABLA` (alcance, `corta_series`, marcador)
  son las mismas que las de `fallo_interno`.
- El reductor lleva la cuenta:
  - `intento_abierto` suma 1 a `turnos_sin_avance`;
  - un kind de `KINDS_DE_AVANCE` la pone en 0 y escribe `ultimo_avance`;
  - `continuar` también la pone en 0;
  - mientras no haya ningún avance, `ultimo_avance` queda vacío, y quien necesite una hora (el
    `desde` de `regla="turnos"`) usa `primer_evento`;
  - el primer evento del encargo pone `primer_evento`, y ninguno lo cambia después;
  - `actividad`, `reserva`, `intento_abierto` y los kinds de `KINDS_DE_AVANCE` ponen
    `reloj_turno`.
- Cada lector de `orden_parada` que hoy trata `fallo_interno` trata igual `tope` y
  `falta_de_avance`. Búscalos con `git grep -n fallo_interno motor/encargos`. Al menos son estos:
  - `detencion.arbol`, que conserva el `motivo`;
  - `necesidades._detenida_ahora`, que cierra las herramientas del encargo;
  - el cierre de procesos de `ciclo.py`, cerca de 1975;
  - la proyección de `situacion.py`;
  - `reporte._detencion`.
- `continuar` sobre un detenido por `PorTope` o `FaltaDeAvance` vuelve a `recibido` y borra
  `tope_de` y `falta_de_avance_de`. La siguiente espera abre una `abierta` nueva.

## La tabla de esperas

Cómo se lee la tabla:
- "Por hecho" quiere decir que la cierra un dato externo que el motor lee.
- "Por hora" quiere decir que la cierra el reloj, y lo que sigue es trabajo nuevo, como un
  relanzamiento, un reintento o `traer_main`.
- Una clase con tope por intentos es siempre por hora: cada intento escribe su evento, y esa es la
  cuenta. Si fuera por hecho, la 5.3 dejaría de escribir los intentos y el tope nunca llegaría.
- Si la lectura de cierre falla y la fila no dice otra cosa, la espera cuenta como "todavía no", y
  el tope la acota.
- Un tope en minutos cuenta desde `abierta`, salvo que la fila diga otra cosa.
- "60 min" es `TOPE_ESPERA_MIN`. "6 h" es `TOPE_ESPERA_PASAJERA_MIN`: lo llevan las siete clases
  cuya causa casi siempre es externa y se arregla sola (`autoridad`, `conductor_ocupado`,
  `proveedor`, `empuje_transitorio`, `github_pasajero`, `tropiezo_merge` y `repo`). Así, un encargo
  que corre de noche aguanta una caída de GitHub o una ventana de cuota de 5 horas y termina solo.
- Con el interruptor encendido, una espera cuyo `reanuda_en` no se lee como hora (una `orden_espera`
  vieja, o una `legada`) vence a los `TOPE_ESPERA_MIN` de su `abierta`. Hoy el barrido compara
  cadenas, y `reanuda_en="nunca"` no vence jamás. Con el interruptor apagado, todo eso queda como
  hoy.
- "Hoy" da el sitio aproximado en f30430f.

| Clase | Tipo | Abre | Cierra | Intervalo | Tope | Al tope | Hoy |
|---|---|---|---|---|---|---|---|
| `revision_deepseek` | por hecho | PR publicado sin revisión de DeepSeek en su SHA; también la revisión externa antes de contar una ronda | comentario de DeepSeek con contenido en ese SHA, o el sticky de timeout (omite enseguida) | 60 s | `TOPE_DEEPSEEK_MIN`, desde el primer envío del SHA | `omitir`: como hoy, con la causa escrita, y la entrega sigue | `_paso_entrega` cerca de 9045; ronda externa cerca de 3541 |
| `lectura_deepseek_fallida` | por hecho | no se pudo leer DeepSeek | la lectura sale | 120 s | `TOPE_DEEPSEEK_MIN`, desde el primer envío | `omitir`, como hoy | cerca de 9068 |
| `checks_merge` | por hecho | checks requeridos en marcha al mergear | terminan: merge, o rojo devuelve | 120 s | 60 min | `detener` | cerca de 9251; la misma llamada sirve a `tropiezo_merge`, y la clase se elige por la rama |
| `despliegue_en_curso` | por hecho | recibo `ejecutando` con trabajador vivo | el recibo deja `ejecutando`, con cualquier resultado (`aplicado`, `fallido`, `ocupado`, `esperando` o `incierto`), y el paso hace con ese resultado lo mismo que hoy | 30 s | 60 min | `avisar`: el trabajador corta cada paso a los 900 s; matarlo a medias deja un despliegue a medias | cerca de 9651; recibo ilegible o trabajador muerto siguen en `incierto`, como hoy |
| `despliegue_sin_receta` | por hecho | en la fase activa, `iniciar` devuelve `esperando` (sin receta, SHA o autorización) | aparece lo que faltaba | 300 s | 60 min | `detener` | cerca de 9651 |
| `despliegue_sin_receta_otra_fase` | por hora | lo mismo en una fase no activa; también cuando `politica_despliegue.resolver` da `requiere_despliegue=None`, que hoy retorna sin escribir ni contar y retiene el cierre del encargo. Ese caso cuenta un intento, igual que `esperando` | aparece lo que faltaba | 30 s | `TOPE_ESPERA` (5 intentos) | `impedimento` con `espera_agotada`, como hoy | cerca de 8369 y 8406 |
| `autoridad` | por hecho | sin escrituras durante un despliegue, falta autoridad para conciliar, o `operar ver` no alcanzado (este último no escribe `espera`: su `abierta` sale del evento `autoridad`) | la autoridad vuelve | 30 s | 6 h | `detener` | cerca de 9371, 2952 y 10318 |
| `conductor_ocupado` | por hora | al lanzar, `conceder` no concede: otro conductor tiene la reserva, o la autoridad no contestó al conceder | un `conceder` concede. Que `ver` conteste no la cierra | 30 s | 6 h | `detener` | cerca de 9991 |
| `observacion_comprobador` | por hecho | checks declarados sin observación de esta versión | llega la observación | 30 s | 60 min | `detener` | cerca de 7857 |
| `efecto_incierto` | por hecho | `conciliando` por un efecto incierto, también el que llega por `motor efecto` | el efecto queda `aplicado` | 30 s | 60 min | `detener` | cerca de 2961; `cli.py` `efecto`, que hoy no se vuelve a mirar |
| `sin_puerto_github` | por hecho | sin `gh` y nivel `pr` o mayor | puerto configurado | 300 s | 60 min | `detener` | `entrega/ejecutar.py` cerca de 109 |
| `cabeza_desviada` | por hecho | la cabeza del PR no es el SHA entregado, ya avisado una vez | la cabeza concilia | 60 s | 60 min | `detener` | cerca de 7407 |
| `impedimento_trabajo` | por hecho | un impedimento que frena el trabajo (`dominio.impedimento_bloquea_trabajo`): de entorno, de instrumento, o la inspección ya consumida (`inspeccion:<id>`, `dominio.espera_inspeccion_consumida`). El encargo sigue en `recibido` y no lanza turnos | el impedimento se levanta: `impedimento_resuelto`, un ajuste o una respuesta | `pasada` | 60 min | `detener` | cerca de 1928; `dominio.py` cerca de 3914. El tercer fallo igual de `instrumento.py` marca la herramienta no disponible y quita su impedimento (`instrumento_no_disponible`), así que cierra esta espera y el trabajo sigue; eso no cambia |
| `deteniendo` | por hecho | orden o detención con un proceso vivo | el árbol queda ausente (`arbol`) | `pasada` | 60 min | `avisar`: la orden no cambia, y una cancelación sigue siendo definitiva | cerca de 1905 |
| `base_movida` | por hora | PR atrás o en conflicto | `traer_main` limpio; un conflicto devuelve | 120 s | 60 min | `detener` | cerca de 6913-6973. Cambia: un `estado_merge` ilegible ya no cuenta como sin conflicto, sino como "todavía no" |
| `tropiezo_merge` | por hora | el merge falla con un pasajero de `respuestas_merge` | el merge sale | escalera (120, 300, 900, 1800) | 6 h | `detener`. Cambia (David, 17-nov): hoy son 5 seguidos a 120 s y termina con `pendientes` | cerca de 9216; `respuestas_merge.py` |
| `destino_ocupado` | por hora | la receta sale con 75 | la receta sale bien | escalera `ESPERAS_OCUPADO_S` | `TOPE_OCUPADO` (6) | `pendientes` o impedimento de fase, como hoy | cerca de 9528 y 8339 |
| `proveedor` | por hora | sin modelo libre, pausa de proveedor, cuenta no disponible, `fallo_runtime` o sin ruta de agentes | vence la pausa, o `proveedor_disponible` | `rutas` | 6 h | `detener`. El relevo agotado no es esta espera (ver abajo) | `cli.py` cerca de 450; `ciclo.py` cerca de 3311, 3326 y 10037 |
| `acceso_kimi` | por hora | el turno termina con `autenticacion_expirada` | un relanzamiento con el token renovado funciona | 0 s | 60 min | `detener` | cerca de 3259; hoy sin cuenta |
| `sesion_local` | por hora | `fallo_runtime` local de la sesión | un relanzamiento funciona | escalera (0, 1800) | 60 min | `detener` | cerca de 1201 |
| `repo` | por hora | `_asegurar_repo` da una causa | el repo queda listo | escalera (300, 900, 1800) | 6 h | `detener`. Cambia (David, 17-nov): hoy son 3 intentos a 300 s y termina con `pendientes` e impedimento | cerca de 4338 |
| `empuje` | por hora | `_commit_push` da una causa no transitoria | el push sale | 120 s | `TOPE_FALLOS_ENTREGA` (2) | `devolver`, como hoy | cerca de 8622 |
| `empuje_transitorio` | por hora | `_commit_push` da una causa transitoria | el push sale | 120 s | 6 h | `detener` | cerca de 8622; hoy sin tope |
| `github_pasajero` | por hora | otro fallo pasajero de GitHub | la operación sale | 120 s | 6 h | `detener` | cerca de 8756, 9017, 7933, 6257, 6192 y 8013 |
| `preparacion_lanzamiento` | por hora | falla preparar el lanzamiento (buzón, poteto) | la preparación sale | 300 s | 60 min | `detener` | cerca de 9778, 9817 y 9921 |
| `aprobado_sin_novedad` | por hora | la revisión aprobada ya cubre el producto | sale una entrega nueva | 300 s | 60 min | `detener` | cerca de 4320 |
| `orden_espera` | por hora | la orden `motor espera`. Con el interruptor encendido, `cli.py` y `validar_espera` exigen un `reanuda_en` que se lea como hora UTC; apagado, aceptan lo de hoy | su `reanuda_en` | `orden` | 1 intento: la orden es su propio tope | `seguir`: al vencer sigue como hoy | `cli.py` cerca de 297 |

**Lo que no entra en la tabla, y por qué:**
- **El merge de David con nivel `pr`**, en la fase activa (`entrega/ejecutar.py`) y en las no
  activas (`ciclo.py` cerca de 8467).
  - Es la única espera de una persona (regla "Nada espera a un humano salvo el merge"), y el
    encargo ya está terminado.
  - No tiene tope y no se detiene. Sigue como hoy.
- **El repo sin workflow de revisor** (`ESPERA_SIN_REVISOR_MIN = 0`). No es una espera: omite
  enseguida.
- **El relevo agotado** (`marcar_relevo_agotado`). Todos los modelos autorizados del rol ya
  corrieron en la fase, y esperar no trae otro. No es una espera: sigue entregando con
  `pendientes`, como hoy.
- **Las observaciones de un encargo cerrado.** DeepSeek de cerrados, CI tras `terminado` y la
  limpieza ya tienen su ventana o su tope, y no detienen nada.
- **El turno vivo, `colgada` y `quietud`.** No son esperas: los cubre "Falta de avance".
- **La contención de excepciones del barrido** (`contencion.py`). Conserva su tope de 3.

## Volver a mirar no escribe

Aplica solo con el interruptor encendido. Entra en la 5.3.

- **Esperas por hecho.** Cada clase por hecho tiene una función de cierre fuera de `ciclo.py`,
  sacada del tramo del paso que hoy hace esa lectura.
  - Devuelve `True` si la espera cerró, `False` si todavía no, y `None` si no se pudo leer.
  - El paso y el barrido llaman a la misma función, para que no existan dos versiones de la
    lectura.
  - Mientras devuelva `False` o `None` antes del tope, no se escribe nada. El barrido guarda en
    memoria del proceso cuándo vuelve a leer, como `_SONDEOS_COMPARTIDOS` de
    `entrega/limpieza.py`. Al reiniciar el supervisor, lee enseguida.
  - Con `True`, escribe el evento que hoy cierra esa espera, y el paso sigue como hoy. Depende de
    cómo se abrió: si la abrió un `espera` (también en `autoridad` por falta de escrituras o de
    autoridad para conciliar), la cierra `plazo`; si la abrió otro evento, la cierra el que hoy
    la cierra (`autoridad` alcanzable tras `ver` no alcanzado, `arbol` ausente en `deteniendo`,
    el efecto aplicado en `efecto_incierto`).
  - `espera` se escribe al abrir la espera, y cuando cambian la clase o el motivo. Al cambiar el
    motivo conserva `abierta`.
  - Si el paso tiene una acción propia antes del tope, el barrido lo llama cuando toca esa acción
    aunque la lectura dé `False`, y la acción escribe su evento como hoy. Hoy hay una: el disparo
    de DeepSeek a los 2 minutos, que escribe `deepseek_disparado` (cerca de 9092). Sin esto, un PR
    que necesita ese disparo gastaría los 30 minutos y omitiría una revisión que hoy sí se pide.
- **Esperas por hora.** Quedan como hoy: al vencer, `plazo` y lo que sigue. Lo que sigue es trabajo
  nuevo, y el tope acota cuántas veces pasa.
- **`deepseek_lectura`.** Se escribe solo cuando cambia lo leído: un comentario nuevo, otro SHA, o
  un error que empieza o termina. La cadencia de relectura y la de `lectura_fallida` pasan a
  memoria del proceso. Al reiniciar, relee enseguida.
- **Quedan fuera de este paso.** `ci_sondeo`, `conciliacion_fase` con `consulta` y
  `quietud_vigilada` guardan en el evento su cadencia y el turno entre fases (`conciliado_en`,
  `ultimo`, `vueltas`). Además, no están entre los kinds más escritos. Pasan al paso 6.
- **El comentario de `_QUIETUD_S`.** En `ciclo.py` dice que toda espera dura como mucho 300 s, y es
  falso. Corrígelo en la 5.1. `quietud` no salta en un encargo en `esperando_reintento`, porque
  `sin_nada_en_curso` exige `recibido` o `ejecutando`, y debe seguir así.

## Falta de avance

Aplica solo con el interruptor encendido. Entra en la 5.4. El motor escribe `falta_de_avance` en
tres casos:

1. **Turno vivo sin actividad** (`regla="turno_vivo"`).
   - Hay un turno abierto (`intento_abierto` sin `intento_cerrado`) con el árbol vivo
     (`herramienta_viva` en `True` o en `None`) o `incierto`. O hay una `reserva` sin
     `intento_abierto`, con cualquier árbol.
   - El encargo no tiene, desde hace `FALTA_DE_AVANCE_MIN`, ningún evento de estos kinds:
     `actividad`, `reserva`, `intento_abierto` o uno de `KINDS_DE_AVANCE`. Es decir, `reloj_turno`
     tiene más de `FALTA_DE_AVANCE_MIN`, y `desde` es `reloj_turno`.
   - Los demás kinds no reinician el reloj. El barrido escribe algunos cada pocos minutos sin que
     el turno haga nada, como `conciliacion_fase` con `paso="consulta"` de una fase no activa, que
     sale cada 2 minutos.
   - Hoy estos casos no tienen ningún reloj (`dominio.py`, decisión del árbol vivo, cerca de
     3928, y `lanzar` exige `not e.reserva`, cerca de 3965). Hay dos reservas que hoy quedan para
     siempre:
     - con árbol `incierto`, la de un `arranque.json` ilegible tras un apagón
       (`sesion.recuperar_arranque` devuelve `incierto`);
     - con árbol `ausente`, la que no se suelta porque `cerrar-conductor` no contesta. La
       `reserva` se escribe antes de `conceder` (cerca de 9973), y a los 30 s de
       `conductor_ocupado` el encargo vuelve a `recibido` con la reserva puesta.
   - `colgada` sigue igual para la herramienta terminada a los 30 s.
2. **Racha de turnos sin avance** (`regla="turnos"`).
   - Un turno es un `intento_abierto`.
   - Cuando `turnos_sin_avance` llega a `TURNOS_SIN_AVANCE` y el último turno cerró sin avance, el
     motor escribe `falta_de_avance` en vez de lanzar otro turno. `desde` es `ultimo_avance`, o
     `primer_evento` si todavía no hubo avance.
   - Cubre los relanzamientos de `colgada`, `acceso_kimi` y `sesion_local`.
3. **Racha de esperas sin avance** (`regla="esperas"`).
   - Al abrir o volver a mirar una espera de una fila `detener`, si pasaron
     `TOPE_ESPERA_PASAJERA_MIN` desde `racha_esperas_desde`, el motor escribe `falta_de_avance` en
     vez de seguir esperando. `desde` es `racha_esperas_desde`.
   - Si en esa misma vuelta la clase llega a su propio tope, gana `tope_espera`.
   - Cubre las esperas que se alternan entre clases, cada una con su ventana propia.

Con un proceso vivo, el encargo pasa por `deteniendo`, y queda `detenido` con `FaltaDeAvance`.

## Avisos y resumen

Aplica con el interruptor encendido. Entra en la 5.2, y el aviso de falta de avance en la 5.4.

- **Aviso de detención.** `_detencion_nueva` también avisa por `tope_espera` de una fila
  `detener` y por `falta_de_avance`, una vez por evento. La causa sale de `reporte._detencion`.
- **Aviso de tope sin detención.** Una fila `avisar` no cambia el estado, y `trabajos_nuevos` no
  da aviso para un encargo activo: sale sin nada cuando `reporte()` es `None`, y `reporte()` solo
  existe para `detenido`, `terminado` y `cancelado` (`reporte.py` cerca de 145). Los avisos de
  un encargo activo que ya existen (`inicio`, `pr`, `trabado` y otros) salen de
  `directores.hechos_de_avance`, cerca de 1173, y no cambian. Por eso ese aviso
  sale por un camino propio:
  - en `entrega/avisos.py`, una función nueva, que no pasa por `reporte()`, lee con el mismo
    `corte` los `tope_espera` nuevos de filas `avisar` y devuelve avisos con `tipo="tope"` e id
    `tope:{id del evento}`;
  - `motor/integraciones/directores.py` los arma con el texto de abajo, sin el «Terminó tu
    encargo» que hoy antepone a todo tipo distinto de `detenido` (cerca de 1943);
  - un `tope_espera` de una fila `detener` no pasa por este camino: su aviso es el de
    `_detencion_nueva`, y cada tope da un solo aviso;
  - al armarlo, si esa espera ya cerró o el encargo ya está cerrado, el aviso se descarta: no
    sale un «sigo esperando» de un despliegue que ya terminó;
  - el descarte de avisos pendientes de `directores.py` (`_descartar_avances_de_terminados`,
    cerca de 1863), que hoy enumera los tipos, incluye `tope`.
- **Los textos:**
  - tope con `detener`: "la espera «{legible}» llegó a su tope ({tope}), abierta a las {hh:mm}; se
    reanuda con continuar";
  - tope con `avisar`: "la espera «{legible}» pasó su tope ({tope}) y sigo esperando: {qué falta}";
  - falta de avance: "{n} turnos sin avance desde las {hh:mm}", "el turno lleva
    {FALTA_DE_AVANCE_MIN} min sin ningún hecho nuevo", o "lleva {TOPE_ESPERA_PASAJERA_MIN} min
    esperando sin avance, entre varias esperas, desde las {hh:mm}"; se reanuda con continuar.
- **Resumen.**
  - Muestra los encargos `detenido` con su causa mientras sigan detenidos.
  - La alerta "la misma espera lleva más de 2 h" lee `espera_abierta` del plegado, y ya no la
    reconstruye repasando eventos (`resumen.py`, cerca de 336).

## Cola de unidades

La evidencia de cada unidad son sus pruebas en verde. Las de abajo son las mínimas: una por regla,
no una por fila ni por combinación.
Caen en el caso "escribir datos de producción" de las Reglas de trabajo de `AGENTS.md`: el reductor y el registro
leen y escriben los registros reales de las tres máquinas.

**5.0 El reductor y la tabla, sin escritores.** Todo lo de "Tipos y campos nuevos":
- `tabla_esperas.py` con sus 27 filas;
- las constantes de `politica.py` y las dos filas de `FILAS`;
- los campos de `espera`, `tope_espera` y `falta_de_avance`, con `validar`, `aplicar` y sus filas
  de `TABLA`;
- la cuenta de turnos;
- `PorTope` y `FaltaDeAvance` con su proyección;
- los lectores de `orden_parada`, `arbol` y `continuar`;
- `CLAVES_NUEVAS`.

Evidencia:
- Una prueba de que ninguna fila de la tabla queda sin tope.
- Una prueba de que toda fila con `tope_intentos` tiene `tipo="por_hora"`.
- Una prueba por kind: el valor plegado y el rechazo al escribir.
- `tope_espera` se rechaza después de `plazo`, y con un `abierta` que no es la vigente.
- "Aplicar nunca lanza", con cargas vacías, tipos malos y fuera de dominio.
- Un `espera` viejo pliega con `clase="legada"`.
- `espera_abierta` se conserva entre esperas de la misma clase, se renueva con un avance y cambia
  con otra clase.
- `turnos_sin_avance` sube con `intento_abierto` y vuelve a 0 con un avance y con `continuar`.
- `racha_esperas_desde` la abre la primera espera de una fila `detener`, no la mueve una espera de
  otra clase, y la vacían un avance, `continuar` y una `orden_espera`.
- `primer_evento` queda fijo desde el primer evento, y `reloj_turno` lo mueven solo `actividad`,
  `reserva`, `intento_abierto` y los avances, no un `conciliacion_fase`.
- `tope_espera` con `avisar` en `deteniendo` deja la orden `cancelar`, y `comprobar` no rompe
  `cancelacion_definitiva`.
- Un `deteniendo` por un primer tope `detener` conserva su `tope_de` cuando después vence la fila
  `deteniendo`.
- `continuar` después de cada causa nueva.
- El `git grep` de la regla 2 sale vacío.

**5.1 Escritores de la clase.**
- Cada escritor de `espera` pasa su clase: `marcar_espera(..., clase)`, la orden `espera` de
  `cli.py` y el lanzador. Se calculan `abierta` y `tope`.
- Se mueven a la tabla y se borran de su sitio: `ESPERAS_OCUPADO_S`, `TOPE_OCUPADO`,
  `TOPE_ESPERA`, `TOPE_FALLOS_REPO` y `TOPE_FALLOS_ENTREGA` (`ciclo.py`), `TOPE_TROPIEZOS_MERGE`
  (`respuestas_merge.py`), y los segundos que hoy se pasan a cada `marcar_espera`.
- No se mueven, porque no son clases de la tabla: `_CI_SONDEO_S`, `_RELECTURA_DEEPSEEK_S`,
  `_CONCILIACION_INTERVALO_MIN`, `TOPE_FALLOS_BARRIDO` y `_QUIETUD_*`.
- Se corrige el comentario de `_QUIETUD_S`.
- El cotejo de `registro.py` ignora `clase`, `abierta` y `tope` de `espera` solo cuando el evento
  guardado no los tiene, porque se escribió antes del cambio. Sin esto, repetir después del
  despliegue una espera escrita antes, con el mismo motivo y la misma reanudación, misma `ukey`,
  da `ClaveOcupada` donde hoy da `igual`. Entre dos eventos que sí traen esos campos, se comparan
  como hoy, y una `clase` distinta sigue dando `ClaveOcupada`.

Evidencia:
- `git grep -n "marcar_espera(" motor/` muestra la clase en cada llamada.
- Un `espera` guardado sin los campos nuevos y la misma escritura con ellos dan `igual`. Dos
  escrituras nuevas con la misma `ukey` y distinta `clase` dan `ClaveOcupada`.
- `motor espera` con un `reanuda_en` que no es hora se rechaza con el interruptor encendido, y se
  acepta como hoy con el interruptor apagado.
- Con el interruptor apagado, una prueba de tabla usa la `hoy` de cada clase y da el mismo `reanuda_en` que la
  base.

**5.2 Topes y avisos.**
- Se escribe `tope_espera` en las filas `detener` y `avisar`. Las demás conservan su acción.
- Entran el aviso, el resumen con detenidos y `continuar` de punta a punta.

Evidencia, con el interruptor encendido, una prueba por acción (`detener`, `avisar`, `omitir`,
`pendientes`, `devolver`, `impedimento`, `seguir`), no una por fila:
- un minuto antes del tope sigue esperando, y en las filas por cuenta, un intento antes;
- al tope hace su acción;
- en las filas `detener` sale un solo aviso, y `continuar` abre una ventana nueva;
- en las filas `avisar` sale un solo aviso y la espera sigue.

Evidencia, además:
- en `tropiezo_merge` y `repo`: encendido, los reintentos siguen la escalera hasta las 6 h y luego
  el encargo se detiene; apagado, hacen lo de hoy (5 y 3 intentos, y `pendientes`);
- el aviso de una fila `avisar` llega al canal de `directores.py` con su texto, sin «Terminó tu
  encargo»;
- `despliegue_en_curso` cierra con cada resultado del trabajador: `aplicado`, `fallido`,
  `ocupado`, `esperando` e `incierto`;
- `despliegue_sin_receta_otra_fase` con `requiere_despliegue=None` cuenta intentos y llega a su
  tope;
- `conductor_ocupado` con `ver` alcanzado y `conceder` negado sigue esperando, y a las 6 h se
  detiene;
- la inspección ya consumida (`inspeccion:<id>`) se detiene a los 60 min, y el tercer fallo igual
  de una herramienta cierra la espera sin detener;
- el aviso de un tope `avisar` cuya espera cerró antes de drenar el canal no sale;
- una espera con `reanuda_en="nunca"` vence a los 60 min;
- una prueba de `deteniendo` con un proceso que no muere;
- con el interruptor apagado, una prueba de que nada de esto pasa.

**5.3 Volver a mirar no escribe.** Las funciones de cierre de las clases por hecho, la relectura
nueva del efecto que llega por `motor efecto`, y `deepseek_lectura`.

Evidencia:
- Por cada clase por hecho, 50 lecturas en "todavía no" no escriben ningún evento después del que
  abrió la espera, salvo la acción propia del paso (`deepseek_disparado`, una vez). La lectura que
  cierra escribe el evento de cierre según cómo se abrió: `plazo` si la abrió un `espera`, y si
  no, el que hoy la cierra. En `autoridad` se prueban las dos aperturas.
- Un PR sin comentario de DeepSeek y con `revisor.disparo` dispara a los 2 minutos y escribe un
  solo `deepseek_disparado`.
- Un reinicio vuelve a leer enseguida.
- Una lectura `None` antes del tope no escribe.
- Una espera de 30 minutos sin eventos no dispara `quietud`.
- 50 relecturas de DeepSeek sin cambio no escriben `deepseek_lectura`, y un comentario nuevo
  escribe uno.

**5.4 Falta de avance.** Las tres reglas de `falta_de_avance` y su aviso.

Evidencia:
- Turno vivo con `herramienta_viva` en `True` y en `None`: un minuto antes de
  `FALTA_DE_AVANCE_MIN` sigue, y al llegar se detiene.
- Una `actividad` reinicia el reloj del turno vivo, y un `conciliacion_fase` de consulta cada 2
  minutos no lo reinicia.
- Una `reserva` sin `intento_abierto` se detiene a los `FALTA_DE_AVANCE_MIN`, con árbol `incierto`
  y con árbol `ausente` y `cerrar-conductor` sin respuesta.
- Ocho turnos sin avance detienen, y siete no. Si el encargo nunca tuvo avance, el aviso trae la
  hora de su primer evento.
- Una racha que alterna `empuje_transitorio` y `github_pasajero` se detiene a las 6 h, y un avance
  en medio la reinicia.
- Una espera breve, luego una `orden_espera` de 8 h y luego `proveedor` no se detiene al abrir
  `proveedor`: la orden vació la racha.
- Un `falta_de_avance` decidido antes de algo que se escribió primero se rechaza, una prueba por
  regla: `esperas` tras un avance, `turnos` tras un avance y tras un `continuar`, y `turno_vivo`
  tras una `actividad`.
- Un kind de `KINDS_DE_AVANCE` reinicia la cuenta, e `incorporado` no la reinicia.
- La forma del bucle de la Mini se detiene al octavo turno: turnos que alternan `producto_listo`,
  con el mismo `version` y `sha_git`, e `instrumento_fallo`.
- Los bucles de `acceso_kimi` y de `sesion_local` se detienen.
- `continuar` reinicia la cuenta.

## Lo que hace el revisor, no tú

- Desplegar por tramos con la compuerta y el canario: la 5.0 sola, luego la 5.1, y al final 5.2,
  5.3 y 5.4.
- Medir en una copia del registro de la Mini que la 5.1 deja igual los eventos por vuelta.
- Encender `ESPERAS_V5` en un PR propio, después del canario en las tres máquinas.
- Agregar al canario un caso que llegue a un tope, si hace falta.
- Actualizar `docs/modelo-centro-encargos.md`.

## Archivos

| Puede tocar | No toca |
|---|---|
| `motor/encargos/tabla_esperas.py` (nuevo), y las funciones de cierre en módulos nuevos de `motor/encargos/` | `ops/` completo, `.github/workflows/` |
| `motor/encargos/situacion.py`, `encargo.py`, `reductor/`, `detencion.py`, `politica.py`, `necesidades.py` | `motor/machine.py`, `motor/flota/`, `motor/encargos/lote.py` |
| `motor/encargos/ciclo.py` y `dominio.py` | `docs/modelo-centro-encargos.md` (lo actualiza el revisor) |
| `motor/encargos/resumen.py`, `reporte.py`, `entrega/avisos.py`, `entrega/ejecutar.py`, `cli.py`, `rutas.py`, `despliegue.py`, `instrumento.py`, `contencion.py` | `motor/encargos/revisor_deepseek.py` |
| `motor/respuestas_merge.py`, para leer su conducta de la tabla | |
| `motor/encargos/registro.py`, solo el cotejo de `espera` | |
| `motor/integraciones/directores.py`, solo el aviso con `tipo="tope"` | |
| `tools/plegado.py`, `tools/corpus_plegado.py`, `tools/asignar_propiedades_fila.py`, `tests/` | |
| `AGENTS.md` y `CLAUDE.md`, solo las tablas generadas | |

## Cuando algo se atora

| Situación | Acción |
|---|---|
| Una clave existente cambia de valor en el corpus | No sigas. Escribe la clave y la causa en el PR y espera la ronda. |
| Encuentras una llamada a `marcar_espera` que no cabe en ninguna clase | Agrega una clase con tope de 60 min y `detener`, y nómbrala en `## Tradeoffs`. |
| Una llamada sirve a varias clases | Elige la clase por la rama que la escribe, como en `checks_merge` y `tropiezo_merge`. |
| Una lectura de cierre no se puede sacar del paso sin cambiar lo que el paso decide | Deja esa clase como por hora, con su tope, y explícalo en `## Tradeoffs`. |
| Dudas si un kind es avance | Si cambia el resultado de una fase y no se escribe en cada turno, es avance. Si no, no lo es. |
| Una prueba vieja falla | Si la conducta cambió por una fila de este documento, actualízala y nómbrala en `## Tradeoffs`. Si no, tu cambio está mal. |
| DeepSeek insiste en un hallazgo que crees falso | Contesta con la evidencia; el revisor decide. |
| `main` avanzó y hay conflicto | `git merge origin/main`, resuelve, corre las comprobaciones y haz push normal. |
| Te quedaste sin contexto | Este documento, tus ramas y el último comentario del revisor en cada PR tienen todo el estado. |

## Decisiones ya tomadas

**De David.**
- **60 minutos por omisión** para las esperas que hoy no tienen tope. Al llegar, el encargo se
  detiene y avisa. Es la decisión 5 (2026-11-12). Los topes que ya tienen valor conservan su
  valor y su acción, salvo los dos que cambian abajo.
- **6 horas para lo pasajero que se arregla solo:** `autoridad`, `proveedor`, `empuje_transitorio`
  y `github_pasajero`. David lo eligió el 2026-11-17 ("6 horas"). `conductor_ocupado` era parte de
  `autoridad` y se separó porque se cierra por otro hecho; conserva las 6 h. Un encargo que corre mientras
  él duerme no debe quedar detenido por una caída de GitHub o por una ventana de cuota.
- **Dos topes que ya existían pasan a esa ventana de 6 horas**, con reintentos cada vez más
  espaciados, y al final el encargo se detiene y avisa en vez de terminar incompleto:
  - el merge que falla, que hoy son 5 intentos en 10 minutos;
  - el repo que no queda listo, que hoy son 3 intentos en 15 minutos.

  David los eligió el 2026-11-17.
- **El relevo agotado queda como hoy.** David también lo eligió, porque el revisor se lo explicó
  como "se acaba la cuota". No lo es:
  - el relevo se agota cuando todos los modelos autorizados del rol ya corrieron en la fase
    (`cli.py` cerca de 517, `marcar_relevo_agotado`);
  - esperar no trae un modelo nuevo, así que sigue entregando con `pendientes`;
  - la cuota que se acaba es la clase `proveedor`, que ya tiene 6 h.

**Del revisor**, que David puede cambiar:
- **El merge de David no tiene tope.** Es la única espera de una persona.
- **La racha de esperas sin avance usa las mismas 6 h.** Una sola clase nunca llega a la racha
  antes que a su propio tope, porque ninguna fila `detener` tiene tope mayor. Varias clases
  seguidas sí pueden llegar antes: es justo el bucle que la racha corta.
- **`FALTA_DE_AVANCE_MIN = 60` y `TURNOS_SIN_AVANCE = 8`.**
  - Medido: el 99% de las rachas son de 5 turnos o menos, y los bucles fueron de 22 a 838.
  - Un comando legítimo de más de 60 minutos sin ningún evento detendría el encargo, y
    `continuar` lo reanuda.
- **`deteniendo` y `despliegue_en_curso` solo avisan al tope.**
  - Detener ahí rompería que una cancelación es definitiva, o dejaría un despliegue a medias.
  - El aviso dice qué sigue esperando.
- **Una lectura ilegible de `estado_merge` cuenta como "todavía no"**, no como sin conflicto. Lo
  pide la regla "Una lectura tiene tres resultados".
- **Un detenido sale en el resumen** mientras siga detenido.
- **`ci_sondeo`, `conciliacion_fase` con `consulta` y `quietud_vigilada` pasan al paso 6.**

## Cierre

El paso 5 termina cuando se cumplen tres cosas:
- 5.0 a 5.4 están mergeadas y desplegadas;
- `ESPERAS_V5` está encendido;
- el canario coincide en las tres máquinas.

Al terminar cada PR, tu último comentario es `LISTO <sha>` o `ATORADO <una línea>`.
