# Poteto-mode verificable en los encargos

## Problema

David quiere que los responsables de los encargos sigan poteto-mode de verdad. Hoy el motor comprueba que existe una skill, copia un paquete y agrega `Lee poteto-mode en <ruta>` al prompt (`motor/encargos/ciclo.py:6541-6568`). No revisa lo que el responsable entrega. Los últimos 10 PRs de `goncloud-motor` y los últimos 10 de `goncloud-Orbit` no contienen `## Principios`. La sección tampoco aparece en los últimos 100 PRs consultados de cada repo.

La revisión debe usar entregables durables. Al cerrar, el motor borra `runtime/<encargo>` y `ejecucion/<encargo>/intento-*`, incluidos prompts, logs y transcripciones (`motor/encargos/ciclo.py:683-739`). Solo el registro y la última copia del espacio sobreviven.

La solución tiene cuatro reglas:

1. El responsable escribe un sobre JSON con una nota de arranque y los principios aplicados.
2. El motor valida identidad, vocabulario, decisiones y anclas contra la copia de `SKILL.md` usada en ese intento.
3. El reducer conserva un documento válido o una omisión explícita. El PR se deriva de ese documento, nunca del `## Principios` heredado.
4. La revisión temprana solo avisa. La final devuelve la entrega. La tercera falla de una clase permite entregar con un pendiente visible.

El motor podrá comprobar que hay una declaración vigente y revisable. No podrá demostrar que la nota se escribió antes del trabajo, que el agente leyó una ficha ni que aplicó bien un principio. El revisor independiente conserva el juicio semántico.

Este documento es solo diseño. No implementa el módulo ni cambia el comportamiento del motor.

## Cómo funciona hoy

### Panorama

`_lanzar_uno_en_turno` crea una generación, un token y un runtime. El wrapper abre el intento, lanza un CLI *one-shot* y publica `outcome.json`. Un barrido posterior incorpora el espacio compartido, registra el producto y decide corrección, revisión o entrega. `registro.sqlite` es append-only y `reducir` reconstruye `Encargo`.

Todos los adaptadores reciben el prompt con `stdin=DEVNULL`. GLM usa OpenCode. Kimi, Muse, Codex, Grok y Claude también terminan un proceso por turno. Sus opciones de reanudación abren otro proceso con una sesión anterior (`motor/encargos/agentes.py:421-635`).

| Agente | Reanudación que construye el adaptador |
|---|---|
| GLM mediante OpenCode | `opencode run --session <sesion>` (`agentes.py:582-603`). |
| Kimi | `kimi --session <sesion>`. En Windows delega en OpenCode (`agentes.py:555-580`). |
| Muse | `muse exec --session-id <sesion>` (`agentes.py:527-553`). |
| Codex | `codex exec resume <sesion>` (`agentes.py:489-521`). |
| Grok | `grok --resume <sesion>` (`agentes.py:453-487`). |
| Claude | `claude --resume <sesion>` (`agentes.py:605-635`). |

Ninguna opción acepta más entrada en el proceso que ya terminó o que se pretende interrumpir. El código tampoco demuestra qué conserva cada proveedor después de `SIGKILL`.

### Conceptos que condicionan el diseño

- **Intento.** Cada lanzamiento crea otro token (`motor/encargos/ciclo.py:6329-6346`). La generación cuenta responsables y revisores. Todas las vueltas reutilizan el mismo espacio.
- **Producto.** `.interno` queda fuera de la firma y del commit (`motor/encargos/comprobacion.py:142-164`, `motor/encargos/ciclo.py:3839-3844`). Cambiar una declaración no cambia por sí solo el SHA del producto.
- **Instrumentos.** `_VERIFICABLES` y `_EVIDENCIA_DE` están en `motor/encargos/ciclo.py:1695-1714`. La segunda tabla contiene frases para `impedimento_resuelto`, no evidencia del agente. `_verificar_impedimento` hace la revalidación real.
- **Escalera de instrumentos.** `_impedimento_de_instrumento` cuenta fallos por clase en todo el encargo (`motor/encargos/ciclo.py:1911-1970`). No es la escalera `investigar`, `diagnosticar`, `relevar` de los bloqueantes de revisión.
- **PR.** `_cuerpo_pr` lee `.interno/cuerpo-pr.md` y agrega pendientes y notas (`motor/encargos/ciclo.py:3994-4026`). La actualización se deduplica por fase y SHA, sin hash del cuerpo (`motor/encargos/ciclo.py:4029-4085`).
- **Salida forzada.** `_entregar_con_pendientes` publica directamente y puede terminar sin otra revisión (`motor/encargos/ciclo.py:5338-5462`). Con pendientes y sin proceso, el barrido entra en esa salida antes de lanzar otro agente (`motor/encargos/ciclo.py:966-977`).

### Restricciones que no se deben esconder

`parar` no pausa. En POSIX, `SIGSTOP` solo congela el árbol antes de `SIGKILL`, incluido el wrapper (`motor/encargos/sesion.py:219-275`). La sesión portable se guarda al incorporar `outcome.json`, no continuamente. Matar antes puede perder el identificador y el trabajo conversacional posterior.

Si el agente termina antes del barrido, un `outcome.json` no incorporado basta para cosecharlo (`motor/encargos/ciclo.py:1094-1131`). Por eso la validación final no necesita observar antes el proceso.

Una lectura durante el intento puede ver un archivo a medio escribir. El candado `turno` ordena el registro, no las escrituras del agente. El aviso temprano nunca puede tratarse como un fallo confirmado.

La distribución también es una frontera. `paquetes/poteto/SKILL.md` tiene 18 líneas y no contiene `## Principles` ni fichas hermanas. `ops/ensayo_fases_3_4.py:495` lo copia a `base/politica/poteto`, una de las fuentes que acepta `copiar_poteto`. Esto demuestra que una fuente admitida puede carecer del catálogo. No se inspeccionó la política real del host. El preflight debe validar la fuente seleccionada, esté completa o no, y probar la siguiente en el orden actual. Un paquete incompleto no puede consumir correcciones del responsable.

## Uso desde la perspectiva del llamador

El módulo nuevo es `motor/poteto.py`. Cuatro funciones ocultan el sobre, el catálogo, la frescura, el parseo de Markdown y la composición del cuerpo. `ciclo.py` conserva procesos, eventos y la escalera. `dominio.py` conserva el documento derivado.

### 1. Preparar el responsable

`exigir_poteto` copia cada fuente a un destino temporal propio del intento y ejecuta `preflight` sobre esa copia, no sobre la fuente original. Si el catálogo o las fichas no llegan completos, descarta el destino temporal y prueba la siguiente fuente. El respaldo `~/.agents/skills` entra en el mismo recorrido. Solo una copia válida se renombra al destino final. Ninguna fuente rechazada se mezcla con la siguiente mediante `dirs_exist_ok=True`. Si ninguna sirve, `PotetoAusente` sigue la espera visible actual. No se lanza al responsable.

En `motor/encargos/ciclo.py:6547-6568`:

```python
intento = IntentoResponsable(
    encargo=enc.id,
    generacion=generacion,
    token=token,
    poteto_digest=poteto_digest,
)
contenido += "\n\n" + instrucciones_poteto(intento=intento, skill_md=skill)
preparar_administrado(
    cmd, espacio, directorio, enc, sesion, hora, runtime, entorno_gen,
    agente=agente_lanzado,
    modelo=modelo_lanzado,
    sesion_modo=sesion_modo,
    poteto_digest=poteto_digest,
    poteto_skill=skill,
)
```

El prompt contiene el sello y el esquema del sobre. No contiene el catálogo ni las fichas. `arranque.json` conserva la ruta de la skill y `intento_abierto` la propaga (`motor/encargos/arranque.py:544-557`).

### 2. Observar el arranque e incorporar el outcome

El aviso temprano se ejecuta cuando el barrido observa el primer cambio del producto con un responsable vivo (`motor/encargos/ciclo.py:1067-1093`). Solo revisa la nota. No revisa principios, porque esas decisiones pueden seguir cambiando.

```python
aviso = aviso_arranque(
    intento=intento_actual,
    skill_md=skill_del_intento,
    interno=interno_de(espacio),
)
_anotar_aviso_poteto(registro, enc, aviso, hora)
```

La validación final ocurre después de adoptar fases y calcular la versión, antes de `producto_listo` (`motor/encargos/ciclo.py:2888-2947`):

```python
def evaluar_actual() -> ResultadoPoteto:
    return evaluar_entrega(
        intento=intento_actual,
        skill_md=skill_del_intento,
        interno=interno,
        fase=enc.fase_activa,
        version=version.firma,
        manifiesto=version.manifiesto,
        cuerpo_pr=_leer_cuerpo_del_espacio(espacio),
        omitidas=_omisiones_poteto(registro, registro.fold(enc.id)),
    )

resultado = evaluar_actual()
if isinstance(resultado, CorreccionPoteto):
    siguiente = _aplicar_fallos_poteto(registro, enc, resultado, directorio, hora)
    if siguiente is None:
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    enc = registro.fold(enc.id)
    resultado = evaluar_actual()
if not isinstance(resultado, DocumentoPoteto):
    raise TransicionIlegal("la evaluacion posterior a omitir debe ser publicable")
_capturar_documento_poteto(registro, enc, resultado, hora)
```

`_omisiones_poteto` reconstruye `tuple[InstrumentoOmitido, ...]` desde los eventos. Cada omisión conserva clase, causa y número de fallos. El evaluador no consulta SQLite y no tiene que reconstruirlas desde nombres sueltos. Si `_aplicar_fallos_poteto` devuelve un `Encargo`, todas las fallas quedaron omitidas y el llamador vuelve a evaluar para construir el documento. Si devuelve `None`, la incorporación se detiene.

Después se registra `producto_listo` mediante `_anotar_producto_listo`. Su reducer exige que el documento tenga la misma `EntregaId`. El documento llega al revisor independiente. En `_intentar_cierre`, cuando todas las demás fases están `preparada`, `_revision_aprobada_vigente(enc)` es verdadera y `(enc.entrega.nivel == "terminado" or nivel_cumplido(enc))` también lo es, `_proyectar_pendientes_poteto` crea los pendientes por las clases omitidas. Si existe alguno, actualiza el PR mediante `gh` y `_actualizar_cuerpo_pr`, cuyo recibo incluye el hash del cuerpo, y retorna sin componer `cierre.json` en esa llamada. Si `gh` es `None` o no hay PR abierto, no actualiza nada y retorna igual sin componer. El siguiente barrido entrega por la vía forzada existente y, si no hay PR, deja los pendientes en la evidencia. `_intentar_cierre` recibe `gh=None` como parámetro opcional y lo recibe de `_cerrar_con_veredicto` y de los llamadores dentro de `_paso_entrega`.

Los cuatro emisores actuales pasan por esa misma función: `_incorporar_responsable`, `_reabrir_revision` (`ciclo.py:2131-2162`), `_incorporar_revisor` (`ciclo.py:3276-3291`) y la recepción por autoridad (`ciclo.py:7385-7400`). Esos otros tres solo la llaman al detectar una versión distinta. Si el documento no coincide, `_anotar_producto_listo` registra primero `_devolver(registro, enc, "investigar", "version sin documento poteto", "redeclarar .interno/poteto-entrega.json para la version vigente", hora, f"poteto-version-{version.firma}")`. Ese evento devuelve trabajo sin anotar `instrumento_fallo` ni consumir la escalera. Después devuelve `None`.

El revisor publica `incorporado` y retorna sin aplicar el juicio. La autoridad devuelve `{"rechazado": True, "motivo": "falta una evaluacion poteto vigente"}`. `_reabrir_revision` retorna el fold sin anotar la estrategia del revisor. Una repetición de la versión ya registrada conserva el documento sin llamar al emisor.

### 3. Publicar el mismo documento

`_cuerpo_pr(enc)` usa `enc.fase_actual.documento_poteto.cuerpo_base` solo si su `EntregaId` coincide con el producto vigente. Ya no lee el cuerpo del espacio. Agrega pendientes, notas y la línea de fase como hace hoy.

Antes de recortar, el compositor reserva el espacio de `## Pendientes abiertos al entregar`, `## Notas del motor` y la línea final. Recorta solo `cuerpo_base`. Después agrega esas secciones completas y calcula el hash del cuerpo final. Un cuerpo próximo al límite no puede borrar el pendiente que explica la entrega.

Las salidas normal y con pendientes usan ese mismo documento. `_actualizar_cuerpo_pr` compone una vez y guarda `sha256(cuerpo)` en el evento y en la clave idempotente. Un cuerpo nuevo llega a GitHub aunque el SHA de Git no cambie. Un reintento con el mismo cuerpo no repite la escritura.

## Forma de los datos

### Identidad y frescura

```python
@dataclass(frozen=True)
class IntentoResponsable:
    encargo: str
    generacion: int
    token: str
    poteto_digest: str

    @property
    def sello(self) -> str:
        """SHA-256 de encargo, generacion y token. El token no se publica."""
        raise NotImplementedError


@dataclass(frozen=True)
class EntregaId:
    intento: IntentoResponsable
    fase: str
    version: str
```

`IntentoResponsable` identifica al escritor. `EntregaId` identifica lo que entregó para una fase y una versión. La fase y la versión las fija el motor, no el sobre. Esto evita que una propuesta nueva de `fases.json` deje una identidad escrita por el agente que ya no coincide.

El sobre lleva `sello`, `generacion` y `poteto_digest`. No lleva el token crudo. El token ya aparece en rutas internas, así que el sello no es una defensa contra un agente hostil. Es una forma de no propagarlo al PR ni a más artefactos. La frescura sigue dependiendo de que cada intento tenga otro token.

Una corrección puede conservar todos los textos vigentes. Debe redeclararlos con el sello y el digest del nuevo intento. El motor no compara prosa entre intentos. Cambiar solo la identidad no demuestra razonamiento nuevo y no se presenta como tal prueba.

### Resultados sin estados ambiguos

```python
ClasePoteto = Literal["nota-arranque", "principios"]


@dataclass(frozen=True)
class NotaArranque:
    objetivo: str
    problema: str
    cambio_minimo: str


@dataclass(frozen=True)
class DecisionPrincipio:
    id: str
    decision: str
    ancla: str
    ficha_md: str


@dataclass(frozen=True)
class InstrumentoOmitido:
    clase: ClasePoteto
    causa: str
    fallos: int


@dataclass(frozen=True)
class DocumentoPoteto:
    entrega: EntregaId
    nota: NotaArranque | InstrumentoOmitido
    principios: tuple[DecisionPrincipio, ...] | InstrumentoOmitido
    cuerpo_base: str
    cuerpo_sha256: str


@dataclass(frozen=True)
class FalloPoteto:
    clase: ClasePoteto
    causa: str
    condicion: str
    siguiente: str
    artefacto: str


@dataclass(frozen=True)
class CorreccionPoteto:
    entrega: EntregaId
    fallos: tuple[FalloPoteto, ...]


ResultadoPoteto = DocumentoPoteto | CorreccionPoteto


@dataclass(frozen=True)
class AvisoArranque:
    intento: IntentoResponsable
    causa: str
    observacion_sha256: str
```

`CorreccionPoteto.fallos` contiene al menos un elemento. `DocumentoPoteto` solo existe cuando cada clase tiene un valor válido o una omisión ya registrada. Una tupla válida de principios no está vacía y no repite ids. Estas reglas pertenecen a los constructores.

`InstrumentoOmitido` evita usar `None` para tres hechos distintos. Una nota ausente todavía no es una omisión. Una lista vacía no equivale a una clase omitida. Un fallo de parseo no es un documento publicable.

`DecisionPrincipio.decision` explica qué elección cambió por aplicar el principio. `ancla` señala dónde se puede comprobar ese efecto. Debe ser una ruta del manifiesto del producto, `.interno/evidencia.txt` o `.interno/comprobaciones.json`. Las dos rutas internas admitidas son una sola constante del módulo. El revisor juzga si la decisión y el ancla realmente explican el cambio.

`ficha_md` lo agrega el motor después de leer la ficha citada. No forma parte del sobre. El documento conserva solo las fichas citadas para que el revisor pueda leer la misma versión después de borrar el runtime. El PR recibe id, decisión y ancla, no `ficha_md`.

`cuerpo_base` contiene el resto de `cuerpo-pr.md`, sin ningún `## Principios` escrito por el agente. Si la clase es válida, agrega una sola sección generada desde el documento. Si quedó omitida, no agrega esa sección. `cuerpo_sha256` identifica ese contenido antes de añadir pendientes y notas.

### Persistencia

`Fase` gana `documento_poteto: DocumentoPoteto | None`. `Encargo` conserva la ruta `poteto_skill` del intento actual. No hay un campo `poteto_contrato` ni un campo `aviso_poteto`.

El evento `documento_poteto` contiene el documento completo. Su clave incluye encargo, fase, generación, sello y versión. La primera escritura gana. Una reincorporación idéntica devuelve `igual`. Una lectura posterior no reemplaza evidencia aceptada.

El reducer de `producto_listo` exige un documento coincidente cuando el intento tiene `poteto_skill`. Los eventos históricos sin esa ruta se siguen leyendo. Antes de activar el cambio hay que drenar los encargos en vuelo. Los históricos no obtienen una vía de publicación sin documento. Tampoco existe un cuerpo mínimo alternativo en la salida forzada.

## Contrato del sobre

El responsable escribe `.interno/poteto-entrega.json` en cada intento:

```json
{
  "esquema": 1,
  "intento": {
    "sello": "sha256-del-intento-responsable",
    "generacion": 8,
    "poteto_digest": "sha256-de-la-skill-copiada"
  },
  "nota_arranque": {
    "objetivo": "Qué quiere conseguir el propietario.",
    "problema": "Qué impide hoy conseguirlo.",
    "cambio_minimo": "El menor cambio que espera satisfacer el pedido."
  },
  "principios": [
    {
      "id": "principle-model-the-domain",
      "decision": "El documento distingue una entrega válida de una omisión.",
      "ancla": "motor/poteto.py"
    }
  ]
}
```

Las tres partes de la nota son `objetivo`, `problema` y `cambio_minimo`. Salen de la primera regla de poteto-mode. Las tres son textos no vacíos.

La lista de principios no puede estar vacía. Cada entrada tiene un id del catálogo, una decisión concreta y un ancla entregada. El agente no escribe `ficha_md`, fase, versión ni hashes del cuerpo.

El agente tampoco escribe `## Principios` en `cuerpo-pr.md`. El compositor elimina cualquier sección con ese nombre, incluidas las heredadas, y genera una sola desde el documento válido. Los encabezados dentro de bloques de código cercados no se interpretan como secciones.

## Firmas y propiedad de los módulos

### Interfaz pública de `motor/poteto.py`

```python
def preflight(skill_md: Path) -> str:
    """Devuelve '' o la causa de instalación incompleta."""
    raise NotImplementedError


def instrucciones_poteto(
    *, intento: IntentoResponsable, skill_md: Path
) -> str:
    """Genera el contrato y la regla de lectura bajo demanda."""
    raise NotImplementedError


def aviso_arranque(
    *, intento: IntentoResponsable, skill_md: Path, interno: Path
) -> AvisoArranque | None:
    """Lee una escritura posiblemente incompleta sin convertirla en fallo."""
    raise NotImplementedError


def evaluar_entrega(
    *,
    intento: IntentoResponsable,
    skill_md: Path,
    interno: Path,
    fase: str,
    version: str,
    manifiesto: tuple[tuple[str, str], ...],
    cuerpo_pr: str | None,
    omitidas: tuple[InstrumentoOmitido, ...] = (),
) -> ResultadoPoteto:
    """Valida el sobre y compone el documento asociado al producto."""
    raise NotImplementedError
```

`preflight` exige una sección `## Principles` legible y una ficha hermana para cada principio del catálogo. `instrucciones_poteto` y los parsers comparten el mismo contrato. `aviso_arranque` solo valida identidad y nota. `evaluar_entrega` valida todo y compone el cuerpo una vez. Las omisiones llegan completas, con causa y conteo.

`skill_md` siempre señala la copia del intento. Las funciones comprueban que su SHA-256 coincide con `intento.poteto_digest`. Ninguna recibe rutas de transcripciones, argv, nombres de CLIs ni formatos de proveedores.

### Validación privada de principios

```python
def _validar_principios(
    crudo: object,
    *,
    intento: IntentoResponsable,
    skill_md: Path,
    manifiesto: tuple[tuple[str, str], ...],
) -> tuple[DecisionPrincipio, ...]:
    """Valida el sobre contra la copia autoritativa de SKILL.md."""
    raise NotImplementedError
```

La función verifica el digest antes de leer el catálogo. Lee solo `## Principles`. Deriva cada id del título en kebab case, salvo que la entrada declare uno explícito. La excepción `principle-sequence-verifiable-units` ya está escrita en la fuente y no se duplica en código.

También rechaza ids desconocidos o duplicados, lista vacía, decisiones vacías y anclas ausentes del manifiesto o de las dos rutas internas admitidas. Comprueba que cada ficha citada exista y congela su texto en `ficha_md`. No califica la prosa ni afirma que el agente haya leído la ficha.

Los helpers privados restantes son `_validar_nota`, `_catalogo_de`, `_leer_sobre`, `_sello_de_intento` y `_componer_cuerpo_base`. No forman parte de `__all__`.

### Adaptadores del ciclo

```python
def _aplicar_fallos_poteto(
    registro: Registro,
    enc: Encargo,
    resultado: CorreccionPoteto,
    directorio: Path,
    hora: str,
) -> Encargo | None:
    """None detiene; un Encargo pide reevaluar con las omisiones."""
    raise NotImplementedError


def _fallo_ya_registrado(
    registro: Registro, entrega: EntregaId, clase: ClasePoteto
) -> bool:
    """Deduplica una reincorporación antes de consultar la escalera."""
    raise NotImplementedError


def _omisiones_poteto(
    registro: Registro, enc: Encargo
) -> tuple[InstrumentoOmitido, ...]:
    """Reconstruye clase, causa y conteo desde los eventos."""
    raise NotImplementedError


def _capturar_documento_poteto(
    registro: Registro, enc: Encargo, documento: DocumentoPoteto, hora: str
) -> Encargo:
    raise NotImplementedError


def _anotar_producto_listo(
    registro: Registro, enc: Encargo, version: VersionProducto, hora: str
) -> Encargo | None:
    """Devuelve al responsable antes de None si la version no coincide."""
    raise NotImplementedError


def _proyectar_pendientes_poteto(
    registro: Registro, enc: Encargo, documento: DocumentoPoteto, hora: str
) -> Encargo:
    raise NotImplementedError
```

`_aplicar_fallos_poteto` devuelve `None` cuando toca corrección o impedimento. Devuelve un `Encargo` cuando todas las fallas quedaron omitidas y toca reevaluar. `_fallo_ya_registrado` evita que una retransmisión sume otra falla. `_omisiones_poteto` conserva las causas y los conteos. `_anotar_producto_listo` es el único camino para los cuatro productores actuales. Antes de devolver `None` por una versión distinta, registra `_devolver` con estrategia `investigar` y el trabajo de redeclarar el sobre. No anota `instrumento_fallo`. Los otros llamadores se detienen como se indicó en el uso.

### Mapa de módulos

| Ruta | Responsabilidad |
|---|---|
| `motor/poteto.py` | Sobre, tipos, prompt, catálogo, frescura, anclas, evaluación y composición del cuerpo. No contiene SQLite, procesos ni CLIs. |
| `motor/flota/runtime.py:147-190` | Copia cada fuente a un destino temporal, valida esa copia y solo publica una distribución completa. No mezcla fuentes. |
| `motor/encargos/ciclo.py:6313-6655` | Crea la identidad, agrega instrucciones y pasa `poteto_skill` a `preparar_administrado`. |
| `motor/encargos/sesion.py:129-180` | Conserva la ruta de la skill en `arranque.json`. |
| `motor/encargos/arranque.py:544-557` | Propaga ruta y digest en `intento_abierto`. |
| `motor/encargos/ciclo.py:1067-1093` | Emite el aviso temprano al observar cambios del producto. |
| `motor/encargos/resumen.py:307-336` | Muestra el aviso tentativo y lo retira con una observación válida o el resultado final. |
| `motor/encargos/ciclo.py:2888-2947` | Evalúa, aplica fallos, reevalúa omisiones y captura el documento antes de `producto_listo`. |
| `motor/encargos/ciclo.py:2131-2162` | `_reabrir_revision` usa `_anotar_producto_listo`. Si devuelve `None`, no anota la estrategia del revisor. |
| `motor/encargos/ciclo.py:3276-3291` | Si el emisor devuelve `None`, el revisor publica `incorporado` y retorna sin aplicar el juicio. |
| `motor/encargos/ciclo.py:7385-7400` | Si el emisor devuelve `None`, la autoridad rechaza el resultado hasta que el responsable capture el documento. |
| `motor/encargos/dominio.py:222-318, 440-541` | Añade `Fase.documento_poteto` y `Encargo.poteto_skill`. |
| `motor/encargos/dominio.py:1301-1430` | Limpia el documento al abrir otro responsable y lo conserva durante el revisor. Exige identidad coincidente en `producto_listo`. |
| `motor/encargos/ciclo.py:1405-1435` | Extiende `instrumento_fallo` con identidad, fase y skill para deduplicar y conservar omisiones. |
| `motor/encargos/ciclo.py:1695-1728` | Registra las clases por coherencia con `_es_verificable`. La validación real ocurre en cada incorporación, no en un impedimento posterior. |
| `motor/encargos/ciclo.py:1911-1970` | Conserva la escalera y `nota_pr`. No proyecta pendientes poteto en este punto. |
| `motor/encargos/ciclo.py:2258-2259` | Antes de `componer`, exige las demás fases `preparada`, `_revision_aprobada_vigente(enc)` y `(enc.entrega.nivel == "terminado" or nivel_cumplido(enc))`. Proyecta pendientes y, si hay alguno, actualiza el PR con `gh` y retorna sin componer `cierre.json`. `_cerrar_con_veredicto` (`:4484`) y `_paso_entrega` (`:5863`, `:5902`, `:5936`, `:5942`) pasan `gh` a `_intentar_cierre`. |
| `motor/encargos/ciclo.py:3994-4085` | Usa el cuerpo capturado, reserva las secciones finales al recortar y agrega el hash del cuerpo final a la deduplicación. |
| `motor/encargos/ciclo.py:5338-5462` | Publica solo un documento coincidente. Si falta, no publica un cuerpo alternativo y deja la causa en la evidencia del cierre forzado. |
| `motor/encargos/ciclo.py:6380-6404` | Entrega al revisor el documento y las fichas citadas. |
| `motor/encargos/comprobacion.py:498-569` | Mantiene las instrucciones generales. El esquema poteto no se repite aquí. |
| `motor/encargos/ciclo.py:683-739` | Borra runtime y transcripciones después de que el documento ya está en el registro. |

`motor/pstack.py` no cambia. Su lectura de huellas pertenece a otro subsistema.

### Registro de clases

`_POTETO_EVIDENCIA` es la única colección de clases nuevas. `_EVIDENCIA_DE` la incorpora y `_VERIFICABLES` se deriva de las claves:

```python
_POTETO_EVIDENCIA = {
    "nota-arranque": (
        ".interno/poteto-entrega.json redeclara objetivo, problema y cambio "
        "minimo para el intento esperado"
    ),
    "principios": (
        ".interno/poteto-entrega.json redeclara principios del SKILL.md del "
        "intento con decision y ancla"
    ),
}
_EVIDENCIA_DE = {
    "entorno": (
        "la sonda del entorno funciona: temporal del CLI, espacio y fuentes autorizadas"
    ),
    "comprobaciones.json": ".interno/comprobaciones.json vuelve a validar",
    "fases.json": (
        ".interno/fases.json valida y su propuesta se puede adoptar "
        "sobre las fases vigentes"
    ),
    "pedido-efecto.json": ".interno/pedido-efecto.json vuelve a validar",
    "disputa.json": ".interno/disputa.json vuelve a validar",
    **_POTETO_EVIDENCIA,
}
_VERIFICABLES = tuple(_EVIDENCIA_DE)
```

Las clases entran en las tablas por coherencia con `_es_verificable`. Como `_instrumento_se_puede_omitir` devuelve siempre `True`, las primeras dos fallas vuelven al responsable sin crear un impedimento persistente. Por eso no se añade una rama nueva en `_verificar_impedimento`. La validación real es `evaluar_entrega` en cada incorporación. Las frases de `_EVIDENCIA_DE` quedan disponibles si una política futura crea un impedimento de estas clases. No hay otra lista de principios que mantener.

## Flujos

### Revisión temprana que solo avisa

1. El lanzamiento crea la identidad y entrega el contrato.
2. El primer cambio de producto observado con un responsable vivo dispara `aviso_arranque`.
3. Una nota ausente, incompleta, parcial o con sello viejo produce un `procedimiento` informativo. El evento incluye `poteto_clase`, `estado`, `intento` y `observacion_sha256` para que el resumen no tenga que interpretar el texto. La clave incluye intento y hash de la observación para no repetir el aviso en cada barrido.
4. Una observación válida posterior registra la retirada. El resultado final sustituye cualquier aviso anterior.
5. `resumen._alertas` muestra el aviso como tentativo. El texto de `procedimiento` también entra en el material del siguiente intento.

El aviso no crea `instrumento_fallo`, impedimento, estrategia, `nota_pr` ni `pendiente_entrega`. No mata ni relanza. El agente no puede recibir entrada nueva en ese proceso. Si termina antes del barrido, no hay aviso y la validación final decide igual.

### Validación final

1. Se adoptan las fases y se calcula la versión.
2. Se lee el sobre con límite de bytes y la skill del intento.
3. Se validan identidad, nota, principios y anclas. Después se compone el cuerpo base.
4. Si hay fallos no omitidos, se registra una falla por clase y se devuelve una sola corrección con todas las causas. No se emite `producto_listo` ni pendiente.
5. Si todas las clases son válidas u omitidas, se captura el documento. Después se emite `producto_listo` con la misma `EntregaId` y el revisor recibe el documento y las fichas citadas.
6. Cuando todas las demás fases están `preparada`, `_revision_aprobada_vigente(enc)` y `(enc.entrega.nivel == "terminado" or nivel_cumplido(enc))` son verdaderas, `_intentar_cierre` proyecta los pendientes antes de `componer`. Actualiza el PR con `gh` y retorna sin componer `cierre.json`. Una aprobación intermedia no cumple la condición ni corta fases. Si el revisor pide cambios, el trabajo sigue su corrección normal.

Una nota encontrada solo al final puede pasar. El motor no afirma que se escribió al arrancar. Un JSON parcial durante el trabajo tampoco es una prueba de incumplimiento.

### Tercera falla real y pendiente visible

Hay un choque con el orden actual. Varios llamadores anotan `instrumento_fallo` antes de consultar `_impedimento_de_instrumento`. Como la función omite cuando encuentra dos fallos previos, esos recorridos omiten al registrar el segundo. `tests/test_bucle_instrumento.py:339-350` muestra dos ejecuciones antes de que `comprobaciones.json` quede no disponible.

El adaptador poteto no copia ese orden:

1. Deriva la identidad del fallo desde `EntregaId` y clase. `_fallo_ya_registrado` comprueba esa clave antes de consultar la escalera. Una retransmisión usa el resultado ya registrado y nunca vuelve a contar.
2. Solo para un fallo nuevo, consulta la escalera antes de anotarlo. Dos fallos históricos distintos más el actual constituyen la tercera falla.
3. La escalera marca `instrumento_no_disponible` y agrega `nota_pr`. No agrega todavía `pendiente_entrega` para las clases poteto.
4. Anota el fallo nuevo con su clave idempotente. Si el proceso muere después de anotar la segunda falla, la reincorporación la reconoce y no la convierte en la tercera.
5. `_omisiones_poteto` reconstruye las omisiones completas desde los eventos. Después de plegar, el adaptador devuelve el trabajo si alguna clase sigue reparable, sin proyectar pendientes.
6. Con las demás fases `preparada`, la aprobación vigente y `(enc.entrega.nivel == "terminado" or nivel_cumplido(enc))`, `_intentar_cierre` llama a `_proyectar_pendientes_poteto`. Agrega un pendiente por cada `InstrumentoOmitido`, con su causa y su conteo. Actualiza el PR mediante `gh` y retorna sin componer `cierre.json`. El siguiente barrido entrega con pendientes. La tercera falla no elimina la revisión del producto ni corta las fases intermedias.

Este orden evita tres salidas incorrectas. La segunda entrega no se confunde con la tercera. Una reincorporación no suma otra falla. Una omisión de `principios` tampoco fuerza `_entregar_con_pendientes` mientras `nota-arranque` o el producto todavía necesitan otra corrección.

Si `principios` queda omitido, el PR no lleva `## Principios`. Lleva `## Pendientes abiertos al entregar` con la causa. Si solo la nota queda omitida, los principios válidos se conservan y el pendiente declara la ausencia de la nota.

La omisión es por encargo, como exige el contador actual. También afecta las fases posteriores. No se crea otra escalera ni se cambia el orden de los instrumentos antiguos en este trabajo.

### Salida forzada sin documento coincidente

Otras escaleras pueden producir pendientes cuando el documento está vacío o no coincide con el producto. Por ejemplo, un relevo sin modelo disponible puede ocurrir antes de que el responsable capture su primera entrega válida. En ese caso, `_entregar_con_pendientes` no publica un cuerpo alternativo ni inventa una omisión poteto.

La salida conserva la causa en `nota_pr` y en la evidencia del cierre forzado. El aviso de cierre dice explícitamente que no se publicó el PR por falta de un documento coincidente. El encargo termina por la vía forzada existente, sin quedar lanzando procesos y sin presentar texto viejo como evidencia válida. La tercera falla poteto normal sí produce un documento, pasa por revisión y permite publicar el pendiente en el PR.

### Corrección con una sección vieja

Una corrección abre otra generación y otro sello. El reducer limpia el documento anterior al abrir el responsable.

- Si el sobre conserva la identidad anterior, la entrega se devuelve aunque las decisiones sean correctas.
- Si redeclara las mismas decisiones y anclas bajo la identidad nueva, pasa. No se exige un diff de prosa.
- Si `cuerpo-pr.md` conserva `## Principios`, el compositor lo elimina. Solo el documento actual puede generar esa sección.
- Si cambia otra parte del cuerpo sin cambiar el SHA de Git, el hash del cuerpo final permite republicarlo.

## Fichas `principle-*` y costo

Las fichas quedan como lectura obligatoria bajo demanda. No se pegan en el prompt y no se ignoran.

El código actual no pega ni siquiera la `SKILL.md`. Solo pasa su ruta (`motor/encargos/ciclo.py:6561-6568`). La nueva instrucción conserva esa forma y recuerda la regla de la propia skill. Antes de citar un principio, el responsable debe leer completa la ficha hermana `../<id>/SKILL.md` del mismo paquete.

El preflight garantiza que todas las fichas del catálogo existan. El validador congela solo las citadas para el revisor. Esto no demuestra que el agente las haya leído. La evidencia revisable es la decisión y su ancla.

Medición local de `/Users/dn/.agents/skills`:

| Material | Caracteres | Estimación a cuatro caracteres por token |
|---|---:|---:|
| `poteto-mode/SKILL.md` | 21,819 | 5,455 |
| Las 24 fichas | 33,645 | 8,411 |
| Conjunto | 55,464 | 13,866, redondeado a unos 14,000 |

No había un tokenizador instalado. Estas cifras son estimaciones, no conteos exactos. Las fichas medidas van de 396 a 2,615 caracteres, aproximadamente 100 a 654 tokens por ficha. La lectura bajo demanda evita unos 8,400 tokens de fichas en cada lanzamiento. El responsable y el revisor reciben solo el subconjunto citado.

## Decisión de síntesis

La arena produjo cinco candidatos sin bajas. Los candidatos 1 a 4 usaron Kimi. El candidato 5 usó Claude `fable-5-1`. El juez fue Claude `opus-5-5`, con herramientas de solo lectura. Su veredicto puntúa los seis criterios de la rúbrica y recomienda la base y los injertos descritos abajo.

Hubo dos formas estructurales. Una usa un sobre JSON y una sección generada por el motor. La otra acepta `## Principios` dentro de `cuerpo-pr.md` y lo vuelve a proyectar. La primera oculta mejor la gramática y separa fuente de salida. El juez señaló que la diversidad era justa porque todos comparten el mismo ciclo de control.

El juez eligió el candidato 2 con 19 puntos sobre 24. Los candidatos 1, 3 y 4 obtuvieron 18. El candidato 5 obtuvo 15. La diferencia de un punto no decide por sí sola. La ventaja decisiva del candidato 2 es que espera a que ambas clases sean válidas u omitidas antes de proyectar pendientes.

La síntesis adopta esa base y agrega estos injertos:

- Del candidato 1, el aviso mediante `procedimiento` deduplicado y las lecturas con límite de bytes. Esto evita otro campo en `Encargo`.
- Del candidato 3, la consulta a la escalera antes de anotar el fallo actual y la copia de las fichas citadas para el revisor.
- Del candidato 4, el preflight antes de gastar un turno y el recorrido válido sin proceso antes del primer barrido.
- Del candidato 5, el preflight que prueba la siguiente fuente, el sello derivado y el ancla por decisión.

Se rechazaron el recibo que conserva defectos del candidato 1, el Markdown como fuente del candidato 3, el contrato privado duplicado en runtime del candidato 4 y la interfaz de siete funciones con tipos ambiguos del candidato 5. También se retiró del candidato 2 el campo `poteto_contrato` y su cuerpo mínimo para históricos.

## Tradeoffs aceptados

- Aceptamos redeclarar el sobre en cada intento a cambio de rechazar declaraciones viejas sin comparar prosa.
- Aceptamos guardar un cuerpo de hasta 65,536 caracteres y las fichas citadas en SQLite a cambio de revisar y publicar lo mismo después de borrar el runtime.
- Aceptamos un aviso solo para el operador y el siguiente prompt a cambio de no arriesgar procesos *one-shot*.
- Aceptamos una validación estructural y juicio semántico del revisor a cambio de no inventar una prueba de lectura o aplicación.
- Aceptamos proyectar pendientes fuera de `_impedimento_de_instrumento` y solo con las demás fases preparadas, aprobación vigente y nivel `terminado` o nivel cumplido. Actualizamos el PR antes de retornar de `_intentar_cierre`, para que el siguiente barrido entregue con el pendiente visible sin eliminar la revisión ni cortar fases intermedias.
- Aceptamos que otra salida forzada anterior a ese punto muestre la omisión poteto solo como `nota_pr`, no como `pendiente_entrega`.
- Aceptamos que cualquier versión nueva vuelva al responsable antes de `producto_listo`, incluso cuando la detecta el revisor o la autoridad. Es la única forma de conservar la misma `EntregaId`. No cambia el criterio de revisión del producto.
- Aceptamos no publicar el PR si una salida forzada no tiene documento coincidente. La causa queda en la evidencia y en el aviso de cierre, sin fabricar un cuerpo alternativo ni una omisión.
- Aceptamos drenar los encargos en vuelo antes de activar el cambio a cambio de no mantener una segunda vía de publicación para históricos. Se usa el resumen existente y se espera el cierre normal, sin cancelar trabajo.
- Aceptamos que una omisión afecte las fases posteriores del encargo a cambio de conservar el contador existente por clase y encargo.

## Alternativas consideradas

**Archivos por intento sin documento en el reducer.** Cada intento escribiría `.interno/entregas/<token>/`. La publicación buscaría el directorio vigente. Esta forma evita eventos nuevos, pero deja selección, frescura y reconciliación en varios llamadores. Un corte entre archivos produce combinaciones parciales. La interfaz es más ancha y oculta menos.

**Markdown con identidad dentro de `cuerpo-pr.md`.** Reduce un archivo, pero mezcla fuente y salida. Incorporación, recorte y publicación comparten más gramática. El agente también debe sincronizar la misma identidad en dos archivos. El sobre JSON concentra esa frontera.

**Dos procesos por intento.** El primero escribe la nota y el segundo implementa. Esta forma sí acredita orden, pero duplica lanzamientos y expone reanudación y pérdida de sesión a cada CLI. Sigue sin probar lectura ni aplicación.

**Instrumentar hooks y transcripciones.** Podría observar lecturas reales, pero exige un adaptador por CLI y conservar formatos que hoy se borran. La interfaz terminaría conociendo proveedores. Leer una ficha tampoco demostraría aplicarla.

## Orden de implementación en unidades verificables

Cada unidad empieza con su recorrido fallando contra `d5d2636` y termina con ese recorrido verde. No se agregan pruebas de texto, pruebas de las pruebas ni mutantes.

1. **Distribución, identidad y nota.** Agregar `motor/poteto.py`, preflight que pruebe la siguiente fuente, sello, sobre, instrucciones, ruta en `arranque.json`, aviso y compuerta de nota. Centralizar las fixtures de skill y declaración en un helper común. Verificar el recorrido 1 y las suites de arranque existentes.
2. **Catálogo, principios y documento.** Añadir decisiones, anclas, fichas congeladas, evaluación, evento de documento e invariante del reducer. Migrar los cuatro emisores a `_anotar_producto_listo` en la misma ola. Verificar el recorrido 2 y las suites de PR existentes.
3. **Tercera falla y salida diferida.** Registrar las clases para `_es_verificable` y validar en cada incorporación. Deduplicar cada fallo antes de contar, reconstruir omisiones completas y proyectar pendientes después de la aprobación independiente. Verificar el recorrido 3 y las suites `test_bucle_instrumento.py` y `test_motor_sin_personas.py`.
4. **Correcciones y republicación.** Limpiar el documento al abrir otro responsable, retirar secciones viejas, reservar pendientes y notas al recortar y agregar el hash del cuerpo final a la deduplicación. Verificar el recorrido 4 y `test_cuerpo_pr.py`.
5. **Integración.** Actualizar `docs/flujo-revisiones.md` con el contrato y la escalera vigente. Ejecutar la batería estándar y `pre-commit run --all-files`. Antes de activar el contrato, usar el resumen existente para esperar, sin cancelar, que los encargos en vuelo cierren por el flujo actual.

Los comandos acotados durante las unidades son:

```bash
PYTHONPATH=. python3 -W ignore -m unittest tests.test_poteto_entrega
PYTHONPATH=. python3 -W ignore -m unittest tests.test_bucle_instrumento tests.test_motor_sin_personas tests.test_cuerpo_pr
```

## Cuatro recorridos de prueba

Los cuatro recorridos viven en `tests/test_poteto_entrega.py`. Usan el banco existente de barridos, registro y GitHub falso. Conducen lanzamientos e incorporaciones, no funciones privadas de parseo. Las aserciones sobre el PR comprueban el entregable visible, no texto del prompt ni detalles internos.

### 1. Nota ausente y se devuelve

Arrancar un responsable que sigue vivo sin nota y observar un cambio de producto. Comprobar que aparece un aviso tentativo en el resumen, que el proceso no fue cerrado y que no existe `instrumento_fallo` de `nota-arranque`.

Publicar después un outcome sin nota y retirar el proceso. Comprobar que el encargo vuelve al responsable con `reparar_instrumento`, que el fallo tiene clase `nota-arranque` y que no hay `producto_listo` ni llamada al revisor. Esto demuestra el aviso no bloqueante y la compuerta final sin proceso vivo.

### 2. Principios presentes y pasa

Entregar nota y principios con identidad actual, un id derivado de la `SKILL.md` copiada, decisiones y anclas válidas. Incluir `principle-sequence-verifiable-units` para recorrer el nombre explícito ya declarado por la fuente. Retirar el proceso antes del primer barrido.

Comprobar que se captura `documento_poteto`, que `producto_listo` queda aceptado y que el revisor puede abrirse. Tras la aprobación, comprobar que el PR contiene una sola sección `## Principios`, las decisiones y sus anclas. El revisor recibe además las fichas citadas de la misma copia.

### 3. Tercera falla y pasa con pendiente visible

Entregar tres intentos con nota válida y principios inválidos. Los dos primeros vuelven al responsable. Reincorporar el segundo outcome antes de entregar el tercero y comprobar que sigue habiendo solo dos fallos, sin omisión anticipada.

En el tercero, comprobar que `principios` queda no disponible con `InstrumentoOmitido(fallos=3)` y su causa, que el documento y el producto se registran y que el revisor recibe el expediente. Comprobar que todavía no se fuerza la entrega. Cuando las demás fases están preparadas, la aprobación es vigente y el nivel es `terminado` o está cumplido, comprobar que `_intentar_cierre` proyecta el pendiente, que `gh` recibe el cuerpo actualizado con su nuevo hash y que retorna sin componer `cierre.json`. El siguiente barrido entrega sin una cuarta reparación de principios. El PR contiene el pendiente completo y no contiene `## Principios`.

### 4. Corrección con sección vieja

Crear una primera entrega válida con `## Principios` heredado en `cuerpo-pr.md`. En la corrección, conservar el sobre y el cuerpo anteriores. Comprobar que la identidad vieja se devuelve y no produce un nuevo `producto_listo`.

En el siguiente intento, redeclarar exactamente las mismas decisiones y anclas bajo la nueva identidad. Mantener el mismo producto y cambiar otra parte del cuerpo. Comprobar que pasa sin exigir un diff en los principios, que el PR tiene una sola sección generada y que GitHub recibe el cuerpo nuevo aunque el SHA de Git no cambie.

## Preguntas abiertas y riesgos

- ¿Con qué consistencia rechazará el revisor una decisión genérica con ancla válida pero sin relación con el diff? El contrato ubica ese juicio fuera del parser.
- ¿El crecimiento de SQLite por cuerpos y fichas resulta aceptable en encargos con muchas correcciones? Existe un límite por cuerpo, no por número de entregas.
- ¿El drenaje de encargos en vuelo puede hacerse sin pausar trabajo útil? Es una condición de activación, no una vía de compatibilidad.
- ¿El aviso temprano por JSON parcial genera ruido? Debe decir "aún no observable" y desaparecer al validar, nunca afirmar incumplimiento.

## Siguiente paso de implementación

Escribir primero el recorrido de nota ausente contra `d5d2636` y después añadir la identidad del intento y la compuerta final mínima que lo haga pasar.

## Principios

- **Exhaust the Design Space.** Cinco candidatos y dos formas de serialización evitaron elegir la primera interfaz suficiente. La salida con pendientes del candidato 2 cambió la base.
- **Model the Domain.** `DocumentoPoteto`, `CorreccionPoteto` e `InstrumentoOmitido` reemplazan booleanos y valores `None` con significados mezclados.
- **Boundary Discipline.** `motor/poteto.py` concentra JSON, Markdown, catálogo y anclas. El reducer, el revisor y GitHub reciben tipos de dominio.
- **Type System Discipline.** `EntregaId` liga intento, fase y versión. Una declaración válida, un defecto y una omisión no comparten representación.
- **Laziness Protocol.** Un solo sobre, cuatro funciones y la escalera existente evitan archivos por intento, controladores de CLI y una vía legacy de publicación.
- **Separate Before Serializing Shared State.** El responsable escribe el sobre. El motor escribe el registro y la sección publicada. Nadie sincroniza `## Principios` en dos fuentes.
- **Make Operations Idempotent.** La primera escritura del documento, las claves por entrega y el hash del cuerpo hacen que reincorporar y republicar converjan. Los pendientes esperan documento, aprobación de la última fase y nivel `terminado` o nivel cumplido.
- **Minimize Reader Load.** La publicación lee `Fase.documento_poteto` y no vuelve a parsear ni a reconstruir identidad desde el revisor.
- **Encode Lessons in Structure.** `_POTETO_EVIDENCIA` deriva las tablas. La validación real es `evaluar_entrega` en cada incorporación. El catálogo sale de `SKILL.md`. La sección pública solo se genera desde el documento.
- **Sequence Work into Verifiable Units.** Las cuatro unidades funcionales terminan con un recorrido observable antes de pasar a la siguiente.
- **Test Behavior, Not Implementation.** Los cuatro recorridos usan barridos, registro y GitHub falso. No prueban prompts, parsers ni pruebas.

## Verificación del diseño

- La batería estándar del código terminó sobre el árbol final con 144 pruebas verdes y 2 omitidas en 333.232 segundos. Las pruebas no leen el documento, así que esa corrida no valida su contenido.
- El análisis del documento validó 9 bloques Python, 1 bloque JSON, 12 firmas sin implementación y 4 recorridos. Otras comprobaciones encontraron 40 referencias completas y 9 cortas con rutas existentes y rangos dentro del archivo. No prueban su contenido semántico.
- Un revisor de Kimi encontró cuatro problemas. Después, Claude Opus revisó el documento completo contra el código en varias rondas de corrección y cerró con `Sin bloqueantes`.
- `pre-commit run --all-files` y `git diff --check` pasaron con Gitleaks y la revisión de conflictos.
