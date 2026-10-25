# Previa mala por archivo ignorado
autopiloto: motor
plan: previa-ignorado
repo: git@github.com:gon0801/goncloud-motor.git

## Bloque A — archivo ignorado
lane: gate
prueba-real: test -f README.md
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | revisa el registro ignorado | test -f ignorado.log |
