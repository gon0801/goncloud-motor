#!/usr/bin/env bash
# ops/senal-kuma.sh, senal-kuma-checklist.sh y senal-kuma-rollback.sh con ssh y docker falsos y una
# base SQLite con las tablas reales de Kuma (kuma-esquema.sql). La Mini corre su script en un HOME
# falso; con pwsh, la PC corre el PowerShell de verdad con un USERPROFILE falso; sin pwsh,
# un sustituto en Python prueba todo menos la escritura de la PC.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc MOTOR_SSH_SERVIDOR=goncloud
export MOTOR_KUMA_DB="$S/kuma.db" MOTOR_ESPERA_PASO=1 MOTOR_KUMA_TOPE=2
export HOME_MINI="$S/mini" HOME_PC="$S/pc"
mkdir -p "$HOME_MINI/.motor" "$HOME_PC/.motor"
cat >"$S/escribe-pc.py" <<'PYEOF'
import sys
from pathlib import Path
f, accion, base = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
tok = sys.stdin.readline().strip() if accion == "poner" else ""
lineas = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
lineas = [l for l in lineas if not l.lstrip().startswith("MOTOR_KUMA_URL=")]
if accion == "poner":
    lineas.append("MOTOR_KUMA_URL=" + base + tok)
f.parent.mkdir(parents=True, exist_ok=True)
f.write_text("\n".join(lineas) + ("\n" if lineas else ""), encoding="utf-8")
PYEOF
TODO="$S/todo.out"
: >"$TODO"

cat >"$S/bin/docker" <<'FAKE'
#!/usr/bin/env bash
echo "docker $*" >>"$FAKE_LOG"
case "$1" in
  restart) exit 0 ;;
  inspect) echo "${FAKE_KUMA_CORRE:-true}"; exit 0 ;;
  *) echo "docker: no se espera '$*'" >>"$FAKE_LOG"; echo "docker: no se espera '$*'" >&2; exit 1 ;;
esac
FAKE
cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
doctor() { # doctor <archivo> <ansi|utf8>: lo que diría `motor doctor --json` según secretos.env.
  # ansi: la PC real escribe la ñ en su página ANSI (cp1252, medido en MOTOR 01), salvo con -X utf8.
  local json
  if grep -q '^MOTOR_KUMA_URL=.' "$1" 2>/dev/null; then json='{"senal": "señal a Kuma: configurada"}'; else json='{"senal": "señal a Kuma: sin configurar"}'; fi
  if [ "$2" = ansi ]; then
    printf '%s\n' "$json" | python3 -I -c 'import sys; sys.stdout.buffer.write(sys.stdin.read().encode("cp1252"))'
  else
    printf '%s\n' "$json"
  fi
}
case "$host" in
  goncloud) exec bash -c "$*" ;;
  gon@mini-fake)
    case "$*" in *doctor*) cat >/dev/null; doctor "$HOME_MINI/.motor/secretos.env" utf8; exit 0 ;; esac
    HOME="$HOME_MINI" exec bash -c "$*" ;;
  gwpc)
    case "$*" in
      *doctor*)
        case "$*" in
          *"-X utf8"*) doctor "$HOME_PC/.motor/secretos.env" utf8 ;;
          *) doctor "$HOME_PC/.motor/secretos.env" ansi ;;
        esac
        exit 0 ;;
      "powershell "*)
        if ! command -v pwsh >/dev/null 2>&1; then
          _script="$(printf '%s' "${*##* }" | python3 -I -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode("utf-16-le"))')"
          _accion="$(printf '%s\n' "$_script" | sed -n "s/.*accion = '\([^']*\)'.*/\1/p" | head -1)"
          _base="$(printf '%s\n' "$_script" | sed -n "s/.*MOTOR_KUMA_URL=\([^']*\)'.*/\1/p" | grep -F http | head -1)"
          exec python3 -I "$S/escribe-pc.py" "$HOME_PC/.motor/secretos.env" "$_accion" "$_base"
        fi
        python3 -I -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode("utf-16-le"))' "${*##* }" >>"$FAKE_LOG"
        USERPROFILE="$HOME_PC" exec pwsh ${1#powershell } ;;
    esac
    echo "ssh falso: no se espera '$*'" >&2; exit 1 ;;
esac
FAKE
chmod +x "$S/bin/docker" "$S/bin/ssh"

crea_base() {
  rm -f "$S"/kuma.db*
  python3 -I - "$S/kuma.db" "$RAIZ/ops/tests/kuma-esquema.sql" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.executescript(open(sys.argv[2]).read())
# Dos avisos activos y con id distinto: con uno solo, conectar al aviso equivocado no puede fallar.
con.execute("INSERT INTO notification (id, name, active, user_id, is_default, config) VALUES (0, 'Telegram', 1, 1, 1, '{}')")
con.execute("INSERT INTO notification (id, name, active, user_id, is_default, config) VALUES (1, 'Server Alerts', 1, 1, 0, '{}')")
con.execute(
    "INSERT INTO monitor (id, name, user_id, type, interval, retry_interval, maxretries, push_token, accepted_statuscodes_json, timeout, description) "
    "VALUES (26, 'backup orbit', 1, 'push', 90000, 3600, 1, 'ORBITTOKENDEPRUEBA', '[\"200-299\"]', 48, 'copiame')"
)
con.execute("INSERT INTO monitor_notification (monitor_id, notification_id) VALUES (26, 1)")
con.commit()
PY
}

sql() { python3 -I -c 'import sqlite3,sys; print(*[" ".join(map(str, r)) for r in sqlite3.connect(sys.argv[1]).execute(sys.argv[2])], sep="\n")' "$S/kuma.db" "$1"; }
latido() { # latido <monitor> <segundos atrás> [status]
  python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("INSERT INTO heartbeat (monitor_id, status, time, important) SELECT id, ?, strftime(\"%Y-%m-%d %H:%M:%f\", \"now\", ?), 0 FROM monitor WHERE name = ?", (int(sys.argv[4]), "-%s seconds" % sys.argv[3], sys.argv[2])); c.commit()' "$S/kuma.db" "$1" "$2" "${3:-1}"
}
corre_y_guarda() { # corre_y_guarda <nombre> <código|!0> comando...
  espera "$@"
  cat "$S/salida" >>"$TODO"
}
reinicios() { grep -c 'docker restart' "$FAKE_LOG" || true; }
un_reinicio() { # un_reinicio <etapa> <cuenta antes>
  [ "$(( $(reinicios) - $2 ))" = 1 ] || fallo "$1: la corrida del servidor debe reiniciar Kuma exactamente una vez"
}
tokens() { sql "SELECT push_token FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') ORDER BY name"; }
respaldo_de_antes() { # respaldo_de_antes <etiqueta>: el respaldo del día sigue siendo el de antes de los cambios
  [ "$(python3 -I -c 'import sqlite3,glob,sys; print(sqlite3.connect(glob.glob(sys.argv[1])[0]).execute("select count(*) from monitor").fetchone()[0])' "$S/kuma.db.antes-motor-03-*")" = 1 ] || fallo "$1: el respaldo de antes de motor-03 se pisó con los cambios"
}

crea_base
printf 'MOTOR_TELEGRAM_TOKEN=abc\n# nota\nMOTOR_KUMA_URL=http://viejo/api/push/VIEJO\nMOTOR_X=1' >"$HOME_MINI/.motor/secretos.env"
chmod 644 "$HOME_MINI/.motor/secretos.env"
printf '# nota de la señal\r\nMOTOR_TELEGRAM_TOKEN=pc\r\nMOTOR_X=2\r\nMOTOR_N=ñandú' >"$HOME_PC/.motor/secretos.env"

r0=$(reinicios)
corre_y_guarda "primera corrida" 0 bash "$RAIZ/ops/senal-kuma.sh"
[ "$(sql "SELECT count(*) FROM monitor WHERE type='push'")" = 3 ] || fallo "primera: debe haber 3 monitores push (la plantilla y los 2 nuevos)"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND active=1 AND interval=300 AND retry_interval=60 AND maxretries=2 AND push_token GLOB '[A-Za-z0-9]*' AND length(push_token)=32 AND push_token NOT GLOB '*[^A-Za-z0-9]*'")" = 2 ] || fallo "primera: los dos monitores deben quedar activos, 300/60/2 y con token de 32 letras y números"
[ "$(sql "SELECT count(*) FROM monitor_notification mn JOIN monitor m ON m.id=mn.monitor_id JOIN notification n ON n.id=mn.notification_id WHERE m.name IN ('Motor de la Mini','Motor de la PC') AND n.name='Server Alerts'")" = 2 ] || fallo "primera: los dos monitores deben estar conectados a Server Alerts"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND type='push' AND user_id=1 AND timeout=48 AND description='copiame' AND accepted_statuscodes_json='[\"200-299\"]'")" = 2 ] || fallo "primera: los monitores nuevos deben copiar los campos de 'backup orbit'"
[ "$(sql "SELECT push_token FROM monitor WHERE id=26")" = ORBITTOKENDEPRUEBA ] || fallo "primera: no debe tocar 'backup orbit'"
compgen -G "$S/kuma.db.antes-motor-03-*" >/dev/null || fallo "primera: falta el respaldo kuma.db.antes-motor-03-<fecha>"
respaldo_de_antes "primera"
un_reinicio primera "$r0"
tokens_1="$(tokens)"

verifica_archivos() { # verifica_archivos <etapa>
  local f url
  for m in MINI PC; do
    if [ "$m" = MINI ]; then f="$HOME_MINI/.motor/secretos.env"; else f="$HOME_PC/.motor/secretos.env"; fi
    [ "$(tr -d '\r' <"$f" | grep -c '^MOTOR_KUMA_URL=')" = 1 ] || fallo "$1: $m debe tener una sola línea MOTOR_KUMA_URL"
    tr -d '\r' <"$f" | grep -q '^MOTOR_X=' || fallo "$1: $m perdió otra línea de secretos.env"
    if [ "$m" = PC ]; then
      grep -q 'nota de la señal' "$f" && grep -q 'MOTOR_N=ñandú' "$f" || fallo "$1: la PC debe conservar los caracteres no ASCII"
    fi
    tr -d '\r' <"$f" | grep -q '^MOTOR_TELEGRAM_TOKEN=' || fallo "$1: $m perdió MOTOR_TELEGRAM_TOKEN"
  done
  url="$(grep '^MOTOR_KUMA_URL=' "$HOME_MINI/.motor/secretos.env")"
  [ "$url" = "MOTOR_KUMA_URL=http://10.13.13.1:3001/api/push/$(sql "SELECT push_token FROM monitor WHERE name='Motor de la Mini'")" ] || fallo "$1: la URL de la Mini no es la del monitor"
  url="$(tr -d '\r' <"$HOME_PC/.motor/secretos.env" | grep '^MOTOR_KUMA_URL=')"
  [ "$url" = "MOTOR_KUMA_URL=http://10.13.13.1:3001/api/push/$(sql "SELECT push_token FROM monitor WHERE name='Motor de la PC'")" ] || fallo "$1: la URL de la PC no es la del monitor"
  [ "$(stat -f %Lp "$HOME_MINI/.motor/secretos.env" 2>/dev/null || stat -c %a "$HOME_MINI/.motor/secretos.env")" = 600 ] || fallo "$1: secretos.env de la Mini debe quedar en 600"
  compgen -G "$HOME_MINI/.motor/*nuevo*" >/dev/null && fallo "$1: quedó un temporal en la Mini"
  compgen -G "$HOME_PC/.motor/*nuevo*" >/dev/null && fallo "$1: quedó un temporal en la PC"
}
verifica_archivos "primera"

r0=$(reinicios)
corre_y_guarda "segunda corrida" 0 bash "$RAIZ/ops/senal-kuma.sh"
un_reinicio segunda "$r0"
[ "$(tokens)" = "$tokens_1" ] || fallo "segunda: debe reusar los tokens"
[ "$(sql "SELECT count(*) FROM monitor WHERE type='push'")" = 3 ] || fallo "segunda: no debe duplicar monitores"
[ "$(sql "SELECT count(*) FROM monitor_notification")" = 3 ] || fallo "segunda: no debe duplicar monitor_notification"
[ "$(find "$S" -maxdepth 1 -name 'kuma.db.antes-motor-03-*' | wc -l | tr -d ' ')" = 1 ] || fallo "segunda: el respaldo es uno por día"
respaldo_de_antes "segunda"
verifica_archivos "segunda"

espera "checklist sin señales falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist sin señales" "$S/salida" "no tiene señal UP"
latido "Motor de la Mini" 10
espera "checklist con señal solo de la Mini falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist sin señal de la PC" "$S/salida" "de pc no tiene señal UP"
latido "Motor de la PC" 200
espera "checklist con señal de hace 200 s falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
latido "Motor de la PC" 5 0
espera "checklist con señal DOWN falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
latido "Motor de la PC" 170
corre_y_guarda "checklist con las dos señales pasa" 0 bash "$RAIZ/ops/senal-kuma-checklist.sh"

# La primera señal puede tardar: el checklist la espera en vez de rendirse en la primera mirada.
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM heartbeat WHERE monitor_id IN (SELECT id FROM monitor WHERE name = \"Motor de la PC\")"); c.commit()' "$S/kuma.db"
( sleep 3; latido "Motor de la PC" 1 ) &
corre_y_guarda "checklist espera la primera señal de la PC" 0 env MOTOR_KUMA_TOPE=15 bash "$RAIZ/ops/senal-kuma-checklist.sh"

python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE notification SET active=0"); c.commit()' "$S/kuma.db"
espera "checklist con el aviso apagado falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE notification SET active=1"); c.execute("UPDATE monitor SET active=0 WHERE name=\"Motor de la PC\""); c.commit()' "$S/kuma.db"
espera "checklist con un monitor inactivo falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE monitor SET active=1 WHERE name=\"Motor de la PC\""); c.execute("UPDATE monitor_notification SET notification_id = (SELECT id FROM notification WHERE name = \"Telegram\") WHERE monitor_id IN (SELECT id FROM monitor WHERE name=\"Motor de la Mini\")"); c.commit()' "$S/kuma.db"
espera "checklist con la Mini en otro aviso falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist otro aviso" "$S/salida" "mini no está conectado a un aviso Server Alerts activo"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE monitor_notification SET notification_id = (SELECT id FROM notification WHERE name = \"Server Alerts\") WHERE monitor_id IN (SELECT id FROM monitor WHERE name=\"Motor de la Mini\")"); c.commit()' "$S/kuma.db"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM monitor_notification WHERE monitor_id IN (SELECT id FROM monitor WHERE name=\"Motor de la Mini\")"); c.commit()' "$S/kuma.db"
espera "checklist con un monitor sin aviso falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
FAKE_KUMA_CORRE=false espera "checklist con Kuma caído falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist Kuma caído" "$S/salida" "Kuma no corre"
bash "$RAIZ/ops/senal-kuma.sh" >/dev/null 2>&1
chmod 644 "$HOME_MINI/.motor/secretos.env"
espera "checklist con secretos.env abierto falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
chmod 600 "$HOME_MINI/.motor/secretos.env"
grep -v '^MOTOR_KUMA_URL=' "$HOME_PC/.motor/secretos.env" >"$S/pc-sin" && cp "$S/pc-sin" "$HOME_PC/.motor/secretos.env"
espera "checklist sin la línea en la PC falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
bash "$RAIZ/ops/senal-kuma.sh" >/dev/null 2>&1

r0=$(reinicios)
corre_y_guarda "rollback" 0 bash "$RAIZ/ops/senal-kuma-rollback.sh"
un_reinicio rollback "$r0"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND active=0")" = 2 ] || fallo "rollback: los dos monitores deben quedar con active 0"
[ "$(sql "SELECT count(*) FROM monitor")" = 3 ] || fallo "rollback: no borra monitores"
[ "$(tokens)" = "$tokens_1" ] || fallo "rollback: no cambia tokens"
grep -q MOTOR_KUMA_URL "$HOME_MINI/.motor/secretos.env" "$HOME_PC/.motor/secretos.env" && fallo "rollback: la línea debe salir de la Mini y la PC"
grep -q '^MOTOR_X=1' "$HOME_MINI/.motor/secretos.env" || fallo "rollback: la Mini perdió otra línea"
grep -q 'MOTOR_N=ñandú' "$HOME_PC/.motor/secretos.env" || fallo "rollback: la PC perdió los caracteres no ASCII"
[ "$(stat -f %Lp "$HOME_MINI/.motor/secretos.env" 2>/dev/null || stat -c %a "$HOME_MINI/.motor/secretos.env")" = 600 ] || fallo "rollback: secretos.env de la Mini debe seguir en 600"
respaldo_de_antes "rollback"

r0=$(reinicios)
corre_y_guarda "otra corrida después del rollback" 0 bash "$RAIZ/ops/senal-kuma.sh"
un_reinicio "tras rollback" "$r0"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND active=1")" = 2 ] || fallo "tras rollback: los monitores deben volver a activarse"
[ "$(tokens)" = "$tokens_1" ] || fallo "tras rollback: debe reusar los tokens"
[ "$(sql "SELECT count(*) FROM monitor")" = 3 ] || fallo "tras rollback: no debe duplicar monitores"
respaldo_de_antes "tras rollback"
verifica_archivos "tras rollback"

grep -q 'docker restart uptime-kuma' "$FAKE_LOG" || fallo "falta docker restart uptime-kuma"
grep -Eq 'docker (stop|kill|rm)' "$FAKE_LOG" && fallo "nunca se llama docker stop"
grep -q 'docker: no se espera' "$FAKE_LOG" && fallo "docker recibió un subcomando inesperado"

# Sin -X utf8 la PC contesta en cp1252: la lectura con respaldo lo tiene que tolerar igual.
(
  export LC_ALL=C
  . "$RAIZ/ops/lib.sh" && . "$RAIZ/ops/lib-kuma.sh" &&
    pc_motor="cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor" &&
    senal_configurada pc
) || fallo "la señal se debe poder leer aunque la PC conteste en cp1252"

for t in $tokens_1; do
  grep -qF "$t" "$TODO" && fallo "una salida contiene el token $t"
  grep -qF "$t" "$FAKE_LOG" && fallo "una línea de comandos de ssh contiene el token"
done
grep -q "VIEJO" "$TODO" && fallo "una salida contiene un valor de secretos.env"

command -v pwsh >/dev/null 2>&1 || echo "sin pwsh: no probé la escritura de la PC"
termina
