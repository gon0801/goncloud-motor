# Reglas del motor

Este archivo es la fuente única de las reglas permanentes. `CLAUDE.md` es una copia generada
de este texto (`cp AGENTS.md CLAUDE.md`); no se edita a mano. La prueba
`tests/test_calidad.py` falla si los dos archivos difieren.

| Regla | Decisión de David | Qué la hace cumplir |
| --- | --- | --- |
| Nada espera a un humano salvo el merge. | 3-nov | `motor/encargos/ciclo.py` sigue solo; `tests/test_motor_sin_personas.py`. |
| CI no es compuerta: solo frena el merge. | 3-nov | `motor/encargos/ciclo.py`; `tests/test_ci_en_el_pr.py`. |
| Sin tope de rondas. | 1-nov | `tests/test_freno_vueltas.py` (el freno no escala por contar vueltas). |
| Arreglos de raíz. | 1-nov | `tests/test_flujo_revisiones.py` y la revisión independiente; no hay check automático. |
| OpenCode Go solo DeepSeek. | 1-nov | `motor/encargos/catalogo_agentes.py`; `tests/test_catalogo_opencode.py`. |
| Los Medium se corrigen en el mismo PR. | 30-oct | `motor/encargos/ciclo.py`; `tests/test_lectura_deepseek.py`. |
| Las pruebas corren con el HOME en un temporal. | David, sin fecha registrada en el pedido | `tests/guarda_home.py`; `conftest.py`. |
| Repo público, sin secretos ni runners propios. | 31-oct | `.github/workflows/quality.yml` (gitleaks); `.gitleaks.toml`. |
| Un dato, un dueño: derivar en vez de guardar copias. | David, sin fecha registrada en el pedido | Nada todavía. |

## Calidad

Los mismos comandos corren en local y en el job `gate` de `.github/workflows/quality.yml`:

- `python3 tools/calidad.py tipos` revisa con pyright en modo básico `motor/` y los dobles de
  GitHub de `tests/`.
- `python3 tools/calidad.py lint` revisa el código con ruff.
- `python3 tools/calidad.py io` falla ante cualquier `read_text(`, `write_text(` o
  `json.loads(...read_text...)` fuera de `motor/io.py`; solo `motor/daemon.py` queda exento
  por la prohibición de tocar el modo planes.
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
