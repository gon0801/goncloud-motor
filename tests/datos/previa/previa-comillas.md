# Previa mala por comillas
autopiloto: motor
plan: previa-comillas
repo: git@github.com:gon0801/goncloud-motor.git

## Bloque A — acepta entre comillas invertidas
lane: gate
prueba-real: test -f README.md
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | revisa el readme | `test -f README.md` |
