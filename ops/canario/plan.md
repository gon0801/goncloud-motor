# Prueba de fuego del motor: tres piezas chicas y un paso de login
autopiloto: motor
plan: canario
repo: git@github.com:gon0801/motor-canario.git

<!-- Plan desechable de MOTOR 01, bloque I. Lo entregan claw (Mini) y Hermi (PC) con ops/canario/corre.sh,
que reinicia cada máquina a media corrida. Todo comando va como `bash <script>` y sin rutas de Mac:
corre igual en la Mini y, con bash de Git, en la PC. -->

## Bloque A — un saludo que se puede probar
lane: gate
depende: -
aviso: el saludo del canario ya existe y está probado
prueba-real: bash canario/prueba-real.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | Crear `canario/saluda.sh`: imprime `hola canario` y sale 0. Crear `canario/prueba-real.sh`: corre `bash canario/saluda.sh` y sale 0 solo si la salida es exactamente `hola canario`. | bash canario/prueba-real.sh |
| A.2 | Crear `canario/espera.sh`: duerme 150 segundos imprimiendo un punto cada 10 (mantiene el trabajo a medias el tiempo suficiente para el reinicio) y sale 0; agregar a `canario/saluda.sh` una línea de comentario que diga que `espera.sh` existe. | bash canario/prueba-real.sh |

## Bloque B — lo mismo en un segundo repo
lane: gate
depende: -
repo: git@github.com:gon0801/motor-canario-b.git
aviso: el segundo repo del canario ya tiene su propio saludo
prueba-real: bash canario/prueba-real.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Crear `canario/despide.sh`: imprime `adios canario` y sale 0. Crear `canario/prueba-real.sh`: corre `bash canario/despide.sh` y sale 0 solo si la salida es exactamente `adios canario`. | bash canario/prueba-real.sh |

## Bloque C — desplegar a una carpeta
lane: release
depende: A
aviso: el canario ya quedó desplegado en su carpeta
prueba-real: bash canario/prueba-real.sh
despliegue: desplegar=ops/despliega.sh checklist=ops/checklist.sh rollback=ops/rollback.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Crear `ops/despliega.sh` (copia `canario/saluda.sh` a `$HOME/motor-canario-despliegue/` y deja ahí un archivo `LISTO`), `ops/checklist.sh` (sale 0 solo si existen `LISTO` y `saluda.sh` en esa carpeta y `saluda.sh` imprime `hola canario`) y `ops/rollback.sh` (borra esa carpeta). Los tres con `bash`, sin `sudo`, y solo escriben en esa carpeta. | bash ops/despliega.sh && bash ops/checklist.sh |

## Bloque H — un login que se detecta solo
lane: fast
depende: C
humano: login Entra al sitio de prueba del canario y acepta el permiso
sonda-humano: bash -c "test -f ~/motor-canario-despliegue/LISTO"
