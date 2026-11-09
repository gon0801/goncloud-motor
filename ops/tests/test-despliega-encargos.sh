#!/usr/bin/env bash
# ops/despliega-encargos.sh con un ssh falso, un HOME temporal por máquina y árboles
# git temporales. Nada de esto toca las máquinas reales ni ~/.motor-encargos real.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=mini MOTOR_SSH_PC=gwpc
export MOTOR_PC_PYTHON='C:\runtime\python.exe'
export MOTOR_ESPERA_DESPLIEGUE=0 MOTOR_ESPERA_ARRANQUE_PC=5 MOTOR_ESPERA_PARADA_PC=5
export FAKE_PID=$$
mkdir -p "$S/bin"

# --- un origen con main en SHA, un anterior y un lado que no está en main.
git init -q -b main "$S/fuente"
printf 'uno\n' >"$S/fuente/README.md"
git -C "$S/fuente" add -A
git -C "$S/fuente" -c user.name=t -c user.email=t@t commit -q -m uno
ANTERIOR="$(git -C "$S/fuente" rev-parse HEAD)"
cp -R "$RAIZ/motor" "$S/fuente/motor"
printf 'dos\n' >>"$S/fuente/README.md"
git -C "$S/fuente" add -A
git -C "$S/fuente" -c user.name=t -c user.email=t@t commit -q -m dos
SHA="$(git -C "$S/fuente" rev-parse HEAD)"
git -C "$S/fuente" checkout -q -b lado "$ANTERIOR"
printf 'lado\n' >"$S/fuente/LADO.md"
git -C "$S/fuente" add -A
git -C "$S/fuente" -c user.name=t -c user.email=t@t commit -q -m lado
LADO="$(git -C "$S/fuente" rev-parse HEAD)"
git -C "$S/fuente" checkout -q main
git clone -q --bare "$S/fuente" "$S/origen.git"
git clone -q "$S/origen.git" "$S/trabajo"
export MOTOR_DESPLEGAR_RAIZ="$S/trabajo"

crear_plist() { # <home>
  mkdir -p "$1/Library/LaunchAgents"
  cat >"$1/Library/LaunchAgents/ai.goncloud.motor-encargos.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>ai.goncloud.motor-encargos</string>
<key>ProgramArguments</key><array><string>$(command -v python3)</string><string>-m</string><string>motor</string><string>supervisor</string></array>
<key>WorkingDirectory</key><string>$1/motor-sucesor</string>
</dict></plist>
PLIST
}

reset_maquinas() {
  local m home
  for m in mac mini pc-home; do
    home="$S/$m"
    rm -rf "$home/motor-sucesor" "$home/.motor-encargos" "$home/.motor" "$home/.kickstarts"
    mkdir -p "$home/.motor-encargos"
    git clone -q "$S/origen.git" "$home/motor-sucesor"
    git -C "$home/motor-sucesor" checkout -q --detach "$ANTERIOR"
    crear_plist "$home"
  done
  : >"$FAKE_LOG"
}
reset_maquinas

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_LOG"
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
case "$host" in
  gwpc)
    phome="$S/pc-home"
    mkdir -p "$phome/.motor-encargos"
    cmd="$*"
    case "$cmd" in
      *iniciar_encargos_pc.py*--parar*)
        : >"$phome/.motor-encargos/vigilante.parar"
        printf '{"parado": true}\n'
        exit 0 ;;
      *iniciar_encargos_pc.py*--estado*)
        if [ -f "$phome/.motor-encargos/vigilante.parar" ]; then
          printf '{"parado": true}\n'
        else
          printf '{"supervisor_pid": 4242, "supervisor": {"sid": "S-1-5-21", "session": 1, "in_job": false}, "parado": false}\n'
        fi
        exit 0 ;;
      *'iniciar_encargos_pc.py'*)
        if [ -n "${FAKE_PC_ARRANQUE_FALLA:-}" ]; then
          echo "el supervisor de la PC no arrancó" >&2
          exit 1
        fi
        rm -f "$phome/.motor-encargos/vigilante.parar"
        sha="$(git -C "$phome/motor-sucesor" rev-parse HEAD 2>/dev/null || true)"
        printf '{"sha": "%s", "ruta": "%s", "hora": "2026-11-09T10:00:00Z", "completa": true, "encargos": 0, "pid": %s}\n' \
          "$sha" "$phome/motor-sucesor" "$FAKE_PID" >"$phome/.motor-encargos/supervisor-pasada.json"
        printf '{"vigilante": "arrancado"}\n'
        exit 0 ;;
      *' -c "'*)
        tmp="${cmd#* -c \"}"
        code="${tmp%%\"*}"
        rest="${tmp#*\"}"
        HOME="$phome" USERPROFILE="$phome" python3 -c "$code" $rest
        exit $? ;;
      *'git -C '*)
        bash -c "$cmd" ;;
      *) echo "comando no reconocido por la PC: $*" >&2; exit 1 ;;
    esac
    ;;
  mac|mini)
    case "$*" in
      *'bash -s'*)
        entrada="$S/entrada-$$"
        cat >"$entrada"
        if [ -n "${FAKE_CHECKOUT_FALLA:-}" ] && [ "$FAKE_CHECKOUT_FALLA" = "$host" ] && grep -q checkout "$entrada"; then
          echo "fatal: checkout cortado" >&2
          rm -f "$entrada"
          exit 1
        fi
        HOME="$S/$host" bash -c "$*" <"$entrada"
        rc=$?
        rm -f "$entrada"
        exit $rc ;;
      *)
        HOME="$S/$host" bash -c "$*" ;;
    esac ;;
  *) exit 1 ;;
esac
FAKE
chmod +x "$S/bin/ssh"

cat >"$S/bin/launchctl" <<'FAKE'
#!/usr/bin/env bash
case "$1" in
  print) exit 0 ;;
  bootstrap) exit 0 ;;
  kickstart)
    echo kickstart >>"$HOME/.kickstarts"
    sha="$(git -C "$HOME/motor-sucesor" rev-parse HEAD 2>/dev/null || true)"
    mkdir -p "$HOME/.motor-encargos"
    printf '{"sha": "%s", "ruta": "%s", "hora": "2026-11-09T10:00:00Z", "completa": true, "encargos": 0, "pid": %s}\n' \
      "$sha" "$HOME/motor-sucesor" "$FAKE_PID" >"$HOME/.motor-encargos/supervisor-pasada.json"
    exit 0 ;;
  *) exit 0 ;;
esac
FAKE
chmod +x "$S/bin/launchctl"

campo_json() { # <archivo> <expresión>
  python3 -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); print($2)" "$1"
}

SCRIPT="$RAIZ/ops/despliega-encargos.sh"
COMPRUEBA="$RAIZ/ops/comprueba-despliegue.sh"

echo "# despliegue bueno en las tres"
espera "despliega en las tres" 0 bash "$SCRIPT" --sha "$SHA"
contiene "lo dice" "$S/salida" "desplegado en:mac mini pc"
for m in mac mini pc-home; do
  [ "$(git -C "$S/$m/motor-sucesor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: el árbol no quedó en el SHA"
  [ "$(campo_json "$S/$m/.motor-encargos/instalacion.json" 'd["destino"]["codigo"]')" = "$SHA" ] \
    || fallo "$m: el recibo no dice el SHA"
  [ ! -e "$S/$m/.motor-encargos/instalacion-pendiente.json" ] || fallo "$m: no se cerró el pendiente"
done
[ -s "$S/mac/.kickstarts" ] || fallo "la Mac no reinició el LaunchAgent"
[ -s "$S/mini/.kickstarts" ] || fallo "la Mini no reinició el LaunchAgent"
grep -qF 'C:\runtime\python.exe' "$FAKE_LOG" || fallo "la PC no usó el intérprete configurado"
grep -q 'uv' "$FAKE_LOG" && fallo "la PC usó uv"
grep -q -- '--parar' "$FAKE_LOG" || fallo "la PC no se paró antes de mover el árbol"

echo "# segunda corrida con el mismo SHA"
: >"$FAKE_LOG"
: >"$S/mac/.kickstarts"
: >"$S/mini/.kickstarts"
espera "no reinicia lo que ya está bien" 0 bash "$SCRIPT" --sha "$SHA"
contiene "dice que ya estaba" "$S/salida" "ya estaba en"
[ ! -s "$S/mac/.kickstarts" ] || fallo "la Mac se reinició en la segunda corrida"
[ ! -s "$S/mini/.kickstarts" ] || fallo "la Mini se reinició en la segunda corrida"
grep -q -- '--parar' "$FAKE_LOG" && fallo "la PC se paró en la segunda corrida"

echo "# un SHA que no está en origin/main"
reset_maquinas
espera "se niega antes de tocar nada" '!0' bash "$SCRIPT" --sha "$LADO"
contiene "la causa lo dice" "$S/salida" "no está en origin/main"
[ ! -s "$FAKE_LOG" ] || fallo "tocó las máquinas antes de negarse"
[ "$(git -C "$S/mac/motor-sucesor" rev-parse HEAD)" = "$ANTERIOR" ] || fallo "movió la Mac al negarse"

echo "# una máquina falla a la mitad"
reset_maquinas
espera "se detiene y nombra la máquina y el paso" '!0' env FAKE_CHECKOUT_FALLA=mini bash "$SCRIPT" --sha "$SHA"
contiene "nombra la mini y el paso" "$S/salida" "FALLA mini en mover el árbol"
[ "$(campo_json "$S/mini/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el pendiente de la mini no conserva el anterior"
[ "$(git -C "$S/pc-home/motor-sucesor" rev-parse HEAD)" = "$ANTERIOR" ] \
  || fallo "siguió con la PC después de la falla de la mini"
[ "$(git -C "$S/mac/motor-sucesor" rev-parse HEAD)" = "$SHA" ] \
  || fallo "la Mac no quedó desplegada antes de la falla"

echo "# un reintento tras una falla conserva el anterior"
reset_maquinas
espera "la primera corrida falla en la PC" '!0' env FAKE_PC_ARRANQUE_FALLA=1 bash "$SCRIPT" --sha "$SHA"
contiene "nombra la PC y el reinicio" "$S/salida" "FALLA pc en reiniciar el supervisor"
[ "$(campo_json "$S/pc-home/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el pendiente de la PC no conserva el anterior tras la primera falla"
espera "la segunda corrida vuelve a fallar" '!0' env FAKE_PC_ARRANQUE_FALLA=1 bash "$SCRIPT" --sha "$SHA"
contiene "dice el anterior que conserva" "$S/salida" "conserva el anterior (${ANTERIOR:0:12})"
[ "$(campo_json "$S/pc-home/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el reintento de la PC perdió el anterior"
[ "$(campo_json "$S/mac/.motor-encargos/instalacion.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el reintento de la Mac perdió el anterior en el recibo"
[ "$(campo_json "$S/mini/.motor-encargos/instalacion.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el reintento de la Mini perdió el anterior en el recibo"
[ "$(campo_json "$S/mac/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el reintento de la Mac perdió el anterior en el pendiente"
[ "$(campo_json "$S/mini/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el reintento de la Mini perdió el anterior en el pendiente"

echo "# el comprobador no cae al recibo del motor viejo"
reset_maquinas
git -C "$S/mac/motor-sucesor" checkout -q --detach "$SHA"
mkdir -p "$S/mac/.motor"
printf '{"destino": {"codigo": "%s"}}\n' "$SHA" >"$S/mac/.motor/instalacion.json"
printf '{"sha": "%s", "ruta": "%s", "hora": "2026-11-09T10:00:00Z", "completa": true, "encargos": 0, "pid": %s}\n' \
  "$SHA" "$S/mac/motor-sucesor" "$FAKE_PID" >"$S/mac/.motor-encargos/supervisor-pasada.json"
espera "sin recibo propio falla" '!0' bash "$COMPRUEBA" --sha "$SHA" --maquina mac
contiene "la causa nombra el recibo que falta" "$S/salida" "recibo en -"

echo "# el registro tiene que plegar entero"
python3 - "$S/mac/.motor-encargos/registro.sqlite" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute(
    "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
    " encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
)
db.execute(
    "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
    ("2026-11-09", "enc_prueba", "basura", "{}", "k1"),
)
db.commit()
PY
printf '{"destino": {"codigo": "%s"}}\n' "$SHA" >"$S/mac/.motor-encargos/instalacion.json"
espera "un encargo que no pliega entero falla" '!0' bash "$COMPRUEBA" --sha "$SHA" --maquina mac
contiene "la causa nombra el registro" "$S/salida" "el registro no pliega entero"

termina
