# Reglas del motor

Las decisiones de David viven en `motor/encargos/politica.py`; la tabla de abajo se genera
desde esas filas y un check falla si difieren. `CLAUDE.md` es una copia generada de este
texto (`cp AGENTS.md CLAUDE.md`); no se edita a mano. La prueba `tests/test_calidad.py`
falla si los dos archivos difieren.

| Regla | Valor | Decisión de David | Qué la hace cumplir |
| --- | --- | --- | --- |
| DeepSeek: 30 minutos y se sigue con omisión explícita. | 30 min | David, 6-nov | a los 29 min espera; a los 31 min omite con la causa escrita |
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

## Calidad

Los mismos comandos corren en local y en el job `gate` de `.github/workflows/quality.yml`:

- `python3 tools/calidad.py tipos` revisa con pyright en modo básico `motor/` y los dobles de
  GitHub de `tests/`.
- `python3 tools/calidad.py lint` revisa el código con ruff.
- `python3 tools/calidad.py io` falla ante cualquier `read_text(`, `write_text(` o
  `json.loads(...read_text...)` fuera de `motor/io.py`; solo `motor/daemon.py` queda exento
  por la prohibición de tocar el modo planes.
- `python3 tools/calidad.py tamano --base origin/main` falla si un archivo de `motor/` de más
  de 1,000 líneas o una función de más de 100 mide más que en la base, en líneas o en tokens
  de código, o si uno que en la base no existe o cabe pasa del límite. Borrar comentarios o
  líneas en blanco no deja espacio. Los tamaños se leen de git; no hay línea base que editar.
  Se arregla sacando código de ahí. En un PR también lo juzga el job `tamano-desde-la-base`
  (`pull_request_target`), que corre el `calidad.py` de la base con `--cabeza` sobre la unión
  del PR con la punta de la base, leída de git: editar la herramienta o el workflow en el PR no
  lo apaga. Cada push a `main` lo repite en los PR abiertos (`tamano-al-avanzar-la-base.yml`).
- `python3 tools/pruebas_nuevas.py --base origin/main` falla si una prueba nueva o cambiada
  pasa con el código de la base, salvo que lleve `# no-roja-en-base: <razón>`.

La línea base de los errores de hoy está en `.github/calidad/baseline.json`. Solo frena los
errores nuevos. Para regenerarla se corre `python3 tools/calidad.py tipos --actualizar` con el
código de la rama base. Las herramientas se instalan con
`python3 -m pip install pyright==1.1.414 ruff==0.17.4`.

## Pruebas y revisiones

- Elige pruebas, verificaciones y revisiones que aporten evidencia útil al comportamiento solicitado
  y al riesgo del cambio. Explica qué demuestra cada comprobación y qué incertidumbre resuelve.
- Explica el impacto real y la evidencia de cada hallazgo. Distingue los problemas que impiden
  cumplir el pedido de las observaciones que ayudan a mejorarlo.
- Aplica poteto-mode siempre y usa las skills, herramientas y apoyos que aporten al trabajo.
  La revisión por otro modelo y la revisión real de DeepSeek se mantienen en el flujo de entrega.
- Conserva la evidencia que sigue siendo válida. Repite una comprobación o amplía una revisión
  cuando un cambio, un riesgo o información nueva lo justifiquen.
- Nunca subir contrasenas ni llaves a un repo.
