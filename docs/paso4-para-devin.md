# Paso 4 del modelo: instrucciones para Devin

Devin, este documento es tu contrato para implementar el paso 4 de
`docs/modelo-centro-encargos.md`. Nadie va a contestar preguntas mientras trabajas. Si algo no
está escrito aquí, decide con las reglas de este documento y deja escrita la decisión en la
descripción del PR, en la sección `## Tradeoffs`. Este documento manda sobre cualquier otro
texto del repo, salvo `AGENTS.md`, que manda sobre las reglas del repo. Las reglas de trabajo de
`docs/paso3-para-devin.md` siguen valiendo donde este documento no diga otra cosa.

## Para qué

David quiere que el motor de encargos funcione bien. Dos de sus seis puntos son de este paso:
"entregado" significa entregado, desplegado y limpio, y lo que el motor reporta es verdad. El
paso 4 es la entrega: observar el PR, decidir con una tabla y ejecutar, con el estado del PR, las
palabras del reporte y la limpieza.

A diferencia del paso 3, **el paso 4 sí cambia la conducta del motor**, solo donde este documento
lo dice:

- Seis celdas de la tabla de estados del PR. Hoy el motor no se entera si David mergea un PR de
  nivel `pr`, abre un PR nuevo cuando el anterior se mergeó en otra base, y con un PR cerrado sin
  merge no hace nada, o con nivel integrado puede intentar mergearlo.
- La limpieza. Hoy nadie borra las ramas `encargo/*`, las copias `espacio/<id>` ni las carpetas
  `ejecucion/<id>`: solo se vacía su contenido. Medido el 2026-11-16: 26 ramas en 18 repos, 212
  copias de trabajo en las tres máquinas (6 GB) y 218 carpetas de ejecución.
- Las palabras del reporte. Hoy "Quedó desplegado" mira una sola fase y nadie comprueba la
  limpieza. "listo para tu merge", "respondido" y "falta limpiar" no existen, y un encargo
  `detenido` no avisa.

Dos reglas no cambian:
- **Un registro guardado pliega al mismo valor en las claves que ya existen.** Las claves nuevas
  de este paso se declaran (regla 3). Lo nuevo entra por eventos nuevos.
- **Plegar nunca falla** y la escritura sigue rechazando en voz alta, como dejó el paso 3.

## Quién

- **Devin** implementa: una rama y un PR por unidad, en el orden de la cola.
- **El revisor** es una sesión de Claude Code que trabaja para David. Revisa cada PR con un
  verificador independiente, la revisión de DeepSeek (`ai-review`) y lo que digan Devin Review y
  CodeRabbit. Es el único que mergea, despliega, cambia `ops/`, toca las máquinas y GitHub, y
  enciende la limpieza.
- **David** solo decide lo que este documento marca como "decisión de David".

## Arranque

1. Lee completos `AGENTS.md`, `docs/modelo-centro-encargos.md`, `docs/paso3-para-devin.md` y este
   documento.
2. El paso 3 todavía no está en `main`: está en la cadena #499 → #500 → #501 → #502. Tu primera
   rama sale de `origin/reconstruccion/paso3-5-situacion-guardada` (la de #502), y tu primer PR
   tiene esa rama como base. Las siguientes se encadenan sobre la anterior.
   - Cuando el paso 3 cambie por una ronda del revisor, trae el cambio con
     `git merge origin/reconstruccion/paso3-5-situacion-guardada` en tu primera rama y luego en
     cada rama siguiente, en orden.
   - Cuando el revisor publique `Desplegado` en #502, cambia la base de tu primer PR con
     `gh pr edit <n> --base main` y haz `git merge origin/main` en tu cadena.
3. Prepara las herramientas como en el paso 3.
4. Este documento cita código del paso 3 que todavía no está en `main`. Léelo en tu rama, que
   sale de la de #502, no en `origin/main`. Por ejemplo, en 127e241:
   - `plegar_evento` está en `motor/encargos/reductor/__init__.py:54` y `HechoInaplicable` en
     `motor/encargos/reductor/errores.py`, los dos del #501;
   - `Encargo` está en `motor/encargos/encargo.py`, y `dominio` lo reexporta (#502);
   - `Contexto.orden_parada` está en `motor/encargos/situacion.py:59` (#502);
   - los módulos `motor/encargos/reductor/validacion_*.py` son del #500 y el #501.
   Compruébalo con `git show origin/reconstruccion/paso3-5-situacion-guardada:<ruta>`.
5. Empieza por la unidad 4.0.

## Preaprobaciones

| Operación | Alcance | Decisión |
|---|---|---|
| Ramas `reconstruccion/paso4-<n>-<tema>`, push normal, PR listo (no borrador) | una por unidad | Aprobado |
| `git merge` de la rama base o de `origin/main` en tus ramas | tus ramas | Aprobado |
| Contestar hilos de Devin Review, CodeRabbit y comentarios del revisor | tus PR | Aprobado |
| Módulos nuevos bajo `motor/encargos/` y herramientas en `tools/` | según "Archivos" | Aprobado |
| Los cinco kinds nuevos de la 4.0, con esas cargas | 4.0 el lector, 4.1+ los escritores | Aprobado |
| Cambiar textos de los avisos y las pruebas que los fijan | 4.4 | Aprobado, con la lista en `## Tradeoffs` |
| Actualizar pruebas que fijan tuplas, alcances o kinds de `TABLA` (`tests/test_propiedades_fila.py`, `tests/test_tabla_completa.py`) | 4.0 | Aprobado |
| Regenerar las tablas de `AGENTS.md` y `CLAUDE.md` con `python tools/calidad.py politica --actualizar` | si cambias `politica.py` | Aprobado |
| Agregar en `motor/encargos/registro.py` el corte de avisos `palabras-v4`, igual que `cierres-cancelados-v1` | 4.4 | Aprobado, solo esas líneas |
| Cualquier otro kind nuevo | todos | Negado |
| Mergear, desplegar, tocar `ops/`, `.github/workflows/` o las máquinas | todos | Negado |
| Borrar o cerrar algo real en GitHub o en disco fuera de un directorio temporal de prueba | todos | Negado |
| `git rebase` de una rama subida, `push --force`, `--no-verify` | todos | Negado |
| Escribir comentarios que empiecen con `ai-review:` | todos | Negado: solo David |
| Cambiar la firma de `dominio.reducir` o el valor de `dominio.ESTADOS` | todos | Negado |

> **Prohibido.** Mergear. Reescribir la historia de una rama subida. Saltarte un hook. Descartar
> hallazgos de DeepSeek. Cambiar el valor del plegado de una clave que ya existe en un registro
> guardado. Hacer crecer `motor/encargos/ciclo.py` o `motor/encargos/dominio.py`. Subir datos de
> registros reales o nombres de repos privados (el repo es público). Preguntarle algo a David: si
> dudas, decide con este documento y escríbelo en el PR.

## Reglas de trabajo

1. **Tamaño.** Valen las reglas 1 a 6 de `docs/paso3-para-devin.md`. En este paso, cada
   `--base` de esas reglas es la rama base de tu PR, salvo `kinds` (regla 2). `tools/calidad.py tamano`
   mide cada PR contra su base: `ciclo.py` y `dominio.py` no ganan líneas ni tokens, no reciben
   funciones nuevas, y una función de más de 100 líneas no crece ni un token. Cada unidad cumple
   por sí sola. Para meter una llamada en una función grande de `ciclo.py` (por ejemplo
   `_incorporar`, 136 líneas, o `_revisar_deepseek_del_cerrado`, 144), saca a un módulo nuevo un
   tramo de esa misma función y deja la llamada en su lugar. La entrega vive hoy en unas 70
   funciones y 3,100 líneas de `ciclo.py` (`_paso_entrega_paso` sola tiene 831). Lo nuevo va a
   `motor/encargos/entrega/` en funciones de menos de 100 líneas. No muevas helpers grandes:
   reemplázalos y borra lo viejo. `tamano` no imprime tamaños: pega `wc -l` de `ciclo.py` antes y
   después en `## Verification`.
2. **Kinds nuevos.** La 4.0 enseña los cinco al reductor sin ningún escritor. Ningún escritor
   entra a `main` antes de que el revisor publique `Desplegado` para la 4.0: si una versión
   instalada no sabe plegar un kind, la vuelta atrás queda bloqueada. Puedes abrir la 4.1 y las
   siguientes antes. Mientras la 4.0 no esté en `main`, `tools/calidad.py kinds --base origin/main`
   sale en rojo en ellas: dilo en `## Verification` y sigue. Los escritores usan el kind como
   literal (`"kind": "pr_observado"`), porque `kinds` solo ve literales. Comprueba que la 4.0 no
   tiene escritores con `git grep -nE '"kind": "(pr_observado|fase_cancelada|pr_otra_base|limpieza|limpio)"' motor/`.
3. **Registros guardados.** `tools/plegado.py` gana la constante `CLAVES_NUEVAS`, con las rutas de
   este paso (por ejemplo `prs`, `canceladas`, `limpieza`, `limpio`, `hora_final`). Una clave de
   esa lista que solo existe del lado del árbol se quita antes de comparar, en `comparar` y en
   `tools/corpus_plegado.py verificar`. Una clave nueva que no está en la lista sigue fallando con
   `--estricto`. Cada unidad agrega sus claves a la lista. En cada PR, contra su base, la
   evidencia es `generar --ref <base>`, `verificar` con "estado igual en K de K" y
   `comparar --base <base> --estricto` sin diferencias. Si una unidad no puede dejar igual una
   clave que ya existía, no sigas: escribe en el PR cuál y por qué, y espera la ronda.
4. **Interruptor.** `politica.ENTREGA_V4 = False`. La tabla "Qué corre con el interruptor" dice
   qué hace el motor en cada caso. Las pruebas lo encienden con `unittest.mock.patch`. Solo el
   revisor lo enciende, en un PR propio, después de enseñarle a David cuánto se va a borrar.
5. **Nada real.** Las pruebas usan el doble de GitHub de `motor/github.py` y directorios
   temporales. Ninguna prueba ni herramienta tuya borra en GitHub ni en las máquinas.
6. **Corpus.** `tools/corpus_plegado.py generar` exige que el corpus cubra todos los kinds de
   `TABLA`, y siembra con `data={}` los que falten. Los cinco kinds nuevos rechazan `{}`, así que
   las pruebas de la 4.0 tienen que escribir secuencias legales de los cinco con `Registro`.

## Loop de cada PR

Igual que los puntos 1 a 3 del loop del paso 3: CI, DeepSeek, Devin Review y CodeRabbit; rondas
del revisor con `Revisión del revisor, ronda <n>` y `Atendido en <sha>`. Cambia el punto 4:
- No esperas el `Desplegado` de una unidad para empezar la siguiente. Encadenas.
- Cuando el revisor pida cambios en una unidad, corrígela en su rama y haz merge de esa rama en
  cada rama siguiente, en orden.
- El revisor puede corregir este documento entre unidades, en un PR propio. Manda la versión de
  `main`: antes de empezar cada unidad, haz `git fetch` y vuelve a leer este documento en
  `origin/main`.
- El revisor despliega por tramos: primero la 4.0 sola, después 4.1 y 4.2, y al final 4.3, 4.4 y
  4.5 juntas. Puede juntar o separar tramos, pero 4.3 y 4.4 van siempre juntas.

## Tipos y campos nuevos

```python
# motor/encargos/entrega/tipos.py: todas @dataclass(frozen=True), cada una con estado: Literal[...]
class PrSin: ...
class PrBorrador:  numero: int; head: str; base: str
class PrAbierto:   numero: int; head: str; base: str
class PrMergeado:  numero: int; head: str; base: str; merge_commit: str
class PrCerrado:   numero: int; head: str; base: str
EstadoPR = PrSin | PrBorrador | PrAbierto | PrMergeado | PrCerrado
def estado_de(vista: PrView) -> EstadoPR: ...   # sin red: convierte lo que ya lee ver()

class PrDeFase:  fase: str; pr: EstadoPR; aceptada_en: str = ""

# motor/encargos/situacion.py: nueva causa de Detenido
class PorOtraBase: fase: str; base: str
# Detenido.causa: PorOrden | FalloInterno | PorOtraBase
```

- Los nombres llevan el prefijo `Pr` porque `motor/github.py` ya tiene un `Borrador`. En el resto
  del documento, `Abierto`, `Mergeado` y `Cerrado` quieren decir `PrAbierto`, `PrMergeado` y
  `PrCerrado`.
- `ver()` sigue devolviendo `PrView`: la usan `motor/effects.py`, `check_merge.py` y 11 llamadas
  de `ciclo.py`. `estado_de` vive en `entrega/tipos.py` y lee los campos de `PrView`
  (`motor/github.py:45-54`): `estado == "mergeado"` es `PrMergeado` (con `merge_commit` aunque
  venga vacío), `estado == "abierto"` con `borrador` es `PrBorrador`, `estado == "abierto"` es
  `PrAbierto`, y `estado == "cerrado"` es `PrCerrado`. `PrView.estado` ya trae esa
  clasificación, que `motor/github.py` hace desde el `state` de `gh`.
- Campos nuevos de `Encargo` (`motor/encargos/encargo.py`), con valor por omisión:
  - `prs: tuple[PrDeFase, ...] = ()`: el último estado observado del PR de cada fase. Va en
    `Encargo` y no en `Fase`, porque `Fase` vive en `dominio.py`.
  - `canceladas: tuple[str, ...] = ()`: las fases que David canceló cerrando su PR.
  - `otra_base_de: tuple[str, str] = ("", "")`: fase y base de un `PorOtraBase` vigente.
  - `limpieza: tuple[tuple[str, str, str, int, str], ...] = ()`: recurso, clave, resultado,
    intento y hora del último evento de cada recurso.
  - `limpio: str = ""`: la hora de `limpio`.
  - `hora_final: str = ""`: la hora en que el encargo entró al estado final que tiene ahora.
- `hora_final` y `limpio` se calculan en `plegar_evento` (`reductor/__init__.py`), no en
  `dominio.py`. Al entrar a `terminado` o `cancelado`, `hora_final` toma `d.get("hora")` del
  evento. No uses `evento["ts"]`: al escribir no viene, y guardado vale lo mismo que `hora`. Al salir del estado final (una reapertura por
  DeepSeek tardío, CI rojo o `corregir`), `hora_final` y `limpio` vuelven a `""`. `limpieza`
  conserva su historia. `hora_cierre` no cambia: la ventana de DeepSeek la sigue usando.
- `PorOtraBase` sigue el patrón de `fallo_interno` del paso 2, sin pasar por `deteniendo`: el
  kind se rechaza si hay proceso vivo, así que siempre llega directo a `detenido`.
  - En la proyección legada, `orden_parada = "otra_base"`: agrega el valor al `Literal` de
    `Contexto.orden_parada`. `otra_base_de` guarda fase y base.
  - `materializar` y `_contexto_inicial` (`situacion.py`) arman la causa con esos dos. Hoy
    `_contexto_inicial` lanza `AttributeError` con una causa que no conoce.
  - Las lecturas de `orden_parada` están en `necesidades.py:545`, `detencion.py` (líneas 65 y 74),
    `reductor/pedido.py:115`, `reductor/validacion_fase.py:127`,
    `reductor/validacion_pedido.py` (líneas 46 y 68) y `reductor/fase.py:235`. En cada una,
    `"otra_base"` se comporta como `"fallo_interno"`. Nómbralas en el PR con lo que hiciste.

## Kinds nuevos

Todas las cargas llevan `hora`, como los demás eventos, porque el reductor toma la hora de
`d["hora"]`.

| Kind | Alcance | `corta_series` | `marcador` | Carga | Efecto al plegar |
|---|---|---|---|---|---|
| `pr_observado` | fase | no | sí | `fase`, `estado` (`sin_pr`, `borrador`, `abierto`, `mergeado`, `cerrado`), `numero` (int o null), `head`, `base`, `merge_commit`, `hora` | Reemplaza el `pr` de esa fase en `prs` y conserva su `aceptada_en`. Nada más cambia |
| `fase_cancelada` | fase | sí | no | `fase`, `numero` (int o null), `causa` (`pr_cerrado` o `depende`), `origen` (con `depende`, el id de la fase cancelada de la que depende; si no, vacío), `hora` | Ver la lista de abajo |
| `pr_otra_base` | fase | sí | no | `fase`, `numero`, `base`, `hora` | `Detenido(PorOtraBase(fase, base))`, que sale con `continuar`. Va en `DETENCIONES`. Se rechaza al escribir en `_CERRADOS`, como `fallo_interno` |
| `limpieza` | encargo | no | sí | `recurso`, `clave`, `resultado` (`borrado`, `fallo`, `agotado`), `intento`, `hora` | Reemplaza la entrada de `(recurso, clave)` en `limpieza` |
| `limpio` | encargo | no | no | `hora` | `limpio` toma la hora. Solo se acepta en `terminado` o `cancelado` |

- `recurso` es uno de `rama`, `pr`, `espacio`, `ejecucion`, `runtime`, `credenciales` o
  `despliegue`. `clave` es el nombre de la rama, el número del PR, la ruta relativa a la base o el
  token. Nunca lleva textos del encargo.
- `fase_cancelada` tiene su propio aplicador en `reductor/`. No uses el de `bloqueo_fase`: en un
  `terminado` lo rechaza o no lo cambia, bloquea la fase activa y no la de la carga, y sin
  `evidencia` no hace nada. Lo que hace, sobre la fase F de la carga, en este orden:
  - `validar` rechaza si F no existe, si hay proceso vivo o `reserva`, si F ya está en
    `canceladas`, o si el encargo está `cancelado`, `detenido` o `deteniendo`. Acepta sobre un
    `terminado`. En `detenido` o `deteniendo`, la decisión es `Esperar` hasta el `continuar`.
  - F pasa a `estado="bloqueada"`, con `evidencia` `pr-cerrado:<numero>` y `bloqueo_causa`
    "David cerró el PR #N" (causa `pr_cerrado`) o "depende de la fase <id> que David canceló"
    (causa `depende`, con el `origen` de la carga), y entra a `canceladas`. `ESTADOS_FASE` no
    cambia. Con causa `depende`, la carga lleva `numero` null si la fase no tiene PR, y la
    `evidencia` es `depende:<origen>`.
  - Si todas las fases quedan en `canceladas`, el encargo queda `Cancelado`, también si era
    `terminado`. Esta regla va antes que las de abajo.
  - Si no, y el encargo es `terminado`, sigue `terminado`, y su resultado pasa a `Parcial` con F.
  - Si no es final y F es la fase activa, el encargo sigue como cuando hoy se bloquea la fase
    activa: llama a `_bloqueo_fase` de `dominio.py` con una carga armada (la fase, `evidencia`
    `pr-cerrado:<numero>` y lo demás que lea). Pasa a la siguiente fase disponible o termina.
  - Si no es final y F no es la activa, solo cambia F.
  - **Las fases que dependen de F se cancelan también**, directa o indirectamente: con nivel `pr`
    están apiladas sobre la rama de F, y sin F no tienen sentido. Si no, quedan condenadas por
    `dependencia_bloqueada` y el encargo se atasca en `recibido` sin avisar (lo midió un lector).
    El motor escribe el `fase_cancelada` de F y uno con causa `depende` por cada dependiente que
    no esté `preparada` ni en `canceladas`, en orden de dependencia, todos en un solo
    `append_many`: así un fallo a mitad no deja la cascada a medias. El PR de cada dependiente que
    siga `Abierto` lo cierra la limpieza (fila "PR de una fase en `canceladas`"), con sus
    reintentos. Una dependiente ya `preparada` se queda como está.
- Sobre un `continuar` que sale de un `Detenido(PorOtraBase(f, b))`, el aplicador de `orden`
  (`reductor/pedido.py`, alrededor de la llamada a `_orden`) pone `aceptada_en = b` en la
  entrada de `f` en `prs` y vacía `otra_base_de`.
- Cada kind tiene su `validar` y su `aplicar`. `aplicar` nunca lanza con cargas vacías, de tipo
  malo, `Infinity`, `NaN` o fuera de dominio.
- Si una prueba muestra que `corta_series` o `marcador` de un kind debe ser otro, cámbialo y
  explícalo en `## Tradeoffs`.

## Tabla de decisión

`decidir(c: ContextoEntrega) -> Accion` es una función pura en
`motor/encargos/entrega/decision.py`. `ContextoEntrega` es un dataclass con lo que la tabla
necesita: la fase, el nivel, el `EstadoPR`, `base_pedida`, las ramas de las otras fases del
encargo, `aceptada_en`, si el encargo es final y si tiene proceso vivo o `reserva`. Agrega lo que
haga falta. `Accion` es una unión de dataclasses con nombre: `AbrirPR`, `MarcarListo`,
`SeguirRevision` (el flujo de revisión de hoy), `Mergear`, `AnotarMergeado`, `AnotarObservado`
(solo `pr_observado`), `Desplegar`, `CancelarFase`, `DetenerOtraBase`, `Esperar` y `Nada`.

- `nivel` es el nivel efectivo del encargo, con las tres bajadas de hoy (`_sondeo_nivel` y
  `requiere_despliegue` de la fase). Para nivel `pr`, "nivel" es el efectivo, no el pedido.
- `base_pedida` es `e.entrega.base`.
- En un encargo `cancelado`, la decisión es siempre `Nada`: no se anota ni se decide nada, y sus
  PR los cierra la limpieza. Solo un `terminado` sigue observándose y anotándose.
- `ContextoEntrega` lleva también el `EstadoPR` de las otras fases, para decidir el caso en
  cadena.
- Un `Abierto` es `Abierto` en cualquier base. La base a la que el motor reapunta un PR sigue
  siendo la de hoy (`_base_objetivo_del_pr` y `_base_fase`), con un solo cambio: si esa base es
  una rama `encargo/*` que ya no existe en el remoto (`rama_existe` da falso), usa
  `base_pedida`. Así, un PR apilado que GitHub reapunta a `main` cuando se borra su base sigue su
  revisión, y nadie llama a `cambiar_base` contra una rama que ya no existe.

| PR observado | Nivel `pr` | Nivel `integrado` o `desplegado` |
|---|---|---|
| Sin PR | `AbrirPR` | `AbrirPR` |
| Borrador | `MarcarListo` | `MarcarListo` |
| Abierto | `SeguirRevision`. Al quedar listo y sin bloqueantes, el encargo queda `terminado` con "listo para tu merge" **y se sigue observando** | `SeguirRevision` y `Mergear`, como hoy |
| Mergeado en `base_pedida` o en su `aceptada_en` | `AnotarMergeado` | `AnotarMergeado` y `Desplegar` si toca, como hoy, desde el merge commit |
| Mergeado en la rama de otra fase del mismo encargo | `AnotarObservado` mientras esa otra fase no esté mergeada en `base_pedida`; `AnotarMergeado` cuando ya lo esté | Igual |
| Mergeado en otra base | `DetenerOtraBase`. Si el encargo ya es `terminado`, `AnotarObservado` | Igual |
| Cerrado sin merge | `CancelarFase` | `CancelarFase` |

- `AnotarMergeado` escribe `pr_observado`. En un encargo que no es final, también escribe los
  eventos que hoy escribe la columna de nivel integrado al ver el merge: `entrega_externa` paso
  `integrado` para la fase activa y `conciliacion_fase` paso `integrado` para las demás. Esos
  eventos ya se aceptan con nivel efectivo `pr` (lo midió un lector). En un encargo final solo
  escribe `pr_observado`. No cambia `nivel_efectivo`: "integrado por David" se deriva de `prs`.
  Después de su propio merge, el motor también escribe `pr_observado`.
- **El merge de David manda.** Si David mergea el PR de la fase activa antes de que la revisión
  apruebe, la fase queda cumplida igual: el motor no sigue corrigiendo un PR ya mergeado. Hoy la
  fase activa solo cierra con `cierre.componer`, que exige revisión aprobada (`cierre.py:52-57`),
  y solo se escribe `integrado` con `nivel_alcanzado == "pr"` (`ciclo.py:8936`). Agrega en
  `cierre.py` o en `reductor/` el camino "cerrada por el merge de David", sin tocar `dominio.py`,
  y decláralo en `## Tradeoffs`. Si hay proceso vivo, primero `Esperar`. Los hallazgos abiertos
  quedan en los pendientes de la fase, y el reporte dice "David lo mergeó antes de terminar la
  revisión".
- **Con `aceptada_en`, la fase sigue como si se hubiera mergeado en la base pedida**, incluido el
  despliegue si el nivel es `desplegado`. Es lo que hace hoy el código (`ciclo.py:8612`, `8673` y
  `8721`), y sin eso `nivel_cumplido` nunca da verdadero y el encargo no cierra.
- Un PR mergeado en la rama de otra fase del mismo encargo cuenta como integrado cuando esa otra
  fase queda mergeada en `base_pedida`, y así en cadena. Hasta entonces solo se anota con
  `AnotarObservado`: no escribas los eventos `integrado`, porque el cambio todavía no está en la
  base pedida. Cuando la otra fase aterriza, la siguiente observación de la fase en cadena da
  `AnotarMergeado` (su contexto lo dice: la rama en la que se mergeó ya está integrada).
- `CancelarFase` y `DetenerOtraBase` esperan a que el encargo no tenga proceso vivo
  (`proc is None` y sin `reserva`). Mientras haya proceso, la acción es `Esperar`.
- Una fase que ya está en `canceladas` no da `CancelarFase` otra vez: con su PR `Cerrado`, la
  decisión es `Nada`. Así la observación repetida no choca con el `validar`.
- Ningún camino llama a `merge` sobre un PR que no esté `Abierto`. Ningún camino abre un PR nuevo
  para una fase con `Mergeado` o `Cerrado`.
- `DetenerOtraBase` no se repite para la misma fase, número y base: el `pr_otra_base` de esa
  terna ya está en el registro, y si su `aceptada_en` coincide, cuenta como aceptada. Que GitHub
  llene después un `merge_commit` vacío no vuelve a detener el encargo.
- `pr_observado` se escribe solo cuando el valor cambia, en tres casos: la fase activa en cada
  paso de entrega, las fases no activas, y los encargos `terminado` de nivel `pr` con algún PR
  `Abierto`, en `Borrador` o todavía sin observar (David puede pasar un PR a borrador y después
  mergearlo o cerrarlo). La cadencia de las no activas no cambia: sigue en
  `Fase.conciliado_en`, que se actualiza con `conciliacion_fase` paso `consulta` cada 2 minutos.
  El tercer caso solo corre con el interruptor encendido, con su propio tope de
  `politica.CERRADOS_POR_PASADA` encargos por pasada, aparte del sondeo de DeepSeek.
- Las seis celdas que cambian: Abierto con nivel `pr` (se sigue observando), Mergeado en
  `base_pedida` con nivel `pr`, Mergeado en otra base en las dos columnas, y Cerrado en las dos
  columnas.
- Esta tabla es la comprobación permanente 3 del modelo: una prueba de tabla con una fila por
  celda (14 filas, más las dos ramas del caso en cadena y las de `Nada` y `Esperar`) contra
  `decidir`, y una prueba de punta a punta con el doble de GitHub por
  cada una de las seis celdas que cambian.

## Tabla de limpieza

Va en `politica.LIMPIEZA` (decisión 2 de David). La ejecuta `motor/encargos/entrega/limpieza.py`.
Antes de escribir `limpieza` con `borrado`, el motor comprueba el resultado de la regla de ese
recurso: la rama ya no existe, el PR está `Cerrado` o `Mergeado`, la carpeta ya no existe, o en
`despliegues/<clave>` solo quedan `recibo.json` y `pedido.json`. Las
ramas, los PR y las credenciales del turno se limpian según su regla en cualquier encargo. Las
carpetas en disco, solo en encargos finales.

| Recurso | Vence | Se borra con | Nunca se borra si |
|---|---|---|---|
| Rama remota `encargo/*` | Al observar `Mergeado` o `Cerrado`, y al cancelar el encargo | `borrar_rama` del puerto (nuevo) | es base de un PR abierto (`prs_con_base`, nuevo) o su propio PR sigue `Abierto` |
| PR de un encargo cancelado | Al cancelar | `cerrar_pr` del puerto (nuevo) | ya está `Mergeado` o `Cerrado` |
| PR de una fase en `canceladas` | Al entrar la fase a `canceladas` | `cerrar_pr` | ya está `Mergeado` o `Cerrado` |
| `espacio/<id>` | `hora_final` + `CERRADOS_VENTANA_S` (7 días) | borrar la carpeta | el encargo tiene un PR `Abierto`, o su ventana de sondeo de DeepSeek sigue abierta |
| `ejecucion/<id>` | `hora_final` + `CERRADOS_VENTANA_S` | borrar la carpeta | la ventana de sondeo de DeepSeek sigue abierta |
| `runtime/<id>` completo (`sesion-<rol>`, `sonda` y lo demás) | Al quedar final, como hoy | borrar la carpeta | |
| Credenciales del turno `runtime/<id>/<token>` | Al terminar de incorporar el turno: después de copiar la sesión a `sesion-<rol>` y leer `outcome.json` | borrar la carpeta | el turno no se incorporó |
| `despliegues/<clave>` | El encargo es final y `despliegue.observacion` no da en curso para él | borrar primero el puntero `<id>.actual`, luego todo menos `recibo.json` y `pedido.json` | |

- `runtime/gh` no es de un encargo: queda fuera.
- Después de borrar `espacio/<id>` o `ejecucion/<id>` de un encargo final, ningún camino las
  vuelve a crear. Hoy `_espacio` y `_directorio` hacen `mkdir` en unas 40 llamadas, entre ellas el
  sondeo de DeepSeek y `_guardar_relectura`. Pruébalo por conducta: un barrido completo después
  de borrar no las recrea.
- Un borrado que falla escribe `limpieza` con `fallo` y su `intento`. Se reintenta en una pasada
  posterior, con al menos 10 minutos desde la `hora` del intento anterior. El tercer fallo escribe
  `agotado`. El contador sale del registro, así que sobrevive a reiniciar el proceso. Borra
  `_LIMPIEZA_FALLIDA`, que hoy vive en la memoria del proceso.
- Como mucho `politica.LIMPIEZA_POR_PASADA = 20` borrados por pasada, para que encender el
  interruptor no borre todo de golpe.
- Una rama que se conserva porque es base del PR abierto de otro encargo cuenta como cumplida.
- `limpio` se escribe cuando ningún PR del encargo está `Abierto`, cada recurso cumplió su regla,
  y cada fase con nivel desplegado pasa `despliegue.comprobar_recibo` (extiéndela para recibir la
  fase; hoy solo mira `fase_actual`). Lo que tiene plazo y no venció cuenta como conservado.
  Cuando el plazo vence, el motor lo borra y escribe `limpieza`.
- `tools/limpieza_pendiente.py <base>` cuenta, por recurso, lo que el motor borraría al encender
  el interruptor. Es una cota superior: no consulta GitHub, así que no ve un PR ajeno que use una
  rama `encargo/*` como base. El motor sí lo comprueba antes de borrar, y la herramienta lo dice
  en su salida. Lee el disco, y las ramas y PR los saca del plegado, sin llamar a GitHub. Abre
  el registro solo para leer, sin `Registro()`, porque `Registro()` crea tablas: reutiliza
  `tools/plegado.leer_encargos`, que ya abre con `mode=ro`. Solo imprime conteos y tamaños, nunca nombres ni ids. No borra nada.

## Qué corre con el interruptor

| Qué | `ENTREGA_V4` apagado | Encendido |
|---|---|---|
| `pr_observado` de la fase activa y de las no activas | Corre | Corre |
| Celdas de decisión en encargos que no son finales, incluidos `fase_cancelada` y `pr_otra_base` | Corren, sin borrar nada | Corren y limpian |
| Observar encargos `terminado` de nivel `pr` | No | Sí |
| `_limpiar_encargo_cerrado` de hoy (al cerrar: cierra los procesos del encargo, borra `runtime/<id>`, `intento-*` y `actual`) | Sigue igual | Lo reemplaza `limpieza.py`, que conserva cerrar los procesos y borrar `actual` |
| Borrar credenciales al incorporar cada turno | No: se borran al cerrar, como hoy | Sí |
| Limpieza nueva, `limpieza` y `limpio` | No | Sí |
| Palabra de `reporte` | `reporte(e, limpieza_activa=False)`. En un cerrado sin `limpio` dice "falta limpiar", con la causa "la limpieza automática no está encendida" | `reporte(e, limpieza_activa=True)` |
| Aviso de cierre | Sale al cerrar, como hoy, con la palabra de `reporte` | Sale con `limpio`, con `agotado` o con "listo para tu merge" |
| Aviso de `detenido` y aviso de cambio de palabra | Corren | Corren |

## Palabras del reporte

`reporte(e, limpieza_activa: bool) -> Reporte | None` es una función pura en
`motor/encargos/reporte.py`: solo lee el estado plegado y el valor del interruptor. `Reporte` tiene `palabra`, `causa` y `fases` (cada fase con su palabra y su
causa). Es `None` si el encargo no es final ni `detenido`. Una fase cuenta como mergeada si `prs`
la tiene `Mergeado` en `base_pedida` (o en su `aceptada_en`, o en cadena, o en cualquier base si
el encargo es `terminado`), o si tiene
`integrado_sha` y `prs` no tiene entrada para esa fase, que cubre los registros viejos. Cuenta como desplegada si
tiene `desplegado_recibo`. Gana la primera regla que se cumple:

1. `detenido`: dice cuál de las causas y que se reanuda con `continuar`.
2. `terminado` de nivel `pr` con algún PR `Abierto` de una fase que no está en `canceladas`:
   "listo para tu merge".
3. Algún recurso con su última `limpieza` en `fallo` o `agotado`, o falta `limpio`: "falta
   limpiar", y nombra lo que queda. Un plazo que venció y sigue en reintentos también cuenta.
4. `cancelado`: "cancelado".
5. Alguna fase en `canceladas`, bloqueada o sin trabajar: "entrega parcial". Nombra cada fase y
   su causa.
6. Por el nivel efectivo, mirando todas las fases que no están en `canceladas`:
   - "entregado": cada fase mergeada en `base_pedida` y desplegada.
   - "integrado": cada fase mergeada, sin despliegue. La causa dice por qué no se desplegó, o en
     qué base la aceptó David. En un encargo final, una fase `Mergeado` en otra base también
     cuenta como integrada, y la causa dice en qué base quedó.
   - "respondido": nivel `terminado`, el resultado llegó y no hay PR.
   - "sin cambios": no hubo cambio que entregar.

Dónde sale, sin ráfagas al desplegar ni al encender:
- `registro.py` agrega el corte `palabras-v4` en `cortes_avisos`, igual que `cierres-cancelados-v1`:
  se fija la primera vez que la versión nueva abre la base. Los avisos de abajo comparan con el
  plegado al corte (`registro.fold(encargo, hasta=corte)`), como hoy los cancelados.
- El aviso de cierre (`motor/integraciones/directores.py`) usa `reporte`, cuándo según la tabla del
  interruptor. Solo cambia para los encargos que no eran finales al corte. Los que ya lo eran
  siguen como hoy.
- Si la palabra de un encargo cambia después de su aviso de cierre de la versión nueva (David
  mergea, o una limpieza llega a `agotado`), sale un aviso más con la palabra nueva. Un `fallo`
  que todavía se reintenta no avisa: "falta limpiar" se avisa solo con `agotado`. Uno por
  palabra, con la palabra en el id del aviso. Un encargo que no recibió aviso de cierre de la
  versión nueva no recibe estos avisos.
- Hay un aviso nuevo al quedar `detenido`, por cada detención cuyo evento es posterior al corte.
  Hoy no sale ninguno. La detención que ya existía al corte no se avisa, porque avisarla sería la
  ráfaga. Si ese encargo se continúa y se vuelve a detener, esa detención nueva sí avisa.
- `motor ver` agrega el campo `reporte` en `cli.py`, sin tocar `dominio.vista`.
- `skill/motor/SKILL.md` cambia la línea que dice que `terminado` significa nivel alcanzado igual
  al autorizado, y lista las nueve palabras.

## Cola de unidades

La evidencia de cada unidad incluye siempre la regla 2 del paso 3 y la regla 3 de este documento.

**4.0 El reductor aprende los cinco kinds, sin escritores.** Los tipos, los campos nuevos,
`PorOtraBase` con su proyección legada, `hora_final` y `limpio` en `plegar_evento`, las cinco
filas de `TABLA` con `validar` y `aplicar`, `CLAVES_NUEVAS` en `tools/plegado.py` y su uso en
`tools/corpus_plegado.py`.
- **Evidencia.** `verificar` en "estado igual en K de K" y `comparar --estricto` sin diferencias,
  con las claves de `CLAVES_NUEVAS`. Una prueba de que una clave nueva que no está en la lista
  sigue fallando con `--estricto`. Una prueba por kind: el valor plegado y el rechazo al escribir.
  "Aplicar nunca lanza" de los cinco con cargas vacías, tipos malos, `Infinity`, `NaN` y fuera de
  dominio. Una prueba de que una reapertura vacía `hora_final` y `limpio`. Una prueba de que
  `continuar` después de `PorOtraBase` deja `aceptada_en`. `git grep` muestra que ningún módulo
  escribe los cinco kinds todavía.

**4.1 Puerto y observación.**
- `estado_de` en `entrega/tipos.py`. En `motor/github.py`: `cerrar_pr`, `borrar_rama`, `prs_con_base` y `rama_existe`, con
  sus versiones en el doble de pruebas. El doble representa PR cerrados, PR mergeados en otra
  base, PR apilados y ramas borradas.
- El escritor de `pr_observado` en la fase activa y en las no activas, y después del merge del
  motor. El tercer caso (terminados de nivel `pr`) entra en la 4.2, detrás del interruptor.
- **Evidencia.** Pruebas de `estado_de` con la salida JSON de `gh` de cada estado. Una prueba de
  que `pr_observado` no se repite si el estado no cambia.

**4.2 Decisión por tabla.** `decision.py`, `ejecutar.py` y la prueba de 14 filas. El paso de
entrega de `ciclo.py` llama a `decidir` y a `ejecutar`. Las celdas nuevas escriben
`fase_cancelada` y `pr_otra_base`. `politica.ENTREGA_V4` y el tercer caso de observación.
- **Evidencia.** La prueba de tabla. Una prueba de punta a punta por cada una de las seis celdas
  que cambian, roja en la base, más una de las fases no activas con `Mergeado` en otra base y con
  `Cerrado`. Una prueba de PR apilados: el PR de la segunda fase reapuntado a `main` sigue
  `Abierto` y no se detiene. `wc -l` muestra que `ciclo.py` baja.

**4.3 Limpieza.** `politica.LIMPIEZA`, `LIMPIEZA_POR_PASADA`, `limpieza.py`, los escritores de
`limpieza` y `limpio`, los reintentos desde el registro, el fin de las carpetas recreadas, el
orden del puntero `.actual`, `comprobar_recibo` por fase y `tools/limpieza_pendiente.py`.
- **Evidencia.**
  - Por cada fila de la tabla de limpieza, una prueba con el interruptor encendido: vence, se
    borra, se comprueba y se escribe el evento. Otra con la condición de "nunca se borra si".
  - Una prueba con el interruptor apagado en la que `_limpiar_encargo_cerrado` sigue igual y no se
    borra nada más.
  - Una prueba de que un segundo turno del mismo encargo funciona después de borrar las
    credenciales del primero.
  - Una prueba de que `despliegue.observacion` no da "incierto" después de limpiar, con y sin el
    argumento `fase` (`despliegue.py:497-523`).
  - Una prueba de los tres fallos hasta `agotado`, que sobrevive a reiniciar el proceso.
  - Una prueba de que un barrido no recrea las carpetas borradas.

**4.4 Palabras del reporte.** `reporte.py`, el aviso de cierre, el aviso de `detenido`, el aviso
de cambio de palabra, `motor ver` y `skill/motor/SKILL.md`.
- **Evidencia.** Una prueba de tabla con una fila por palabra y una por cada empate de la lista de
  precedencia, con el interruptor apagado y encendido. Una prueba de que, al abrir una base con
  encargos ya detenidos y ya cerrados, no sale ningún aviso nuevo. Una prueba de que un registro viejo (sin
  `pr_observado`, con `integrado_sha` y `desplegado_recibo`) da "falta limpiar" y no "integrado".
  En `## Tradeoffs`, la lista de pruebas viejas que cambian de texto y por qué.

**4.5 Lecturas crudas de la entrega e invariantes.**
- `entrega_devuelta` ya tiene fila, pero `ciclo.py` lee del registro crudo su `version` y su
  `generacion` (`_entrega_devuelta_pendiente` y `_entrega_devuelta_de_la_version`). El reductor
  los guarda en un campo nuevo, y esas dos funciones leen el plegado. La hora para medir la espera
  de DeepSeek (cerca de la línea 9204 de `ciclo.py`) también sale del plegado.
- `tools/plegado.py comprobar` agrega las invariantes `completo_implica_preparadas` y
  `limpio_solo_en_final`. `cancelacion_definitiva` ya existe.
- Una prueba genera unas 2,000 secuencias de eventos con `random.Random(<semilla fija>)`, solo con
  la biblioteca estándar, y comprueba lo que pide la comprobación permanente 2: aplicar lo
  guardado nunca lanza y el mismo registro da el mismo estado. Al escribir, un evento se acepta o
  lanza una excepción, que la prueba anota por tipo sin exigir uno: hoy hay cargas que dan
  `KeyError` en vez de `TransicionIlegal`, y ordenarlas no es de este paso.
- **Evidencia.** `comprobar` sobre el corpus sin violaciones en las dos invariantes nuevas.
  `datos_dependen_del_estado` ya falla en 127e241 y no es de este paso. La prueba de secuencias, y una
  mutación sembrada que la pone en rojo.

## Lo que hace el revisor, no tú

- Desplegar cada tramo, con la compuerta y el canario.
- Agregar `--prune` al `fetch` del clon del servicio en `ops/`.
- Activar en GitHub el borrado de la rama al mergear en los repos que usa el motor (decisión 2).
- Correr `tools/limpieza_pendiente.py` en las tres máquinas, enseñarle los conteos a David y
  encender `ENTREGA_V4` con su sí.
- Cambiar en el canario la fila `limpio`, que hoy espera fallar (`ops/canario_encargos.esperado.json`).
- Actualizar `docs/modelo-centro-encargos.md`.

## Archivos

| Puede tocar | No toca |
|---|---|
| `motor/encargos/entrega/` (nuevo), `motor/encargos/reporte.py` (nuevo) | `ops/` completo, `.github/workflows/` |
| `motor/encargos/situacion.py`, `encargo.py`, `reductor/`, `detencion.py`, `politica.py` | `motor/encargos/lote.py`, el resto de `registro.py` |
| `motor/encargos/ciclo.py` y `dominio.py` sin que crezcan | `motor/flota/` |
| `motor/github.py`, `motor/integraciones/directores.py` | `docs/modelo-centro-encargos.md` (lo actualiza el revisor) |
| `motor/encargos/cierre.py`, `despliegue.py`, `arranque.py`, `necesidades.py`, `cli.py` | |
| `motor/encargos/registro.py`, solo el corte `palabras-v4` | |
| `skill/motor/SKILL.md`, `AGENTS.md` y `CLAUDE.md` (solo las tablas generadas) | |
| `tools/plegado.py`, `tools/corpus_plegado.py`, `tools/limpieza_pendiente.py` (nuevo), `tests/` | |

## Cuando algo se atora

| Situación | Acción |
|---|---|
| `kinds --base origin/main` en rojo en la 4.1 o después | Es esperado mientras la 4.0 no esté en `main`. Dilo en `## Verification` y sigue. |
| `tamano` rechaza el PR | Saca código de `ciclo.py` o `dominio.py`, o borra lo que reemplazaste. Nunca subas el límite. |
| Una clave existente cambia de valor en el corpus | No sigas. Escribe en el PR la clave, los encargos de prueba y la causa, y espera la ronda. |
| Una prueba vieja falla | Si la conducta cambió por una celda o una palabra de este documento, actualízala y nómbrala en `## Tradeoffs`. Si no, tu cambio está mal. |
| No sabes qué hace GitHub con un PR apilado cuando se borra su base | No lo averigües en GitHub. La regla es no borrar una rama que es base de un PR abierto. |
| Un recurso parece compartido con otro encargo o con otra fase | No lo borres. Escribe en el PR cuál y por qué, y déjalo conservado. |
| `generar` dice que no pudo sembrar un kind | Faltan secuencias legales de ese kind escritas con `Registro` en las pruebas (regla 6). |
| DeepSeek insiste en un hallazgo que crees falso | Contesta con la evidencia y deja el PR abierto. Solo David descarta. |
| El revisor publica otra ronda en #499–#502 | Haz merge de la rama de #502 en tu primera rama y luego en cada siguiente. |
| `main` avanzó y hay conflicto | `git merge origin/main`, resuelve, corre las comprobaciones y haz push normal. |
| Te quedaste sin contexto o te reiniciaste | Este documento, tus ramas y el último comentario del revisor en cada PR tienen todo el estado. |

## Decisiones ya tomadas

Estas las tomó el revisor. David puede cambiarlas.
- **`continuar` después de "mergeado en otra base"** quiere decir que David acepta esa base. La
  fase sigue como si se hubiera mergeado en la base pedida, incluido el despliegue si se pidió, y
  no se abre otro PR. El reporte dice en qué base quedó.
- **Un encargo `terminado` no se detiene** si David mergea su PR en otra base. Se anota y el
  reporte dice en qué base quedó. Un `cancelado` ya no se observa.
- **`pedido.json` se conserva junto al recibo.** El modelo dice "se conserva el recibo", pero el
  canario lee `pedido.json` para saber de qué encargo es cada carpeta. Pesa poco.
- **Interruptor apagado al desplegar.** Al encenderlo se limpia lo viejo que ya venció. Por eso el
  revisor enseña antes los conteos a David. Los encargos que cierren entre el despliegue y el
  encendido pueden dar un aviso más al encenderlo; es una ráfaga acotada y se acepta.
- **Una fase cancelada arrastra a las que dependen de ella**, y el motor cierra sus PR.
- **El merge de David cierra la fase** aunque la revisión no haya aprobado.
- **Tope de limpieza:** 3 intentos, 10 minutos entre intentos y 20 borrados por pasada.
- **Fuera del paso 4.** Las causas "tope de una espera" y "falta de avance" son del paso 5. Los
  demás kinds que se escriben y se ignoran son del paso 6. Los huecos que el paso 3 conservó
  siguen igual: si te topas con uno, nómbralo en el PR.

## Cierre

El paso 4 termina cuando 4.0 a 4.5 están mergeadas y desplegadas, `ENTREGA_V4` está encendido y
el canario pasa la fila `limpio` en las tres máquinas. Al terminar cada PR, tu último comentario
es `LISTO <sha>` o `ATORADO <una línea>`.
