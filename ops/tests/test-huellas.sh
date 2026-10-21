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
    echo "{\"ok\": true, \"maquina\": \"$m\", \"problemas\": [], \"rutas\": {\"opus\": {\"responde\": true}, \"kimi\": {\"responde\": false}}}" >"$R/$h.rutas"
  done
  unset FAKE_CAE
}
H="$RAIZ/ops/huellas.sh"

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

termina
