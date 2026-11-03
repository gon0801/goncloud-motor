# Previa mala por módulo
autopiloto: motor
plan: previa-mala
repo: git@github.com:gon0801/goncloud-motor.git

## Bloque A — módulo que falta
lane: gate
prueba-real: test -f README.md
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | importa un módulo que no está | python3 -c "import modulo_que_no_existe_24oct" |
