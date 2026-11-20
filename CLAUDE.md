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

## Cómo trabajar con David

Habla conmigo en español claro. Toma mi petición y el plan que haya aprobado como guía para el trabajo. Usa los modelos, cuentas y destinos acordados para ese encargo.

Hazte cargo del resultado. Decide por tu cuenta lo que te haya delegado y comprueba que el resultado funciona donde voy a usarlo. Elige comprobaciones proporcionales al cambio.

Si algo falla, averigua por qué, corrígelo y continúa. Si un intento repite el mismo fallo, cambia de enfoque usando lo que aprendiste. El número de intentos, por sí solo, no decide cuándo parar.

Si falta algo que solo yo pueda aportar, o hace falta una decisión fuera del encargo, avanza con el resto y dime qué ocurrió, qué intentaste y qué decisión concreta necesitas. Al cerrar, dime qué quedó hecho, cómo lo comprobaste y qué sigue pendiente.

### Solo en este repo

- En el ciclo del motor, las vueltas sin avance y los bloqueos de revisión sin resolver siguen su
  tabla de decisiones (arriba, de `motor/encargos/politica.py`) y `docs/flujo-revisiones.md`, no la
  regla general de 3 rondas ni su espera a David.
  Dentro de lo que el encargo autoriza ningún paso espera a un humano salvo el merge, y las esperas
  técnicas siguen los topes de esa tabla; lo que el encargo no autoriza espera a David, como dice
  el párrafo inicial.
- Antes de mergear un cambio que altere cómo se lee el registro (reductor o validación), corre
  `ops/compuerta-plegado.sh --sha <cabeza>`. Solo lee y tarda un minuto. Si una máquina no contesta,
  vale lo que digan las demás; a esa máquina se le corre cuando vuelva, antes de desplegarle.
- Solo cuando se agrega un tipo de evento: se enseña primero al reductor y se despliega antes que
  su escritor. El revisor lo comprueba con `python3 tools/calidad.py kinds --base origin/main`.
- Después de desplegar, corre `ops/canario-encargos.sh`. Una máquina que no contesta no frena a
  las otras: se anota y se le corre cuando vuelva.
