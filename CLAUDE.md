# Reglas del motor

Las decisiones de David viven en `motor/encargos/politica.py`; la tabla de abajo se genera
desde esas filas y un check falla si difieren. `CLAUDE.md` es una copia generada de este
texto (`cp AGENTS.md CLAUDE.md`); no se edita a mano. La prueba `tests/test_calidad.py`
falla si los dos archivos difieren.

| Regla | Valor | Decisión de David | Qué la hace cumplir |
| --- | --- | --- | --- |
| La limpieza se reintenta desde el registro, sin borrar de golpe. | 3 intentos; 10 min; 20 borrados por pasada; 20 consultas GitHub por 120 s | David, 16-nov | el tercer fallo agota el recurso; cada reintento espera 10 min y una pasada borra a lo sumo 20 recursos; las consultas a GitHub comparten un presupuesto de 20 por 120 s |
| DeepSeek: 30 minutos y se sigue con omisión explícita. | 30 min | David, 6-nov | a los 29 min espera; a los 31 min omite con la causa escrita |
| Sin el revisor de DeepSeek en el SHA ni en la base del PR no se espera su revisión. | 0 min | David, 2026-11-13 | sin un workflow de ai-review omite enseguida con la causa escrita; lo que no se pudo leer espera el tope |
| Toda espera técnica tiene un tope y al vencer detiene o cumple su acción declarada. | 60 min por omisión | David, 12-nov (decisión 5) | una espera sin tope detiene con causa escrita; las filas con tope previo conservan su acción salvo merge y repo |
| Las esperas pasajeras externas pueden durar una noche antes de detener. | 6 h | David, 17-nov | autoridad, conductor, proveedor, empuje transitorio, GitHub, merge y repo reintentan durante 6 h; al tope se detiene |
| Un encargo cerrado se sondea 7 días desde el cierre, con 5 consultas por pasada. | 7 días; 5 por pasada | David, 6-nov | a los 6 días sondea; a los 8 no; 5 consultas entran y la 6.ª queda para la pasada siguiente |
| El CI no autoriza el merge, pero no frena publicar, revisar ni corregir. | no es compuerta | David, 3-nov | un CI pendiente o verde no detiene la entrega; un error de lectura nunca cuenta como verde |
| Sin tope de rondas. | sin tope | David, 1-nov | el freno no escala por contar vueltas |
| DeepSeek bloquea con Critical, High o Medium dentro del alcance. | Critical, High o Medium | David, sin fecha registrada en el pedido | un Medium abierto en alcance bloquea; uno fuera de alcance no |
| Los Medium se corrigen en el mismo PR. | mismo PR | David, 30-oct | un Medium abierto en alcance reabre la fase y vuelve al responsable; no espera otro encargo |
| Si Opus no está disponible para revisar, revisa Astra mientras tanto. | openai/gpt-6-astra | David, 6-nov | una sesión de Opus ilegible no espera a una persona: el rol relega a Astra; cuando Opus vuelve, la siguiente revisión es suya |
| Nada espera a un humano salvo el merge. | sin espera | David, 3-nov | el ciclo sigue solo; la prueba del motor sin personas |
| Arreglos de raíz. | causa, no síntoma | David, 1-nov | la revisión independiente y las pruebas del flujo de revisiones |
| OpenCode Go solo DeepSeek. | opencode-go/deepseek-v4.1-flash | David, 1-nov | el catálogo rechaza otro modelo de OpenCode Go |
| Las pruebas corren con el HOME en un temporal. | HOME temporal | David, sin fecha registrada en el pedido | la guarda de HOME del banco de pruebas |
| Repo público, sin secretos ni runners propios. | público y sin secretos | David, 31-oct | gitleaks en el job de calidad |
| Un dato, un dueño: derivar en vez de guardar copias. | derivar | David, sin fecha registrada en el pedido | la tabla de política y la de AGENTS.md se generan de una sola fuente |
| Mientras se reconstruye el centro, un Medium de DeepSeek en estas rutas va a la lista de fuera de alcance, no a otro parche; Critical y High no cambian. | motor/encargos/ciclo.py, motor/encargos/dominio.py | David, 12-nov | un Medium anclado en una ruta congelada queda en «Hallazgos fuera de alcance» con su razón; uno en otra ruta, o un High en la congelada, vuelve al responsable |

## Reglas de trabajo

Iguales en todos los repos de David. Si otro documento de este repo dice otra cosa sobre calidad,
candados, evidencia, rondas o pruebas, manda esto, salvo un plan que David aprobó para ese trabajo:
ahí manda el plan. Si una regla no se puede cumplir, el trabajo sigue y se anota por qué en el PR.
Solo se detiene ese paso, hasta resolverlo, por secretos, por seguridad o por algo que el plan no
autoriza (borrar datos, gastar, escribir en producción, hablar con terceros en nombre de David),
que espera su aprobación. Lo demás no espera a nadie.

Sin excepción:

- Todo cambio entra por PR; nadie sube directo a la rama principal.
- Las llaves y contraseñas quedan fuera del repo, y la revisión de secretos corre siempre (sin
  `--no-verify`). Si marca algo que no es una llave, se agrega a `.gitleaksignore` con su razón y
  se sigue.

Con criterio:

- Pruebas obligatorias en 4 casos: dinero (precios, pujas, presupuestos, cobros), escribir datos de
  producción, instalar o borrar en las máquinas, y una falla que ya pasó en producción (una prueba,
  no una batería). Fuera de esos casos, una prueba solo si es la forma más barata de comprobar que
  funciona. Por defecto no se hacen baterías, pruebas de texto ni mutantes; sí cuando son la forma
  más barata de demostrar un riesgo de dinero o de datos (por ejemplo, el texto exacto con el que
  alguien decide). Fuera de eso, el revisor no las pide.
- Las pruebas que ya existen deben pasar antes de mergear. Si una ya falla igual en la rama
  principal, no frena: se anota en el PR.
- El revisor normal es un modelo distinto del que escribió; si no está disponible, el siguiente que
  sí lo esté. La revisión no espera a una persona. Solo bloquea por seguridad, datos, el
  comportamiento pedido roto o uno de los 4 casos sin su prueba, con el comando que lo reproduce o,
  si no se puede reproducir, la línea exacta y el riesgo. Lo demás va en notas.
- Sin tope de rondas mientras cada una traiga avance. Si queda un bloqueo concreto, el implementador
  lo corrige y el revisor comprueba la corrección; cuando solo quedan notas, se integra el PR. Si el
  mismo bloqueo vuelve 3 rondas seguidas sin avance, lo revisa otro modelo; si ese tampoco lo
  resuelve, se escala a David con el bloqueo y su evidencia.
- Todo cambio que toca producción dice en el PR cómo regresarlo.
- Compuertas (tamaño, lint, tipos, checklists, baterías, CI obligatorio) y revisores extra además
  del normal se agregan solo si David lo pide.

### Solo en este repo

- En el ciclo del motor, las vueltas sin avance siguen su tabla de decisiones (arriba, de
  `motor/encargos/politica.py`) y `docs/flujo-revisiones.md`, no la regla general de 3 rondas.
  Dentro de lo que el encargo autoriza no se espera a nadie salvo el merge; lo que el encargo no
  autoriza espera a David, como dice el párrafo inicial.
- Antes de mergear un cambio que altere cómo se lee el registro (reductor o validación), corre
  `ops/compuerta-plegado.sh --sha <cabeza>`. Solo lee y tarda un minuto. Si una máquina no contesta,
  vale lo que digan las demás; a esa máquina se le corre cuando vuelva, antes de desplegarle.
- Solo cuando se agrega un tipo de evento: se enseña primero al reductor y se despliega antes que
  su escritor. El revisor lo comprueba con `python3 tools/calidad.py kinds --base origin/main`.
- Después de desplegar, corre `ops/canario-encargos.sh`. Una máquina que no contesta no frena a
  las otras: se anota y se le corre cuando vuelva.
