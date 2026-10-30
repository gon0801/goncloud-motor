# Fase 1: estado propio de las fases

Parche local para revisión. No incluye despliegue, PR abierto ni cambios en la
instalación. Base: `c8b368252af884f6f4cd52ea038749d54333803d`, rama
`feat/fases-encargo-fase1`, worktree `/Users/dn/dev/goncloud-motor-fases`.

## Qué conserva cada fase

`Encargo` sigue siendo la unidad de entrega y la autoridad del registro. Cada
fase posee su alcance, producto, revisiones, hallazgos, entrega y rama. El
encargo no guarda copias: `producto`, `revision_vigente`, `hallazgos`,
`nivel_alcanzado`, `pr_numero` y `pr_sha` se leen de la fase activa.

```python
class Fase:
    id, resultado, estado, requiere
    producto, revisado, hallazgos, aprobadas
    revision_vigente, deepseek_vigente
    nivel_alcanzado, pr_numero, pr_sha, deepseek_sha, rama, evidencia
```

Estados: `pendiente`, `trabajo`, `revision`, `entrega`, `preparada`. Una fase
preparada no se vuelve a ejecutar. `fase_activa` apunta a la fase en curso; si
no hay ninguna, la vista usa la última con datos y `objetivo.completo` dice si
el pedido entero terminó.

## Quién organiza las fases

El usuario sigue entregando texto normal. El implementador, en su primera
sesión, escribe `.interno/fases.json`:

```json
{"fases": [
  {"id": "datos", "resultado": "leer y conservar las métricas diarias"},
  {"id": "resumen", "resultado": "mostrar el total diario", "requiere": ["datos"]}
]}
```

`motor/encargos/fases.py` lo lee y el reducer valida ids, resultados,
dependencias y que la propuesta no omita fases con trabajo. Un archivo inválido
se registra como instrumento defectuoso y vuelve al implementador; no se
convierte en una pregunta al usuario. Si el pedido ya tiene fases adoptadas, el
archivo se fusiona por id y no se reescribe una fase ya trabajada.

## Eventos y respuestas tardías

`producto_listo`, `revision`, `entrega_externa` y el cierre llevan `fase` e
`intento`. El reducer rechaza un evento dirigido a otra fase o a un intento que
ya no está vigente, de modo que una respuesta tardía no aprueba otro trabajo.
Las claves de idempotencia incluyen la fase.

## Cierre y avance

El cierre de una fase exige la revisión independiente aprobada sobre su versión,
los recibos de efectos y, si el nivel autorizado es `pr` o superior, la lectura
de DeepSeek y el nivel alcanzado. Al prepararla, se fija la firma exacta que
consumen las fases que la requieren.

- Si quedan fases, el encargo vuelve a `recibido`, `rol=responsable` y la
  siguiente fase queda activa. No se publica el resultado final.
- Si era la última, el encargo pasa a `terminado` y se publica el resultado.
- Parar, cancelar y cerrar el árbol de procesos no cambian.

## Compatibilidad con el historial

Los eventos anteriores no traen `fase`. Al primer producto, revisión, entrega o
cierre, el reducer crea la fase implícita `fase_unica` con el resultado del
pedido. Conserva los estados, aprobaciones y recibos que ya existen; no inventa
aprobaciones ni reactiva encargos detenidos.

## Límite de esta entrega

No se implementaron el puente de herramientas, los conectores, la corrección
central de DeepSeek, los PR apilados ni el despliegue. La rama del producto, la
rama seleccionada y la rama publicada son la rama autorizada del encargo; la
separación por fase y el apilado corresponden a la fase 3 del plan. Una fase sin
producto publicable no abre PR.

## Correcciones de la revisión

1. El resultado recibido se liga a la fase y al intento que lo produjeron. La
   asociación conductor-fase se guarda como evento propio en todas las rutas que
   aceptan el resultado, también cuando la fase se reconoce por un producto ya
   registrado, y se consulta antes de inferir nada; después valen la fase del
   intento y la versión del producto. La clave
   `resultado:<encargo>:<fase>:<token>` es la identidad de la operación: repetir
   el mensaje devuelve el recibo anterior y un contenido distinto con la misma
   identidad se rechaza como conflicto.
2. La rama publicada es la misma que se creó al preparar el repositorio. Se
   retiró la rama por fase de esta entrega, porque creaba una referencia
   inexistente en la primera publicación.
3. Al continuar, se selecciona la primera fase disponible y se conserva el rol y
   el paso de una fase ya iniciada. Una fase en revisión retoma al revisor; una
   reparación de instrumento, una corrección o una fase pendiente activan al
   implementador. Durante la parada, el cierre conserva la fase preparada y mueve
   la selección al trabajo pendiente. Si todas las fases quedaron preparadas, el
   siguiente barrido completa el cierre global con una clave propia.
4. La adopción y el avance eligen una fase cuyas dependencias estén preparadas.
   Reorganizar la propuesta conserva las versiones de entrada ya fijadas y no
   permite reescribir las dependencias de una fase trabajada.

## Comprobación

- Recorrido de dos fases, reinicio del supervisor y cierre global: ensayo con
  base temporal y eventos reales del registro. La fase 1 queda preparada, la 2
  activa y el encargo no termina hasta la segunda.
- Agente real por suscripción: `codex exec` leyó el prompt del motor y escribió
  dos fases y solo el producto de la primera. El motor las adoptó y dejó la fase
  1 en revisión.
- Historial anterior: producto y revisión sin `fase` se reconstruyen como
  `fase_unica` preparada.
- Respuesta tardía: una revisión dirigida a la fase cerrada o a un intento
  anterior devuelve `ilegal`.
- Parada: un proceso propio del ensayo se cierra y el encargo queda detenido.

Simulación y agente real son evidencias distintas; la simulación reproduce
fallos y el agente real acredita la sesión.
