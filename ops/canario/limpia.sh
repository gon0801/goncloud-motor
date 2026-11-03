#!/usr/bin/env bash
# Rollback de ops/canario/corre.sh: borra solo las marcas de reinicio de esta máquina para que un
# nuevo intento pueda volver a llevar cada máquina a media corrida. No toca ssh, ni la Mac, ni los
# planes ya entregados. Siempre sale 0 (limpiar lo que no existe no es una falla).
set -uo pipefail
ESTADO="${MOTOR_CANARIO_ESTADO:-$HOME/.motor-arranque/canario}"
rm -f "$ESTADO/mini.reiniciada" "$ESTADO/pc.reiniciada"
echo "marcas de reinicio del canario borradas en $ESTADO"
