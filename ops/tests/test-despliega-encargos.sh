#!/usr/bin/env bash
# ops/despliega-encargos.sh con un ssh falso, un HOME temporal por máquina y árboles
# git temporales. La PC de mentira aplica el tope de cmd.exe (8191) y contesta CRLF.
# Nada de esto toca las máquinas reales ni ~/.motor-encargos real.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=mini MOTOR_SSH_PC=gwpc
export MOTOR_PC_PYTHON='C:\runtime\python.exe'
export MOTOR_ESPERA_DESPLIEGUE=0 MOTOR_ESPERA_ARRANQUE_PC=5 MOTOR_ESPERA_PARADA_PC=5
export FAKE_PID=$$
# El python3 del PATH de ssh en la Mini es el del sistema (3.9) y no puede
# importar tomllib. Este doble falla igual cuando se lo usa para `-m motor`,
# pero sigue sirviendo para leer el plist y para los programas de stdlib.
REAL_PY="$(command -v python3)"
mkdir -p "$S/bin" "$S/bin-ssh"
cat >"$S/bin-ssh/python3" <<PY
#!/usr/bin/env bash
for a in "\$@"; do
  if [ "\$a" = motor ]; then
    echo "ModuleNotFoundError: No module named 'tomllib'" >&2
    exit 1
  fi
done
exec "$REAL_PY" "\$@"
PY
chmod +x "$S/bin-ssh/python3"

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
    rm -rf "$home/motor-sucesor" "$home/.motor-encargos" "$home/.motor" "$home/.kickstarts" "$home/.ordenes" "$home/.pasada-en"
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
# cmd.exe y Python en Windows: tope de línea de comandos y fin de línea CRLF.
crlf() { sed 's/\r$//' | sed 's/$/\r/'; }
pasada() { # <home>
  sha="$(git -C "$1/motor-sucesor" rev-parse HEAD 2>/dev/null || true)"
  printf '{"sha": "%s", "ruta": "%s", "hora": "2026-11-09T10:00:00Z", "completa": true, "encargos": 0, "pid": %s}\n' \
    "$sha" "$1/motor-sucesor" "$FAKE_PID" >"$1/.motor-encargos/supervisor-pasada.json"
}
# La pasada nueva no aparece hasta que pasa el drenaje que pidió el reinicio:
# launchd relanza y el supervisor nuevo completa su primera pasada después.
aplica_pasada() { # <home>
  local marca="$1/.pasada-en" cuando
  [ -f "$marca" ] || return 0
  cuando="$(cat "$marca" 2>/dev/null)"
  [ -n "$cuando" ] || return 0
  [ "$(date +%s)" -ge "$cuando" ] || return 0
  rm -f "$marca"
  pasada "$1"
}
case "$host" in
  gwpc)
    phome="$S/pc-home"
    mkdir -p "$phome/.motor-encargos"
    cmd="$*"
    if [ "${#cmd}" -gt 8191 ]; then
      # cmd.exe falla pero devuelve 0: quien llamó no puede tomar esto por éxito.
      printf 'The command line is too long.\r\n'
      exit 0
    fi
    consume_orden_pc() {
      [ -f "$phome/.motor-encargos/reiniciar-supervisor.json" ] || return 0
      rm -f "$phome/.motor-encargos/reiniciar-supervisor.json"
      if [ -n "${FAKE_PC_ARRANQUE_FALLA:-}" ]; then
        echo "el supervisor de la PC no arrancó" >&2
        exit 1
      fi
      printf 'orden\n' >>"$phome/.ordenes"
      pasada "$phome"
    }
    case "$cmd" in
      *iniciar_encargos_pc.py*--parar*)
        : >"$phome/.motor-encargos/vigilante.parar"
        if [ -n "${FAKE_PC_SIN_VIGILANTE:-}" ]; then printf '{}\n' | crlf; else printf '{"parado": true}\n' | crlf; fi
        exit 0 ;;
      *iniciar_encargos_pc.py*--estado*)
        if [ -n "${FAKE_PC_SIN_VIGILANTE:-}" ] && [ ! -f "$phome/.motor-encargos/arrancado" ]; then
          printf '{}\n' | crlf
        elif [ -f "$phome/.motor-encargos/vigilante.parar" ]; then
          printf '{"parado": true}\n' | crlf
        else
          printf '{"supervisor_pid": 4242, "supervisor": {"sid": "S-1-5-21", "session": 1, "in_job": false}, "parado": false}\n' | crlf
        fi
        exit 0 ;;
      *'iniciar_encargos_pc.py'*)
        if [ -n "${FAKE_PC_ARRANQUE_FALLA:-}" ]; then
          echo "el supervisor de la PC no arrancó" >&2
          exit 1
        fi
        rm -f "$phome/.motor-encargos/vigilante.parar"
        : >"$phome/.motor-encargos/arrancado"
        pasada "$phome"
        printf '{"vigilante": "arrancado"}\n' | crlf
        exit 0 ;;
      *' -c "'*)
        tmp="${cmd#* -c \"}"
        code="${tmp%%\"*}"
        rest="${tmp#*\"}"
        salida="$(HOME="$phome" USERPROFILE="$phome" python3 -c "$code" $rest)"
        rc=$?
        consume_orden_pc
        [ -n "$salida" ] && printf '%s\n' "$salida" | crlf
        exit $rc ;;
      *'git -C '*'checkout'*)
        if [ "${FAKE_CHECKOUT_FALLA:-}" = gwpc ]; then
          echo "fatal: checkout cortado" >&2
          exit 1
        fi
        salida="$(bash -c "$cmd")"; rc=$?
        printf '%s\n' "$salida" | crlf
        exit $rc ;;
      *'git -C '*)
        salida="$(bash -c "$cmd")"; rc=$?
        printf '%s\n' "$salida" | crlf
        exit $rc ;;
      *) echo "comando no reconocido por la PC: $*" >&2; exit 1 ;;
    esac
    ;;
  mac|mini)
    aplica_pasada "$S/$host"
    case "$*" in
      *'bash -s'*)
        entrada="$S/entrada-$$"
        cat >"$entrada"
        if [ -n "${FAKE_CHECKOUT_FALLA:-}" ] && [ "$FAKE_CHECKOUT_FALLA" = "$host" ] && grep -q checkout "$entrada"; then
          echo "fatal: checkout cortado" >&2
          rm -f "$entrada"
          exit 1
        fi
        PATH="$S/bin-ssh:$PATH" HOME="$S/$host" bash -c "$*" <"$entrada"
        rc=$?
        rm -f "$entrada"
        if [ "$rc" -eq 0 ] && [ -f "$S/$host/.motor-encargos/reiniciar-supervisor.json" ]; then
          tope="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("tope_s"))' \
            "$S/$host/.motor-encargos/reiniciar-supervisor.json" 2>/dev/null || echo '?')"
          printf 'orden %s\n' "$tope" >>"$S/$host/.ordenes"
          rm -f "$S/$host/.motor-encargos/reiniciar-supervisor.json"
          printf '%s\n' "$(( $(date +%s) + ${FAKE_DRENA:-0} ))" >"$S/$host/.pasada-en"
        fi
        exit $rc ;;
      *)
        PATH="$S/bin-ssh:$PATH" HOME="$S/$host" bash -c "$*" ;;
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
  [ -s "$S/$m/.ordenes" ] || fallo "$m: no se pidió el reinicio ordenado"
  [ ! -s "$S/$m/.kickstarts" ] || fallo "$m: usó kickstart en vez del reinicio ordenado"
done
grep -qF 'C:\runtime\python.exe' "$FAKE_LOG" || fallo "la PC no usó el intérprete configurado"
grep -q 'uv' "$FAKE_LOG" && fallo "la PC usó uv"
grep -q -- '--parar' "$FAKE_LOG" && fallo "la PC se paró a mano; el reinicio ordenado no lo necesita"
grep -q 'taskkill' "$FAKE_LOG" && fallo "la PC usó taskkill en vez del reinicio ordenado"
grep -q 'The command line is too long' "$S/salida" && fallo "un programa grande viajó por la línea de comandos de la PC"

echo "# segunda corrida con el mismo SHA"
: >"$FAKE_LOG"
for m in mac mini pc-home; do rm -f "$S/$m/.ordenes" "$S/$m/.kickstarts"; done
espera "no reinicia lo que ya está bien" 0 bash "$SCRIPT" --sha "$SHA"
contiene "dice que ya estaba" "$S/salida" "ya estaba en"
for m in mac mini pc-home; do
  [ ! -s "$S/$m/.ordenes" ] || fallo "$m: se reinició en la segunda corrida"
  [ ! -s "$S/$m/.kickstarts" ] || fallo "$m: usó kickstart en la segunda corrida"
done
grep -q -- '--parar' "$FAKE_LOG" && fallo "la PC se paró en la segunda corrida"

echo "# un SHA que no está en origin/main"
reset_maquinas
espera "se niega antes de tocar nada" '!0' bash "$SCRIPT" --sha "$LADO"
contiene "la causa lo dice" "$S/salida" "no está en origin/main"
[ ! -s "$FAKE_LOG" ] || fallo "tocó las máquinas antes de negarse"
[ "$(git -C "$S/mac/motor-sucesor" rev-parse HEAD)" = "$ANTERIOR" ] || fallo "movió la Mac al negarse"

echo "# la lectura de la PC falla antes de mover: no se mueve ninguna"
reset_maquinas
espera "se niega antes de mover" '!0' env FAKE_CAE=gwpc bash "$SCRIPT" --sha "$SHA"
contiene "nombra la PC y el paso" "$S/salida" "FALLA pc en leer el árbol del supervisor"
contiene "dice que no movió ninguna" "$S/salida" "No moví ninguna máquina"
for m in mac mini pc-home; do
  [ "$(git -C "$S/$m/motor-sucesor" rev-parse HEAD)" = "$ANTERIOR" ] || fallo "$m: se movió pese al fallo de lectura de la PC"
  [ ! -s "$S/$m/.ordenes" ] || fallo "$m: se reinició pese al fallo de lectura de la PC"
  [ ! -e "$S/$m/.motor-encargos/instalacion.json" ] || fallo "$m: escribió recibo antes de comprobar todas"
done

echo "# un error de cmd.exe con rc=0 no se toma por éxito"
reset_maquinas
largo="C:\\$(printf 'x%.0s' {1..8200})\\python.exe"
espera "se niega ante el error de cmd.exe" '!0' env MOTOR_PC_PYTHON="$largo" bash "$SCRIPT" --sha "$SHA"
contiene "nombra la PC y la lectura" "$S/salida" "FALLA pc en leer el árbol del supervisor"
contiene "dice que no movió ninguna" "$S/salida" "No moví ninguna máquina"
for m in mac mini pc-home; do
  [ "$(git -C "$S/$m/motor-sucesor" rev-parse HEAD)" = "$ANTERIOR" ] || fallo "$m: se movió con la PC devolviendo el error"
done

echo "# una máquina falla a la mitad"
reset_maquinas
espera "se detiene y nombra la máquina y el paso" '!0' env FAKE_CHECKOUT_FALLA=mini bash "$SCRIPT" --sha "$SHA"
contiene "nombra la mini y el paso" "$S/salida" "FALLA mini en mover el árbol"
contiene "dice que reinició la Mac movida" "$S/salida" "mac: reiniciado y comprobado"
[ "$(campo_json "$S/mini/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el pendiente de la mini no conserva el anterior"
[ "$(git -C "$S/pc-home/motor-sucesor" rev-parse HEAD)" = "$ANTERIOR" ] \
  || fallo "siguió con la PC después de la falla de la mini"
[ "$(git -C "$S/mac/motor-sucesor" rev-parse HEAD)" = "$SHA" ] \
  || fallo "la Mac no quedó desplegada antes de la falla"
[ -s "$S/mac/.ordenes" ] || fallo "la Mac movida no se reinició"
[ ! -s "$S/mini/.ordenes" ] || fallo "la mini se reinició sin haber movido su árbol"

echo "# falla la PC después de mover la Mac y la Mini"
reset_maquinas
espera "se detiene y compensa" '!0' env FAKE_CHECKOUT_FALLA=gwpc bash "$SCRIPT" --sha "$SHA"
contiene "nombra la PC y el paso" "$S/salida" "FALLA pc en mover el árbol"
contiene "dice que reinició la Mac" "$S/salida" "mac: reiniciado y comprobado"
contiene "dice que reinició la Mini" "$S/salida" "mini: reiniciado y comprobado"
for m in mac mini; do
  [ "$(git -C "$S/$m/motor-sucesor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: no quedó en el SHA"
  [ -s "$S/$m/.ordenes" ] || fallo "$m: no se reinició tras la falla de la PC"
done
[ "$(git -C "$S/pc-home/motor-sucesor" rev-parse HEAD)" = "$ANTERIOR" ] || fallo "la PC se movió pese a la falla"
[ "$(campo_json "$S/pc-home/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el pendiente de la PC no conserva el anterior"
[ ! -s "$S/pc-home/.ordenes" ] || fallo "la PC se reinició sin haber movido su árbol"

echo "# el reinicio ordenado usa el intérprete del LaunchAgent, no el python3 de ssh"
reset_maquinas
espera "despliega aunque el python3 de ssh no tenga tomllib" 0 bash "$SCRIPT" --sha "$SHA" --maquina mac
contiene "lo dice" "$S/salida" "desplegado en:mac"
[ -s "$S/mac/.ordenes" ] || fallo "el reinicio ordenado no llegó a la orden"
grep -q 'tomllib' "$S/salida" && fallo "el reinicio usó el python3 de ssh y falló por tomllib"

echo "# el tope del drenaje deja margen sobre la espera de la comprobación"
reset_maquinas
espera "despliega con el tope y la espera coherentes" 0 \
  env MOTOR_ESPERA_DESPLIEGUE= bash "$SCRIPT" --sha "$SHA" --maquina mac
tope_drenaje="$(sed -n 's/^orden //p' "$S/mac/.ordenes" | head -n 1)"
espera_s="$(sed -n 's/.*hasta \([0-9][0-9]*\)s.*/\1/p' "$S/salida" | head -n 1)"
[ -n "$tope_drenaje" ] || fallo "el reinicio no dijo su tope de drenaje"
[ -n "$espera_s" ] || fallo "el despliegue no dijo su espera de comprobación"
[ "$espera_s" -gt "$tope_drenaje" ] \
  || fallo "la espera (${espera_s} s) no deja margen sobre el drenaje (${tope_drenaje} s)"
contiene "lo dice" "$S/salida" "desplegado en:mac"

echo "# un drenaje que agota su tope aún cabe en la espera con margen"
reset_maquinas
espera "despliega tras un drenaje que agota el tope" 0 \
  env MOTOR_ESPERA_DESPLIEGUE=6 MOTOR_MARGEN_DESPLEGUE=4 FAKE_DRENA=5 \
  bash "$SCRIPT" --sha "$SHA" --maquina mac
tope_drenaje="$(sed -n 's/^orden //p' "$S/mac/.ordenes" | head -n 1)"
espera_s="$(sed -n 's/.*hasta \([0-9][0-9]*\)s.*/\1/p' "$S/salida" | head -n 1)"
[ "$tope_drenaje" = 2 ] || fallo "el drenaje no se recortó al margen (tope ${tope_drenaje} s)"
[ "$espera_s" -gt "$tope_drenaje" ] \
  || fallo "la espera (${espera_s} s) no deja margen sobre el drenaje (${tope_drenaje} s)"
contiene "la espera suma el margen" "$S/salida" "drenaje hasta 2s, espera hasta 6s"
contiene "lo dice" "$S/salida" "desplegado en:mac"

echo "# un reintento tras una falla conserva el anterior"
reset_maquinas
espera "la primera corrida falla en la PC" '!0' env FAKE_PC_ARRANQUE_FALLA=1 bash "$SCRIPT" --sha "$SHA"
contiene "nombra la PC y el reinicio" "$S/salida" "FALLA pc en reiniciar el supervisor"
contiene "dice el anterior que conserva" "$S/salida" "conserva el anterior (${ANTERIOR:0:12})"
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

echo "# la PC sin vigilante se arranca y no culpa a la parada"
reset_maquinas
espera "despliega sin vigilante en la PC" 0 env FAKE_PC_SIN_VIGILANTE=1 bash "$SCRIPT" --sha "$SHA"
contiene "lo dice" "$S/salida" "desplegado en:mac mini pc"
[ "$(git -C "$S/pc-home/motor-sucesor" rev-parse HEAD)" = "$SHA" ] || fallo "la PC no quedó en el SHA"
[ -f "$S/pc-home/.motor-encargos/arrancado" ] || fallo "la PC no arrancó el vigilante"
grep -q -- '--parar' "$FAKE_LOG" && fallo "la PC pasó por --parar"

echo "# un fallo temprano del reintento no borra el anterior"
reset_maquinas
espera "la primera corrida falla en la PC" '!0' env FAKE_PC_ARRANQUE_FALLA=1 bash "$SCRIPT" --sha "$SHA"
rm -rf "$S/mac/motor-sucesor"
espera "el reintento se niega por la Mac" '!0' bash "$SCRIPT" --sha "$SHA"
contiene "nombra la Mac y el paso" "$S/salida" "FALLA mac en leer el árbol del supervisor"
[ "$(campo_json "$S/mac/.motor-encargos/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$ANTERIOR" ] \
  || fallo "el fallo temprano borró el anterior que se quería conservar"

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
