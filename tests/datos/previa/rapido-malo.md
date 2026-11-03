# Rápido malo
autopiloto: motor
plan: previa-rapido-malo
repo: git@github.com:gon0801/goncloud-motor.git

## Bloque A — rápido con despliegue
lane: fast
despliegue: desplegar=d.sh checklist=c.sh rollback=r.sh
prueba-real: test -f README.md
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | cambia un texto | test -f README.md |
