#!/usr/bin/env bash
# ops/huellas.sh con un ssh falso que contesta lo que decidimos: ≠0 si una huella no coincide, si
# doctor no está ok o si el remoto falla; 0 si todo está bien.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

export MOTOR_MAC_SSH=mac@fake MOTOR_SSH_MINI=mini@fake MOTOR_SSH_PC=gwpc
R="$S/resp"

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
r="$S/resp/$host"
case "$*" in
  *pgrep*) cat "$r.pgrep" ;;
  *lstart*) echo "Mon Oct 19 10:00:00 2026" ;;
  *Get-CimInstance*) cat "$r.pgrep" ;;
  *getmtime*|*LastWriteTime*) cat "$r.mtime" ;;
  *rev-parse*) n=$(( $(cat "$r.n" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$r.n"
    if [ -f "$r.codigo.despues" ] && [ "$n" -gt 2 ]; then cat "$r.codigo.despues"; else cat "$r.codigo"; fi ;;
  *programadas*) cat "$r.programadas" ;;
  *como-servicio*) cat "$r.rutas"; exit "$(cat "$r.rutas.rc" 2>/dev/null || echo 0)" ;;
  *doctor*) cat "$r.doctor"; exit "$(cat "$r.doctor.rc" 2>/dev/null || echo 0)" ;;
  *version*json*) cat "$r.version" ;;
  *launchctl*|*"schtasks /query"*) exit "$(cat "$r.servicio.rc" 2>/dev/null || echo 0)" ;;
esac
exit 0
FAKE
chmod +x "$S/bin/ssh"

# Estado sano: las tres con la misma huella.
sano() {
  rm -rf "$R"; mkdir -p "$R"
  local h m
  for par in mac@fake:mac mini@fake:mini gwpc:pc; do
    h=${par%%:*} m=${par##*:}
    echo '{"version": "0.1.0", "sha": "aaaaaaaaaaaa1111"}' >"$R/$h.version"
    echo "{\"ok\": true, \"problemas\": [], \"rutas\": {}, \"maquina\": \"$m\", \"conf_sha\": \"cccccccccccc2222\"}" >"$R/$h.doctor"
    echo '4242 1792404000' >"$R/$h.pgrep"
    [ "$h" = gwpc ] || echo '4242 /usr/bin/python3 -m motor daemon --home /h' >"$R/$h.pgrep"
    echo 0 >"$R/$h.mtime"
    echo "$SHA_BUENO" >"$R/$h.codigo"
    echo '{"leidas": ["launchd"], "sin_leer": [], "hallazgos": []}' >"$R/$h.programadas"
    echo "{\"ok\": true, \"maquina\": \"$m\", \"problemas\": [], \"rutas\": {\"opus\": {\"responde\": true}, \"kimi\": {\"responde\": false}}}" >"$R/$h.rutas"
  done
  unset FAKE_CAE
}

# Una copia git del repo con su origin: la etiqueta de ops/lib.sh y el HEAD que debe correr el daemon.
mkdir -p "$S/fx"
cp -R "$RAIZ/ops" "$S/fx/ops"
git init -q -b main "$S/fx"
git -C "$S/fx" add -A
git -C "$S/fx" -c user.name=t -c user.email=t@t commit -q -m uno
SHA_BUENO="$(git -C "$S/fx" rev-parse HEAD)"
git -C "$S/fx" -c user.name=t -c user.email=t@t commit -q --allow-empty -m dos
SHA_VIEJO="$SHA_BUENO"
SHA_BUENO="$(git -C "$S/fx" rev-parse HEAD)"
git init -q --bare -b main "$S/fx-origen.git"
git -C "$S/fx" remote add origin "$S/fx-origen.git"
git -C "$S/fx" push -q origin main
TAG="$(unset MOTOR_ETIQUETA; . "$RAIZ/ops/lib.sh"; echo "$ETIQUETA")"
git -C "$S/fx" push -q origin "$SHA_BUENO:refs/tags/$TAG"
H="$S/fx/ops/huellas.sh"
export MOTOR_SHA="$SHA_BUENO" MOTOR_TOPE_DAEMON=2 MOTOR_ESPERA_PASO=1

sano; espera "todo coincide" 0 bash "$H"
sano; espera "todo coincide con --servicio --rutas" 0 bash "$H" --servicio --rutas
contiene "tabla" "$S/salida" "Las tres huellas coinciden"

# MOTOR 01: la PC contestó con una "ú" en cp1252 (byte 0xfa) y huellas.sh reventaba al leerla.
sano; printf '{"ok": true, "problemas": [], "rutas": {}, "maquina": "pc", "conf_sha": "cccccccccccc2222", "nota": "men\372"}' >"$R/gwpc.doctor"
espera "la PC contesta en cp1252" 0 bash "$H"

sano; echo '{"version": "0.1.0", "sha": "bbbbbbbbbbbb3333"}' >"$R/mini@fake.version"
espera "el sha del motor difiere en la Mini" '!0' bash "$H"
contiene "dice cuál" "$S/salida" "no coinciden"

sano; sed -i.bak 's/cccccccccccc2222/dddddddddddd4444/' "$R/gwpc.doctor"
espera "el sha de la conf difiere en la PC" '!0' bash "$H"

sano; echo '{"version": "0.2.0", "sha": "aaaaaaaaaaaa1111"}' >"$R/mac@fake.version"
espera "la versión difiere en la Mac" '!0' bash "$H"

sano; echo '{"ok": false, "problemas": ["ningún modelo del papel revisor puede correr aquí"], "rutas": {}, "maquina": "pc", "conf_sha": "cccccccccccc2222"}' >"$R/gwpc.doctor"; echo 1 >"$R/gwpc.doctor.rc"
espera "doctor no está ok en la PC" '!0' bash "$H"
contiene "dice el motivo" "$S/salida" "revisor"

sano; echo '{"ok": false, "problemas": ["maquina.toml no existe"], "rutas": {}}' >"$R/mini@fake.doctor"; echo 1 >"$R/mini@fake.doctor.rc"
espera "doctor sin conf cargada" '!0' bash "$H"

sano; sed -i.bak 's/"maquina": "mini"/"maquina": "mac"/' "$R/mini@fake.doctor"
espera "maquina.toml de otra máquina" '!0' bash "$H"

sano; echo 'Traceback: no module named motor' >"$R/gwpc.version"
espera "salida que no es JSON" '!0' bash "$H"

sano; FAKE_CAE="mini@fake" espera "ssh a la Mini falla" '!0' bash "$H"
contiene "dice cuál" "$S/salida" "mini"
sano; FAKE_CAE="gwpc" espera "ssh a la PC falla" '!0' bash "$H"

sano; echo 1 >"$R/mini@fake.servicio.rc"
espera "servicio no cargado no estorba sin --servicio" 0 bash "$H"
espera "servicio no cargado con --servicio" '!0' bash "$H" --servicio
contiene "dice el servicio" "$S/salida" "servicio"

sano; echo '{"ok": false, "maquina": "pc", "problemas": ["opus: tiempo: no contestó"], "rutas": {"opus": {"responde": false}}}' >"$R/gwpc.rutas"; echo 1 >"$R/gwpc.rutas.rc"
espera "ruta que no responde con --rutas" '!0' bash "$H" --rutas
espera "ruta que no responde sin --rutas" 0 bash "$H"

sano; echo '{"ok": true, "maquina": "mac", "problemas": [], "rutas": {}}' >"$R/mac@fake.rutas"
espera "--rutas sin ningún modelo que responda" '!0' bash "$H" --rutas

espera "argumento desconocido" '!0' bash "$H" --nada

sano; echo "$SHA_VIEJO" >"$R/mini@fake.codigo"
espera "el daemon sigue en el commit anterior" '!0' bash "$H"
contiene "dice cuál" "$S/salida" "mini: el motor daemon no arrancó"

sano; echo 99999999999 >"$R/gwpc.mtime"
espera "el daemon nació antes de que se pusiera el código" '!0' bash "$H"
contiene "dice cuál" "$S/salida" "pc: el motor daemon no arrancó"

sano; : >"$R/mac@fake.pgrep"
espera "no hay proceso motor daemon" '!0' bash "$H"

sano; git -C "$S/fx" push -q -f origin "$SHA_VIEJO:refs/tags/$TAG"
espera "la etiqueta apunta a otro sha" '!0' bash "$H"
contiene "dice la etiqueta" "$S/salida" "la etiqueta $TAG apunta a"
git -C "$S/fx" push -q -f origin "$SHA_BUENO:refs/tags/$TAG"

sano; echo '{"leidas": [], "sin_leer": ["launchd: negado"], "hallazgos": []}' >"$R/mini@fake.programadas"
espera "programadas sin ninguna tarea leída" '!0' bash "$H"
contiene "dice programadas" "$S/salida" "mini: programadas"

sano; echo 'no es json' >"$R/mac@fake.programadas"
espera "programadas que no es JSON" '!0' bash "$H"

sano
espera "sin MOTOR_SHA dentro de una copia git usa su HEAD" 0 env -u MOTOR_SHA bash "$H"
sano; echo "$SHA_VIEJO" >"$R/mini@fake.codigo"
espera "sin MOTOR_SHA el daemon se compara con el HEAD" '!0' env -u MOTOR_SHA bash "$H"
contiene "nombra el HEAD" "$S/salida" "mini: el motor daemon no arrancó con el código de ${SHA_BUENO:0:12}"
git -C "$S/fx" -c user.name=t -c user.email=t@t commit -q --allow-empty -m tres
sano; espera "sin MOTOR_SHA con el HEAD adelante de la etiqueta" '!0' env -u MOTOR_SHA bash "$H"
git -C "$S/fx" reset -q --hard "$SHA_BUENO"
sano; echo "$SHA_VIEJO" >"$R/mini@fake.codigo"; echo "$SHA_BUENO" >"$R/mini@fake.codigo.despues"
MOTOR_TOPE_DAEMON=30 espera "el daemon llega al sha después de unas vueltas" 0 bash "$H"
sano
MOTOR_SHA="$SHA_VIEJO" espera "MOTOR_SHA de un commit anterior al que corre el daemon" '!0' bash "$H"
contiene "dice cuál" "$S/salida" "el motor daemon no arrancó"
MOTOR_TOPE_DAEMON=mucho espera "tope del daemon que no es número" 2 bash "$H"
contiene "dice cuál" "$S/salida" "MOTOR_TOPE_DAEMON"
sano; mkdir -p "$S/suelto/ops" && cp "$RAIZ/ops/huellas.sh" "$RAIZ/ops/lib.sh" "$S/suelto/ops/"
espera "sin MOTOR_SHA y fuera de git" 2 env -u MOTOR_SHA bash "$S/suelto/ops/huellas.sh"
contiene "dice cómo darlo" "$S/salida" 'falta MOTOR_SHA: corre MOTOR_SHA=$(git rev-parse HEAD) bash ops/huellas.sh'

termina
