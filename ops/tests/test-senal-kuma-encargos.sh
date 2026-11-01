#!/usr/bin/env bash
# ops/senal-kuma-encargos.sh con un ssh falso y un HOME de mentira para la Mini: escribe y quita solo
# MOTOR_KUMA_ENCARGOS_URL, deja intacta MOTOR_KUMA_URL (la del daemon del modo planes) y no imprime el token.
set -uo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Donde el temporal por defecto no se puede crear (entornos con el /tmp cerrado), uno junto a la prueba; lib-prueba.sh lo borra al salir.
mktemp() { command mktemp "$@" 2>/dev/null || { mkdir -p "$AQUI/.tmp.$$" && echo "$AQUI/.tmp.$$"; }; }
. "$AQUI/lib-prueba.sh"

export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc MOTOR_SSH_SERVIDOR=goncloud
export HOME_MINI="$S/mini"
mkdir -p "$HOME_MINI/.motor"
cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
case "$host" in
  gon@mini-fake) HOME="$HOME_MINI" exec bash -c "$*" ;;
esac
echo "ssh falso: no se espera '$host'" >&2; exit 1
FAKE
chmod +x "$S/bin/ssh"

SECRETOS="$HOME_MINI/.motor/secretos.env"
printf 'MOTOR_TELEGRAM_TOKEN=abc\n# nota\nMOTOR_KUMA_URL=http://viejo/api/push/DAEMON\nMOTOR_X=1' >"$SECRETOS"
chmod 644 "$SECRETOS"
TOKEN=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef
SCRIPT="$RAIZ/ops/senal-kuma-encargos.sh"

lineas() { grep -c "^$1=" "$SECRETOS" || true; }

espera "sin acción falla" 2 bash "$SCRIPT"
espera "token corto falla" 1 bash -c "printf 'corto\n' | bash '$SCRIPT' poner"
[ "$(lineas MOTOR_KUMA_ENCARGOS_URL)" = 0 ] || fallo "un token inválido no debe escribir nada"

espera "poner" 0 bash -c "printf '%s\n' '$TOKEN' | bash '$SCRIPT' poner"
[ "$(lineas MOTOR_KUMA_ENCARGOS_URL)" = 1 ] || fallo "poner: debe haber una sola línea MOTOR_KUMA_ENCARGOS_URL"
grep -qx "MOTOR_KUMA_ENCARGOS_URL=http://10.13.13.1:3001/api/push/$TOKEN" "$SECRETOS" || fallo "poner: la URL no es la del monitor"
grep -qx 'MOTOR_KUMA_URL=http://viejo/api/push/DAEMON' "$SECRETOS" || fallo "poner: no debe tocar MOTOR_KUMA_URL del daemon"
grep -qx 'MOTOR_X=1' "$SECRETOS" && grep -qx 'MOTOR_TELEGRAM_TOKEN=abc' "$SECRETOS" || fallo "poner: perdió otras líneas"
[ "$(stat -f %Lp "$SECRETOS" 2>/dev/null || stat -c %a "$SECRETOS")" = 600 ] || fallo "poner: secretos.env debe quedar en 600"
compgen -G "$HOME_MINI/.motor/*nuevo*" >/dev/null && fallo "poner: quedó un temporal"
grep -qF "$TOKEN" "$S/salida" && fallo "poner: la salida contiene el token"
grep -qF "$TOKEN" "$FAKE_LOG" && fallo "poner: una línea de comandos de ssh contiene el token"

espera "poner otra vez" 0 bash -c "printf '%s\n' '$TOKEN' | bash '$SCRIPT' poner"
[ "$(lineas MOTOR_KUMA_ENCARGOS_URL)" = 1 ] || fallo "poner dos veces no debe duplicar la línea"

espera "quitar" 0 bash -c "bash '$SCRIPT' quitar </dev/null"
[ "$(lineas MOTOR_KUMA_ENCARGOS_URL)" = 0 ] || fallo "quitar: debe salir la línea de los encargos"
grep -qx 'MOTOR_KUMA_URL=http://viejo/api/push/DAEMON' "$SECRETOS" || fallo "quitar: no debe quitar MOTOR_KUMA_URL del daemon"
grep -qx 'MOTOR_X=1' "$SECRETOS" || fallo "quitar: perdió otra línea"

termina
