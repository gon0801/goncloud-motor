#!/usr/bin/env bash
# ops/senal-kuma.sh, senal-kuma-checklist.sh y senal-kuma-rollback.sh con ssh y docker falsos y una
# base SQLite con las tablas reales de Kuma (kuma-esquema.sql). La Mini corre su script en un HOME
# falso; la PC corre el PowerShell de verdad (pwsh) con un USERPROFILE falso.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

command -v pwsh >/dev/null || { echo "falta pwsh para probar la escritura de la PC" >&2; exit 2; }
export MOTOR_MAC_SSH="" MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc MOTOR_SSH_SERVIDOR=goncloud
export MOTOR_KUMA_DB="$S/kuma.db" MOTOR_ESPERA_PASO=1 MOTOR_KUMA_TOPE=2
export HOME_MINI="$S/mini" HOME_PC="$S/pc"
mkdir -p "$HOME_MINI/.motor" "$HOME_PC/.motor"
TODO="$S/todo.out"
: >"$TODO"

cat >"$S/bin/docker" <<'FAKE'
#!/usr/bin/env bash
echo "docker $*" >>"$FAKE_LOG"
case "$1" in
  restart) exit 0 ;;
  inspect) echo "${FAKE_KUMA_CORRE:-true}"; exit 0 ;;
  *) echo "docker: no se espera '$1'" >&2; exit 1 ;;
esac
FAKE
cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
doctor() { # doctor <archivo>: lo que diría `motor doctor --json` según secretos.env
  if grep -q '^MOTOR_KUMA_URL=.' "$1" 2>/dev/null; then echo '{"senal": "señal a Kuma: configurada"}'; else echo '{"senal": "señal a Kuma: sin configurar"}'; fi
}
case "$host" in
  goncloud) exec bash -c "$*" ;;
  gon@mini-fake)
    case "$*" in *doctor*) cat >/dev/null; doctor "$HOME_MINI/.motor/secretos.env"; exit 0 ;; esac
    HOME="$HOME_MINI" exec bash -c "$*" ;;
  gwpc)
    case "$*" in
      *doctor*) doctor "$HOME_PC/.motor/secretos.env"; exit 0 ;;
      "powershell "*) USERPROFILE="$HOME_PC" exec pwsh ${1#powershell } ;;
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
con.execute("INSERT INTO notification (id, name, active, user_id, is_default, config) VALUES (1, 'Server Alerts', 1, 1, 1, '{}')")
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
tokens() { sql "SELECT push_token FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') ORDER BY name"; }

crea_base
printf 'MOTOR_TELEGRAM_TOKEN=abc\n# nota\nMOTOR_KUMA_URL=http://viejo/api/push/VIEJO\nMOTOR_X=1' >"$HOME_MINI/.motor/secretos.env"
chmod 644 "$HOME_MINI/.motor/secretos.env"
printf 'MOTOR_TELEGRAM_TOKEN=pc\r\nMOTOR_X=2' >"$HOME_PC/.motor/secretos.env"

corre_y_guarda "primera corrida" 0 bash "$RAIZ/ops/senal-kuma.sh"
[ "$(sql "SELECT count(*) FROM monitor WHERE type='push'")" = 3 ] || fallo "primera: debe haber 3 monitores push (la plantilla y los 2 nuevos)"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND active=1 AND interval=300 AND retry_interval=60 AND maxretries=2 AND push_token GLOB '[A-Za-z0-9]*' AND length(push_token)=32 AND push_token NOT GLOB '*[^A-Za-z0-9]*'")" = 2 ] || fallo "primera: los dos monitores deben quedar activos, 300/60/2 y con token de 32 letras y números"
[ "$(sql "SELECT count(*) FROM monitor_notification mn JOIN monitor m ON m.id=mn.monitor_id JOIN notification n ON n.id=mn.notification_id WHERE m.name IN ('Motor de la Mini','Motor de la PC') AND n.name='Server Alerts'")" = 2 ] || fallo "primera: los dos monitores deben estar conectados a Server Alerts"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND type='push' AND user_id=1 AND timeout=48 AND description='copiame' AND accepted_statuscodes_json='[\"200-299\"]'")" = 2 ] || fallo "primera: los monitores nuevos deben copiar los campos de 'backup orbit'"
[ "$(sql "SELECT push_token FROM monitor WHERE id=26")" = ORBITTOKENDEPRUEBA ] || fallo "primera: no debe tocar 'backup orbit'"
compgen -G "$S/kuma.db.antes-motor-03-*" >/dev/null || fallo "primera: falta el respaldo kuma.db.antes-motor-03-<fecha>"
[ "$(python3 -I -c 'import sqlite3,glob,sys; print(sqlite3.connect(glob.glob(sys.argv[1])[0]).execute("select count(*) from monitor").fetchone()[0])' "$S/kuma.db.antes-motor-03-*")" = 1 ] || fallo "primera: el respaldo debe ser de antes de los cambios"
tokens_1="$(tokens)"

verifica_archivos() { # verifica_archivos <etapa>
  local f url
  for m in MINI PC; do
    if [ "$m" = MINI ]; then f="$HOME_MINI/.motor/secretos.env"; else f="$HOME_PC/.motor/secretos.env"; fi
    [ "$(tr -d '\r' <"$f" | grep -c '^MOTOR_KUMA_URL=')" = 1 ] || fallo "$1: $m debe tener una sola línea MOTOR_KUMA_URL"
    tr -d '\r' <"$f" | grep -q '^MOTOR_X=' || fallo "$1: $m perdió otra línea de secretos.env"
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

corre_y_guarda "segunda corrida" 0 bash "$RAIZ/ops/senal-kuma.sh"
[ "$(tokens)" = "$tokens_1" ] || fallo "segunda: debe reusar los tokens"
[ "$(sql "SELECT count(*) FROM monitor WHERE type='push'")" = 3 ] || fallo "segunda: no debe duplicar monitores"
[ "$(sql "SELECT count(*) FROM monitor_notification")" = 3 ] || fallo "segunda: no debe duplicar monitor_notification"
[ "$(find "$S" -maxdepth 1 -name 'kuma.db.antes-motor-03-*' | wc -l | tr -d ' ')" = 1 ] || fallo "segunda: el respaldo es uno por día"
verifica_archivos "segunda"

espera "checklist sin señales falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist sin señales" "$S/salida" "no tiene señal UP"
latido "Motor de la Mini" 10
espera "checklist con señal solo de la Mini falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist sin señal de la PC" "$S/salida" "de pc no tiene señal UP"
latido "Motor de la PC" 400
espera "checklist con señal de hace 400 s falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
latido "Motor de la PC" 5 0
espera "checklist con señal DOWN falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
latido "Motor de la PC" 5
corre_y_guarda "checklist con las dos señales pasa" 0 bash "$RAIZ/ops/senal-kuma-checklist.sh"

python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE notification SET active=0"); c.commit()' "$S/kuma.db"
espera "checklist con el aviso apagado falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE notification SET active=1; UPDATE monitor SET active=0 WHERE name=\"Motor de la PC\"".split(";")[0]); c.execute("UPDATE monitor SET active=0 WHERE name=\"Motor de la PC\""); c.commit()' "$S/kuma.db"
espera "checklist con un monitor inactivo falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE monitor SET active=1 WHERE name=\"Motor de la PC\""); c.execute("DELETE FROM monitor_notification WHERE monitor_id IN (SELECT id FROM monitor WHERE name=\"Motor de la Mini\")"); c.commit()' "$S/kuma.db"
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

corre_y_guarda "rollback" 0 bash "$RAIZ/ops/senal-kuma-rollback.sh"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND active=0")" = 2 ] || fallo "rollback: los dos monitores deben quedar con active 0"
[ "$(sql "SELECT count(*) FROM monitor")" = 3 ] || fallo "rollback: no borra monitores"
[ "$(tokens)" = "$tokens_1" ] || fallo "rollback: no cambia tokens"
grep -q MOTOR_KUMA_URL "$HOME_MINI/.motor/secretos.env" "$HOME_PC/.motor/secretos.env" && fallo "rollback: la línea debe salir de la Mini y la PC"
grep -q '^MOTOR_X=1' "$HOME_MINI/.motor/secretos.env" || fallo "rollback: la Mini perdió otra línea"
[ "$(stat -f %Lp "$HOME_MINI/.motor/secretos.env" 2>/dev/null || stat -c %a "$HOME_MINI/.motor/secretos.env")" = 600 ] || fallo "rollback: secretos.env de la Mini debe seguir en 600"

corre_y_guarda "otra corrida después del rollback" 0 bash "$RAIZ/ops/senal-kuma.sh"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la Mini','Motor de la PC') AND active=1")" = 2 ] || fallo "tras rollback: los monitores deben volver a activarse"
[ "$(tokens)" = "$tokens_1" ] || fallo "tras rollback: debe reusar los tokens"
[ "$(sql "SELECT count(*) FROM monitor")" = 3 ] || fallo "tras rollback: no debe duplicar monitores"
verifica_archivos "tras rollback"

grep -q 'docker restart uptime-kuma' "$FAKE_LOG" || fallo "falta docker restart uptime-kuma"
grep -Eq 'docker (stop|kill|rm)' "$FAKE_LOG" && fallo "nunca se llama docker stop"
[ "$(grep -c 'docker restart' "$FAKE_LOG")" -ge 4 ] || fallo "cada corrida del servidor reinicia Kuma una vez"

for t in $tokens_1; do
  grep -qF "$t" "$TODO" && fallo "una salida contiene el token $t"
  grep -qF "$t" "$FAKE_LOG" && fallo "una línea de comandos de ssh contiene el token"
done
grep -q "VIEJO" "$TODO" && fallo "una salida contiene un valor de secretos.env"

termina
