#!/usr/bin/env bash
# ops/senal-kuma.sh, senal-kuma-checklist.sh y senal-kuma-rollback.sh con ssh y docker falsos y una
# base SQLite con las tablas reales de Kuma (kuma-esquema.sql). El motor de planes y el supervisor de
# encargos tienen monitores distintos. La Mini y la PC ya traen la dirección del supervisor y se
# conserva; la MacBook no la tiene y el script le crea su monitor. La Mac y la Mini corren su script
# en un HOME falso; con pwsh, la PC corre el PowerShell de verdad con un USERPROFILE falso; sin pwsh,
# un sustituto en Python prueba todo menos la escritura de la PC. Un uname y un hostname falsos
# deciden si la Mac es local: con Linux va por ssh; con Darwin y el hostname de la Mini también va
# por ssh; solo con Darwin y otro hostname es la MacBook y escribe en su HOME sin ssh a sí misma.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc MOTOR_SSH_SERVIDOR=goncloud
export MOTOR_KUMA_DB="$S/kuma.db" MOTOR_ESPERA_PASO=1 MOTOR_KUMA_TOPE=2
export HOME_MAC="$S/mac" HOME_MINI="$S/mini" HOME_PC="$S/pc"
export FAKE_UNAME_S=Linux
UNAME_REAL="$(command -v uname)"
export UNAME_REAL
mkdir -p "$HOME_MAC/.motor" "$HOME_MINI/.motor" "$HOME_PC/.motor"
cat >"$S/bin/uname" <<'FAKE'
#!/usr/bin/env bash
if [ "${1:-}" = -s ]; then printf '%s\n' "${FAKE_UNAME_S:-Linux}"; exit 0; fi
exec "$UNAME_REAL" "$@"
FAKE
cat >"$S/bin/hostname" <<'FAKE'
#!/usr/bin/env bash
case "${HOME:-}" in
  */mac) printf '%s\n' 'MacBook-falsa.local' ;;
  */mini) printf '%s\n' 'Mini-falsa.local' ;;
  *) printf '%s\n' 'otra-falsa.local' ;;
esac
FAKE
cat >"$S/escribe-pc.py" <<'PYEOF'
import re
import sys
from pathlib import Path
raiz, accion, sub = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
f = raiz / sub / "secretos.env"
marca = raiz / sub / ".senal-kuma-encargos"
tok = sys.stdin.readline().strip() if accion == "poner" else ""
original = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
linea = next((l for l in original if re.match(r"^\s*MOTOR_KUMA_URL=.", l)), None)
if accion == "quitar" and sub == ".motor-encargos" and not marca.exists():
    sys.exit(3)
if accion in ("asegurar", "asegurar_distinto") and not linea:
    sys.exit(3)
if accion == "asegurar_distinto":
    plan = raiz / ".motor" / "secretos.env"
    plan_linea = None
    if plan.exists():
        plan_linea = next((l for l in plan.read_text(encoding="utf-8").splitlines() if re.match(r"^\s*MOTOR_KUMA_URL=.", l)), None)
    token_de = lambda l: re.sub(r"[?#].*$", "", l.rsplit("/", 1)[-1])
    if plan_linea and token_de(plan_linea) == token_de(linea):
        sys.exit(6)
lineas = [l for l in original if not l.lstrip().startswith("MOTOR_KUMA_URL=")]
if accion == "poner":
    lineas.append("MOTOR_KUMA_URL=" + sys.argv[4] + tok)
if accion in ("asegurar", "asegurar_distinto"):
    lineas.append(linea)
f.parent.mkdir(parents=True, exist_ok=True)
f.write_text("\n".join(lineas) + ("\n" if lineas else ""), encoding="utf-8")
if accion == "poner" and sub == ".motor-encargos":
    marca.write_text("creado por ops/senal-kuma.sh\n", encoding="utf-8")
if accion == "quitar" and sub == ".motor-encargos":
    marca.unlink(missing_ok=True)
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
  mac)
    case "$*" in *doctor*) cat >/dev/null; doctor "$HOME_MAC/.motor/secretos.env" utf8; exit 0 ;; esac
    HOME="$HOME_MAC" exec bash -c "$*" ;;
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
          _sub="$(printf '%s\n' "$_script" | sed -n "s/.*sub = '\([^']*\)'.*/\1/p" | head -1)"
          _base="$(printf '%s\n' "$_script" | sed -n "s/.*MOTOR_KUMA_URL=\([^']*\)'.*/\1/p" | grep -F http | head -1)"
          exec python3 -I "$S/escribe-pc.py" "$HOME_PC" "$_accion" "$_sub" "$_base"
        fi
        python3 -I -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode("utf-16-le"))' "${*##* }" >>"$FAKE_LOG"
        USERPROFILE="$HOME_PC" exec pwsh ${1#powershell } ;;
    esac
    echo "ssh falso: no se espera '$*'" >&2; exit 1 ;;
esac
FAKE
chmod +x "$S/bin/docker" "$S/bin/ssh" "$S/bin/uname" "$S/bin/hostname"
mkdir -p "$HOME_MAC/motor/motor"
: >"$HOME_MAC/motor/motor/__init__.py"
cat >"$HOME_MAC/motor/motor/__main__.py" <<'PYEOF'
import json, os, pathlib
f = pathlib.Path(os.environ["HOME"]) / ".motor" / "secretos.env"
ok = False
if f.is_file():
    lineas = f.read_text(encoding="utf-8").splitlines()
    ok = any(l.strip().startswith("MOTOR_KUMA_URL=") and l.strip() != "MOTOR_KUMA_URL=" for l in lineas)
print(json.dumps({"senal": "señal a Kuma: configurada" if ok else "señal a Kuma: sin configurar"}))
PYEOF

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
tokens() { sql "SELECT push_token FROM monitor WHERE name IN ('Motor de la MacBook','Motor de la Mini','Motor de la PC') ORDER BY name"; }
tokens_encargos() { sql "SELECT push_token FROM monitor WHERE name='Motor de encargos de la MacBook'"; }
todos_los_tokens() { tokens; tokens_encargos; }
respaldo_de_antes() { # respaldo_de_antes <etiqueta>: el respaldo del día sigue siendo el de antes de los cambios
  [ "$(python3 -I -c 'import sqlite3,glob,sys; print(sqlite3.connect(glob.glob(sys.argv[1])[0]).execute("select count(*) from monitor").fetchone()[0])' "$S/kuma.db.antes-motor-03-*")" = 1 ] || fallo "$1: el respaldo de antes de motor-03 se pisó con los cambios"
}

crea_base
printf 'MOTOR_TELEGRAM_TOKEN=abc\n# nota\nMOTOR_KUMA_URL=http://viejo/api/push/VIEJO\nMOTOR_X=1' >"$HOME_MINI/.motor/secretos.env"
chmod 644 "$HOME_MINI/.motor/secretos.env"
mkdir -p "$HOME_MINI/.motor-encargos"
printf 'MOTOR_TELEGRAM_TOKEN=abc\nMOTOR_KUMA_URL=http://viejo/api/push/VIEJO-ENCARGOS\nMOTOR_X=1\n' >"$HOME_MINI/.motor-encargos/secretos.env"
printf 'MOTOR_TELEGRAM_TOKEN=mac\nMOTOR_X=3\n' >"$HOME_MAC/.motor/secretos.env"
mkdir -p "$HOME_MAC/.motor-encargos"
printf 'MOTOR_TELEGRAM_TOKEN=mac\nMOTOR_X=3\n' >"$HOME_MAC/.motor-encargos/secretos.env"
printf '# nota de la señal\r\nMOTOR_TELEGRAM_TOKEN=pc\r\nMOTOR_X=2\r\nMOTOR_N=ñandú' >"$HOME_PC/.motor/secretos.env"
mkdir -p "$HOME_PC/.motor-encargos"
printf '# nota de la señal\r\nMOTOR_TELEGRAM_TOKEN=pc\r\nMOTOR_KUMA_URL=http://viejo/api/push/VIEJO-PC-ENCARGOS\r\nMOTOR_X=2\r\nMOTOR_N=ñandú' >"$HOME_PC/.motor-encargos/secretos.env"

r0=$(reinicios)
corre_y_guarda "primera corrida" 0 bash "$RAIZ/ops/senal-kuma.sh"
[ "$(sql "SELECT count(*) FROM monitor WHERE type='push'")" = 5 ] || fallo "primera: debe haber 5 monitores push (la plantilla, 3 de planes y el de encargos de la MacBook)"
[ "$(sql "SELECT count(*) FROM monitor WHERE name='Motor de la MacBook'")" = 1 ] || fallo "primera: el alta debe incluir el monitor Motor de la MacBook"
[ "$(sql "SELECT count(*) FROM monitor WHERE name='Motor de encargos de la MacBook'")" = 1 ] || fallo "primera: el alta debe incluir el monitor de encargos de la MacBook"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la MacBook','Motor de la Mini','Motor de la PC','Motor de encargos de la MacBook') AND active=1 AND interval=300 AND retry_interval=60 AND maxretries=2 AND push_token GLOB '[A-Za-z0-9]*' AND length(push_token)=32 AND push_token NOT GLOB '*[^A-Za-z0-9]*'")" = 4 ] || fallo "primera: los cuatro monitores gestionados deben quedar activos, 300/60/2 y con token de 32 letras y números"
[ "$(sql "SELECT count(*) FROM monitor_notification mn JOIN monitor m ON m.id=mn.monitor_id JOIN notification n ON n.id=mn.notification_id WHERE m.name IN ('Motor de la MacBook','Motor de la Mini','Motor de la PC','Motor de encargos de la MacBook') AND n.name='Server Alerts'")" = 4 ] || fallo "primera: los cuatro monitores gestionados deben estar conectados a Server Alerts"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la MacBook','Motor de la Mini','Motor de la PC','Motor de encargos de la MacBook') AND type='push' AND user_id=1 AND timeout=48 AND description='copiame' AND accepted_statuscodes_json='[\"200-299\"]'")" = 4 ] || fallo "primera: los monitores nuevos deben copiar los campos de 'backup orbit'"
[ "$(sql "SELECT push_token FROM monitor WHERE id=26")" = ORBITTOKENDEPRUEBA ] || fallo "primera: no debe tocar 'backup orbit'"
compgen -G "$S/kuma.db.antes-motor-03-*" >/dev/null || fallo "primera: falta el respaldo kuma.db.antes-motor-03-<fecha>"
respaldo_de_antes "primera"
un_reinicio primera "$r0"
tokens_1="$(tokens)"
tokens_enc_1="$(tokens_encargos)"
[ "$(printf '%s\n%s\n' "$tokens_1" "$tokens_enc_1" | sort -u | grep -c .)" = 4 ] || fallo "primera: los cuatro tokens gestionados deben ser distintos"

verifica_archivos() { # verifica_archivos <etapa>
  local m f1 f2 f url1 url2 plan enc
  for m in MAC MINI PC; do
    case "$m" in
      MAC) f1="$HOME_MAC/.motor/secretos.env"; f2="$HOME_MAC/.motor-encargos/secretos.env"; plan="Motor de la MacBook"; enc="MOTOR_KUMA_URL=http://10.13.13.1:3001/api/push/$(sql "SELECT push_token FROM monitor WHERE name='Motor de encargos de la MacBook'")" ;;
      MINI) f1="$HOME_MINI/.motor/secretos.env"; f2="$HOME_MINI/.motor-encargos/secretos.env"; plan="Motor de la Mini"; enc="MOTOR_KUMA_URL=http://viejo/api/push/VIEJO-ENCARGOS" ;;
      PC) f1="$HOME_PC/.motor/secretos.env"; f2="$HOME_PC/.motor-encargos/secretos.env"; plan="Motor de la PC"; enc="MOTOR_KUMA_URL=http://viejo/api/push/VIEJO-PC-ENCARGOS" ;;
    esac
    for f in "$f1" "$f2"; do
      [ "$(tr -d '\r' <"$f" | grep -c '^MOTOR_KUMA_URL=')" = 1 ] || fallo "$1: $m debe tener una sola línea MOTOR_KUMA_URL en $f"
      tr -d '\r' <"$f" | grep -q '^MOTOR_X=' || fallo "$1: $m perdió otra línea de secretos.env en $f"
      tr -d '\r' <"$f" | grep -q '^MOTOR_TELEGRAM_TOKEN=' || fallo "$1: $m perdió MOTOR_TELEGRAM_TOKEN en $f"
      if [ "$m" = PC ]; then
        grep -q 'nota de la señal' "$f" && grep -q 'MOTOR_N=ñandú' "$f" || fallo "$1: la PC debe conservar los caracteres no ASCII en $f"
      fi
      compgen -G "$(dirname "$f")/*nuevo*" >/dev/null && fallo "$1: quedó un temporal en $m"
    done
    url1="$(tr -d '\r' <"$f1" | grep '^MOTOR_KUMA_URL=')"
    url2="$(tr -d '\r' <"$f2" | grep '^MOTOR_KUMA_URL=')"
    [ "$url1" = "MOTOR_KUMA_URL=http://10.13.13.1:3001/api/push/$(sql "SELECT push_token FROM monitor WHERE name='$plan'")" ] || fallo "$1: la URL de planes de $m no es la de su monitor en $f1"
    [ "$url2" = "$enc" ] || fallo "$1: la URL de encargos de $m no es la esperada en $f2"
    [ "$url1" != "$url2" ] || fallo "$1: $m tiene la misma URL en los dos secretos (daemon y supervisor comparten monitor)"
    if [ "$m" != PC ]; then
      [ "$(stat -c %a "$f1" 2>/dev/null || stat -f %Lp "$f1")" = 600 ] || fallo "$1: $f1 debe quedar en 600"
      [ "$(stat -c %a "$f2" 2>/dev/null || stat -f %Lp "$f2")" = 600 ] || fallo "$1: $f2 debe quedar en 600"
    fi
  done
}
verifica_archivos "primera"

r0=$(reinicios)
corre_y_guarda "segunda corrida" 0 bash "$RAIZ/ops/senal-kuma.sh"
un_reinicio segunda "$r0"
[ "$(tokens)" = "$tokens_1" ] || fallo "segunda: debe reusar los tokens de planes"
[ "$(tokens_encargos)" = "$tokens_enc_1" ] || fallo "segunda: debe reusar el token de encargos de la MacBook"
[ "$(sql "SELECT count(*) FROM monitor WHERE type='push'")" = 5 ] || fallo "segunda: no debe duplicar monitores"
[ "$(sql "SELECT count(*) FROM monitor_notification")" = 5 ] || fallo "segunda: no debe duplicar monitor_notification"
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
espera "checklist sin señal de la MacBook falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist sin señal de la MacBook" "$S/salida" "de mac no tiene señal UP"
latido "Motor de la MacBook" 5
espera "checklist sin señal de encargos de la MacBook falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist sin señal de encargos de la MacBook" "$S/salida" "de encargos de mac no tiene señal UP"
latido "Motor de encargos de la MacBook" 5
corre_y_guarda "checklist con las señales de los cuatro monitores pasa" 0 bash "$RAIZ/ops/senal-kuma-checklist.sh"

# F6: si el monitor de encargos de la MacBook desaparece, el checklist no puede darlo por bueno.
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE monitor SET name = \"Motor de encargos de la MacBook (ausente)\" WHERE name = \"Motor de encargos de la MacBook\""); c.commit()' "$S/kuma.db"
espera "checklist sin el monitor de encargos de la MacBook falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist monitor de encargos ausente" "$S/salida" "el monitor de encargos de mac no existe o no está activo"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE monitor SET name = \"Motor de encargos de la MacBook\" WHERE name = \"Motor de encargos de la MacBook (ausente)\""); c.commit()' "$S/kuma.db"

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
chmod 644 "$HOME_MAC/.motor-encargos/secretos.env"
espera "checklist con el secreto de encargos de la Mac abierto falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist encargos de la Mac abierto" "$S/salida" "no está en 600"
chmod 600 "$HOME_MAC/.motor-encargos/secretos.env"
grep -v '^MOTOR_KUMA_URL=' "$HOME_PC/.motor/secretos.env" >"$S/pc-sin" && cp "$S/pc-sin" "$HOME_PC/.motor/secretos.env"
espera "checklist sin la línea en la PC falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
grep -v '^MOTOR_KUMA_URL=' "$HOME_MAC/.motor-encargos/secretos.env" >"$S/mac-sin" && cp "$S/mac-sin" "$HOME_MAC/.motor-encargos/secretos.env"
espera "checklist sin la línea de encargos en la Mac falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist sin línea de encargos en la Mac" "$S/salida" "sin MOTOR_KUMA_URL"
bash "$RAIZ/ops/senal-kuma.sh" >/dev/null 2>&1

# F1: el mismo token en los dos secretos hace que daemon y supervisor compartan monitor y uno tape la
# caída del otro. El checklist lo tiene que ver.
cp "$HOME_MINI/.motor-encargos/secretos.env" "$S/mini-enc-ok"
grep -v '^MOTOR_KUMA_URL=' "$S/mini-enc-ok" >"$S/mini-enc"
plan_url="$(grep '^MOTOR_KUMA_URL=' "$HOME_MINI/.motor/secretos.env")"
printf 'MOTOR_KUMA_URL=http://otro-espejo/api/push/%s\n' "${plan_url##*/}" >>"$S/mini-enc"
cp "$S/mini-enc" "$HOME_MINI/.motor-encargos/secretos.env"
chmod 600 "$HOME_MINI/.motor-encargos/secretos.env"
espera "checklist con los dos secretos en el mismo monitor falla" '!0' bash "$RAIZ/ops/senal-kuma-checklist.sh"
contiene "checklist mismo monitor" "$S/salida" "los dos secretos apuntan al mismo monitor"
(
  . "$RAIZ/ops/lib.sh"
  . "$RAIZ/ops/lib-kuma.sh"
  HOME="$HOME_MINI" linea_en mini asegurar_distinto .motor-encargos
)
[ "$?" = 6 ] || fallo "el escritor debe avisar que los dos secretos apuntan al mismo monitor para crear uno propio"
cp "$S/mini-enc-ok" "$HOME_MINI/.motor-encargos/secretos.env"
chmod 600 "$HOME_MINI/.motor-encargos/secretos.env"

# F5: sin la marca de creación, el rollback no borra la dirección del supervisor del operador.
(
  . "$RAIZ/ops/lib.sh"
  . "$RAIZ/ops/lib-kuma.sh"
  HOME="$HOME_MINI" linea_en mini quitar .motor-encargos
)
[ "$?" = 3 ] || fallo "el rollback no debe borrar el secreto de encargos sin la marca de creación"
grep -q '^MOTOR_KUMA_URL=http://viejo/api/push/VIEJO-ENCARGOS$' "$HOME_MINI/.motor-encargos/secretos.env" || fallo "la Mini perdió la dirección propia de su supervisor sin marca de creación"

r0=$(reinicios)
corre_y_guarda "rollback" 0 bash "$RAIZ/ops/senal-kuma-rollback.sh"
un_reinicio rollback "$r0"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la MacBook','Motor de la Mini','Motor de la PC','Motor de encargos de la MacBook') AND active=0")" = 4 ] || fallo "rollback: los cuatro monitores gestionados deben quedar con active 0"
[ "$(sql "SELECT count(*) FROM monitor")" = 5 ] || fallo "rollback: no borra monitores"
[ "$(tokens)" = "$tokens_1" ] || fallo "rollback: no cambia los tokens de planes"
[ "$(tokens_encargos)" = "$tokens_enc_1" ] || fallo "rollback: no cambia el token de encargos de la MacBook"
grep -q MOTOR_KUMA_URL "$HOME_MAC/.motor/secretos.env" "$HOME_MINI/.motor/secretos.env" "$HOME_PC/.motor/secretos.env" "$HOME_MAC/.motor-encargos/secretos.env" && fallo "rollback: la línea debe salir de los secretos de planes de las tres máquinas y del de encargos de la Mac"
grep -q '^MOTOR_KUMA_URL=http://viejo/api/push/VIEJO-ENCARGOS$' "$HOME_MINI/.motor-encargos/secretos.env" || fallo "rollback: la Mini perdió la dirección propia de su supervisor"
grep -q '^MOTOR_KUMA_URL=http://viejo/api/push/VIEJO-PC-ENCARGOS' "$HOME_PC/.motor-encargos/secretos.env" || fallo "rollback: la PC perdió la dirección propia de su supervisor"
for f in "$HOME_MAC/.motor/secretos.env" "$HOME_MAC/.motor-encargos/secretos.env" "$HOME_MINI/.motor/secretos.env" "$HOME_MINI/.motor-encargos/secretos.env" "$HOME_PC/.motor/secretos.env" "$HOME_PC/.motor-encargos/secretos.env"; do
  grep -q '^MOTOR_X=' "$f" || fallo "rollback: se perdió otra línea en $f"
done
grep -q 'MOTOR_N=ñandú' "$HOME_PC/.motor/secretos.env" && grep -q 'MOTOR_N=ñandú' "$HOME_PC/.motor-encargos/secretos.env" || fallo "rollback: la PC perdió los caracteres no ASCII"
[ "$(stat -c %a "$HOME_MINI/.motor/secretos.env" 2>/dev/null || stat -f %Lp "$HOME_MINI/.motor/secretos.env")" = 600 ] || fallo "rollback: secretos.env de la Mini debe seguir en 600"
[ "$(stat -c %a "$HOME_MAC/.motor-encargos/secretos.env" 2>/dev/null || stat -f %Lp "$HOME_MAC/.motor-encargos/secretos.env")" = 600 ] || fallo "rollback: el secreto de encargos de la Mac debe seguir en 600"
respaldo_de_antes "rollback"

r0=$(reinicios)
corre_y_guarda "otra corrida después del rollback" 0 bash "$RAIZ/ops/senal-kuma.sh"
un_reinicio "tras rollback" "$r0"
[ "$(sql "SELECT count(*) FROM monitor WHERE name IN ('Motor de la MacBook','Motor de la Mini','Motor de la PC','Motor de encargos de la MacBook') AND active=1")" = 4 ] || fallo "tras rollback: los monitores gestionados deben volver a activarse"
[ "$(tokens)" = "$tokens_1" ] || fallo "tras rollback: debe reusar los tokens de planes"
[ "$(tokens_encargos)" = "$tokens_enc_1" ] || fallo "tras rollback: debe reusar el token de encargos de la MacBook"
[ "$(sql "SELECT count(*) FROM monitor")" = 5 ] || fallo "tras rollback: no debe duplicar monitores"
respaldo_de_antes "tras rollback"
verifica_archivos "tras rollback"

# F8: si se borra el monitor de encargos de la MacBook, la siguiente corrida lo recrea con token nuevo.
token_enc_viejo="$(tokens_encargos)"
python3 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM monitor_notification WHERE monitor_id IN (SELECT id FROM monitor WHERE name = \"Motor de encargos de la MacBook\")"); c.execute("DELETE FROM heartbeat WHERE monitor_id IN (SELECT id FROM monitor WHERE name = \"Motor de encargos de la MacBook\")"); c.execute("DELETE FROM monitor WHERE name = \"Motor de encargos de la MacBook\""); c.commit()' "$S/kuma.db"
[ "$(sql "SELECT count(*) FROM monitor WHERE name='Motor de encargos de la MacBook'")" = 0 ] || fallo "F8: no se pudo simular el borrado del monitor de encargos"
r0=$(reinicios)
corre_y_guarda "recrea el monitor de encargos borrado de la MacBook" 0 bash "$RAIZ/ops/senal-kuma.sh"
un_reinicio "recreación" "$r0"
token_enc_nuevo="$(tokens_encargos)"
[ -n "$token_enc_nuevo" ] && [ "$token_enc_nuevo" != "$token_enc_viejo" ] || fallo "F8: el monitor de encargos recreado debe tener un token nuevo"
[ "$(sql "SELECT count(*) FROM monitor WHERE name='Motor de encargos de la MacBook' AND active=1 AND interval=300 AND retry_interval=60 AND maxretries=2 AND length(push_token)=32")" = 1 ] || fallo "F8: el monitor de encargos recreado debe quedar activo y completo"
[ "$(sql "SELECT count(*) FROM monitor_notification mn JOIN monitor m ON m.id=mn.monitor_id JOIN notification n ON n.id=mn.notification_id WHERE m.name='Motor de encargos de la MacBook' AND n.name='Server Alerts'")" = 1 ] || fallo "F8: el monitor recreado debe estar conectado a Server Alerts"
[ "$(sql "SELECT count(*) FROM monitor WHERE type='push'")" = 5 ] || fallo "F8: no debe duplicar monitores al recrear"
url="$(grep '^MOTOR_KUMA_URL=' "$HOME_MAC/.motor-encargos/secretos.env")"
[ "$url" = "MOTOR_KUMA_URL=http://10.13.13.1:3001/api/push/$token_enc_nuevo" ] || fallo "F8: el secreto de encargos de la Mac debe quedar con el token del monitor recreado"
latido "Motor de encargos de la MacBook" 5
corre_y_guarda "checklist tras recrear el monitor de encargos" 0 bash "$RAIZ/ops/senal-kuma-checklist.sh"

grep -q 'docker restart uptime-kuma' "$FAKE_LOG" || fallo "falta docker restart uptime-kuma"
grep -Eq 'docker (stop|kill|rm)' "$FAKE_LOG" && fallo "nunca se llama docker stop"
grep -q 'docker: no se espera' "$FAKE_LOG" && fallo "docker recibió un subcomando inesperado"

# La MacBook escribe en local: con uname=Darwin y su hostname no se hace ssh a sí misma y crea los dos archivos.
rm -rf "$HOME_MAC/.motor" "$HOME_MAC/.motor-encargos"
: >"$FAKE_LOG"
corre_y_guarda "la Mac escribe en local sin ssh a sí misma" 0 env FAKE_UNAME_S=Darwin HOME="$HOME_MAC" bash "$RAIZ/ops/senal-kuma.sh"
grep -q '^mac ::' "$FAKE_LOG" && fallo "la Mac se hizo ssh a sí misma"
for f in "$HOME_MAC/.motor/secretos.env" "$HOME_MAC/.motor-encargos/secretos.env"; do
  [ -f "$f" ] || { fallo "la Mac no creó $f"; continue; }
  [ "$(grep -c '^MOTOR_KUMA_URL=' "$f")" = 1 ] || fallo "la Mac no dejó una sola línea MOTOR_KUMA_URL en $f"
  [ "$(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f")" = 600 ] || fallo "$f no quedó en 600"
done
url="$(grep '^MOTOR_KUMA_URL=' "$HOME_MAC/.motor/secretos.env")"
[ "$url" = "MOTOR_KUMA_URL=http://10.13.13.1:3001/api/push/$(sql "SELECT push_token FROM monitor WHERE name='Motor de la MacBook'")" ] || fallo "la URL local de planes de la Mac no es la del monitor"
url="$(grep '^MOTOR_KUMA_URL=' "$HOME_MAC/.motor-encargos/secretos.env")"
[ "$url" = "MOTOR_KUMA_URL=http://10.13.13.1:3001/api/push/$(sql "SELECT push_token FROM monitor WHERE name='Motor de encargos de la MacBook'")" ] || fallo "la URL local de encargos de la Mac no es la del monitor"

# La Mini también es Darwin: no se debe confundir con la MacBook. Se identifica por hostname y la
# escritura de la Mac va por ssh a su HOME, no al de la Mini.
rm -rf "$HOME_MAC/.motor" "$HOME_MAC/.motor-encargos"
: >"$FAKE_LOG"
corre_y_guarda "desde la Mini la Mac se escribe por ssh" 0 env FAKE_UNAME_S=Darwin HOME="$HOME_MINI" bash "$RAIZ/ops/senal-kuma.sh"
grep -q '^mac ::' "$FAKE_LOG" || fallo "desde la Mini se debe hacer ssh a mac para escribir sus secretos"
token_mac="$(sql "SELECT push_token FROM monitor WHERE name='Motor de la MacBook'")"
token_mac_enc="$(sql "SELECT push_token FROM monitor WHERE name='Motor de encargos de la MacBook'")"
for par in "$HOME_MAC/.motor/secretos.env $token_mac" "$HOME_MAC/.motor-encargos/secretos.env $token_mac_enc"; do
  f=${par% *}; tok=${par##* }
  [ -f "$f" ] || { fallo "desde la Mini no se creó $f en la Mac"; continue; }
  grep -q "^MOTOR_KUMA_URL=.*$tok$" "$f" || fallo "desde la Mini, $f no quedó con la URL del monitor que le toca"
done
for f in "$HOME_MINI/.motor/secretos.env" "$HOME_MINI/.motor-encargos/secretos.env"; do
  grep -q "^MOTOR_KUMA_URL=.*$token_mac$" "$f" && fallo "la Mini quedó con el token de planes de la MacBook en $f"
  grep -q "^MOTOR_KUMA_URL=.*$token_mac_enc$" "$f" && fallo "la Mini quedó con el token de encargos de la MacBook en $f"
done

# Sin -X utf8 la PC contesta en cp1252: la lectura con respaldo lo tiene que tolerar igual.
(
  export LC_ALL=C
  . "$RAIZ/ops/lib.sh" && . "$RAIZ/ops/lib-kuma.sh" &&
    pc_motor="cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor" &&
    senal_configurada pc
) || fallo "la señal se debe poder leer aunque la PC conteste en cp1252"

for t in $(todos_los_tokens); do
  grep -qF "$t" "$TODO" && fallo "una salida contiene el token $t"
  grep -qF "$t" "$FAKE_LOG" && fallo "una línea de comandos de ssh contiene el token $t"
done
grep -q "VIEJO" "$TODO" && fallo "una salida contiene un valor de secretos.env"

command -v pwsh >/dev/null 2>&1 || echo "sin pwsh: no probé la escritura de la PC"
termina
