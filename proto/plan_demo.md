# Demo: saludo y versión 2 en producción
autopiloto: taller
plan: demo

## Bloque A — archivos de saludo
lane: gate
aviso: los archivos de saludo ya existen
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | escribe: saludo.txt=hola | test "$(cat saludo.txt)" = hola |
| A.2 | escribe: despedida.txt=adios | test -f despedida.txt |

## Bloque B — versión 2
lane: gate
depende: A
aviso: la versión 2 está lista
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | escribe: version.txt=v2 | test "$(cat version.txt)" = v2 |

## Bloque D — la versión 2 en producción
lane: release
depende: B
aviso: la versión 2 ya está en producción
prueba-real: test "$(cat "$PROD_DIR/version.txt")" = v2
despliegue: desplegar=ejecucion/D/desplegar.sh checklist=ejecucion/D/checklist.sh rollback=ejecucion/D/rollback.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | escribe: ejecucion/D/desplegar.sh=mkdir -p "$PROD_DIR" && cp version.txt "$PROD_DIR/version.txt" | test -f ejecucion/D/desplegar.sh |
| D.2 | escribe: ejecucion/D/checklist.sh=test "$(cat "$PROD_DIR/version.txt")" = v2 | test -f ejecucion/D/checklist.sh |
| D.3 | escribe: ejecucion/D/rollback.sh=rm -f "$PROD_DIR/version.txt" | test -f ejecucion/D/rollback.sh |

## Bloque E — permiso de Seller Central
lane: fast
humano: login Entra a Seller Central y acepta el permiso de anuncios
sonda-humano: test -f "$TALLER_HOME/login-hecho"
