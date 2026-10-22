#!/usr/bin/env bash
# daemon_info y espera_daemon_en_etiqueta de ops/prueba-motor-02.sh con un HOME de mentira:
# un daemon ancestro se ve, uno que arrancó antes del cambio de ~/motor no pasa por nuevo
# y uno que arrancó después sí pasa.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

. "$RAIZ/ops/lib.sh"
export HOME="$S/home" MOTOR_MAC_SSH="" MOTOR_PRUEBA_ESPERA_S=1
mkdir -p "$HOME/.motor"
cp "$RAIZ/ops/lib.sh" "$S/"
sed '/^for m in /,$d' "$RAIZ/ops/prueba-motor-02.sh" >"$S/p.sh"

git init -q "$HOME/motor"
g() { git -C "$HOME/motor" -c user.name=t -c user.email=t@t "$@"; }
viejo=$(( $(date +%s) - 7200 ))
GIT_COMMITTER_DATE="@$viejo" g commit -q --allow-empty -m viejo
GIT_COMMITTER_DATE="@$((viejo + 3600))" g commit -q --allow-empty -m nuevo
g tag "$ETIQUETA"
g checkout -q --detach HEAD~1

espera "daemon ancestro se ve" 0 python3 -c 'import subprocess,sys; sys.exit(subprocess.call(["bash","-c",". \"$1/p.sh\"; daemon_info mac","_",sys.argv[1]]))' "$S" -m motor daemon --home "$HOME/.motor" --intervalo 10

python3 -c 'import time; time.sleep(60)' -m motor daemon --home "$HOME/.motor" --intervalo 10 &
d=$!
sleep 1
bash -c '. "$1/p.sh"; daemon_info mac' _ "$S" >/dev/null 2>&1 || fallo "control: daemon_info no ve un daemon ajeno"
sleep 1
g checkout -q --detach "refs/tags/$ETIQUETA"
espera "daemon anterior al cambio no pasa" '!0' bash -c '. "$1/p.sh"; espera_daemon_en_etiqueta mac >/dev/null' _ "$S"
kill $d 2>/dev/null; wait $d 2>/dev/null

python3 -c 'import time; time.sleep(60)' -m motor daemon --home "$HOME/.motor" --intervalo 10 &
d=$!
sleep 1
espera "daemon posterior al cambio pasa" 0 bash -c '. "$1/p.sh"; espera_daemon_en_etiqueta mac >/dev/null' _ "$S"
kill $d 2>/dev/null; wait $d 2>/dev/null

termina
