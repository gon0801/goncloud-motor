#!/usr/bin/env bash
# ops/comprueba-despliegue.sh con un ssh falso: una máquina que no responde,
# un recibo con otro SHA, un supervisor sin pasada y el modo --ensayo que no
# llama a ssh. No toca ninguna máquina real.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

SHA="1111111111111111111111111111111111111111"
OTRO="2222222222222222222222222222222222222222"
export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=mini MOTOR_SSH_PC=gwpc

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_LOG"
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
cat >/dev/null 2>&1 || true
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
# La PC contesta como cmd.exe y Python en Windows: cada línea termina en CRLF.
crlf() {
  if [ "$host" = gwpc ]; then sed 's/\r$//' | sed 's/$/\r/'; else cat; fi
}
archivo="$S/estado-$host"
[ -f "$S/dinamico-$host" ] && { bash "$S/dinamico-$host" | crlf; exit 0; }
[ -f "$archivo" ] || archivo="$S/estado-default"
[ -f "$archivo" ] || exit 1
crlf <"$archivo"
FAKE
chmod +x "$S/bin/ssh"

estado() { # estado <host> <recibo> <vivo> <sha-pasada> [sha-arbol]
  printf 'recibo %s\nvivo %s\npasada %s 2026-11-08T10:00:00Z completa\nruta /runtime/%s\narbol %s\nregistro 0 0\n' \
    "$2" "$3" "$4" "$1" "${5:-$4}" >"$S/estado-$1"
}

estado mac "$SHA" si "$SHA"
estado mini "$SHA" si "$SHA"
estado gwpc "$SHA" si "$SHA"
cp "$S/estado-mac" "$S/estado-default"

SCRIPT="$RAIZ/ops/comprueba-despliegue.sh"

espera "las tres máquinas comprueban el SHA nuevo" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "el resultado nombra las tres" "$S/salida" "OK mac"
contiene "el resultado nombra la mini" "$S/salida" "OK mini"
contiene "el resultado nombra la pc" "$S/salida" "OK pc"

# La PC real contesta cada línea con \r\n. El comprobador tiene que quitar el
# \r antes de comparar, o el mismo SHA deja de ser igual a sí mismo.
espera "la PC con CRLF y estado bueno pasa" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina pc
contiene "la PC con CRLF se comprueba" "$S/salida" "OK pc: recibo 111111111111"

: >"$FAKE_LOG"
espera "una máquina que no responde hace fallar la comprobación" '!0' \
  env FAKE_CAE=mini bash "$SCRIPT" --sha "$SHA" --maquina "mac mini"
contiene "la causa nombra la mini" "$S/salida" "FALLA mini: no responde"

estado mini "$OTRO" si "$OTRO"
espera "un recibo con otro SHA falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
contiene "la causa nombra el SHA viejo" "$S/salida" "recibo en"

estado mini "$SHA" si "$OTRO"
espera "un supervisor sin pasada con el SHA nuevo falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
contiene "la causa nombra la pasada" "$S/salida" "sin pasada"

estado mini "$SHA" no "$SHA"
espera "un supervisor no vivo falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
contiene "la causa nombra al supervisor" "$S/salida" "supervisor no vivo"

# El árbol que ejecuta el supervisor no es el del recibo: el despliegue no está
# comprobado aunque la pasada diga el SHA, porque el supervisor correría otro
# código. Y sin árbol en la pasada no hay nada que comprobar.
estado mini "$SHA" si "$SHA" "$OTRO"
espera "un árbol del supervisor en otro SHA falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
contiene "la causa nombra el árbol" "$S/salida" "el árbol del supervisor está en"

printf 'recibo %s\nvivo si\npasada %s 2026-11-08T10:00:00Z completa\narbol %s\nregistro 0 0\n' \
  "$SHA" "$SHA" "$SHA" >"$S/estado-mini"
espera "una pasada sin árbol del supervisor falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
contiene "la causa pide el árbol" "$S/salida" "no dice qué árbol"

# El registro de encargos tiene que plegar entero: recibo, pasada y árbol no bastan.
printf 'recibo %s\nvivo si\npasada %s 2026-11-08T10:00:00Z completa\nruta /runtime/mini\narbol %s\nregistro 3 1\n' \
  "$SHA" "$SHA" "$SHA" >"$S/estado-mini"
espera "un registro que no pliega entero falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
contiene "la causa nombra el registro" "$S/salida" "el registro no pliega entero"

printf 'recibo %s\nvivo si\npasada %s 2026-11-08T10:00:00Z completa\nruta /runtime/mini\narbol %s\n' \
  "$SHA" "$SHA" "$SHA" >"$S/estado-mini"
espera "sin dato del registro la comprobación falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
contiene "la causa pide el registro" "$S/salida" "no dice si el registro pliega"

# El despliegue ordenó el reinicio: si la pasada no es nueva, la causa lo dice.
estado mini "$SHA" si "$OTRO"
espera "un reinicio sin pasada nueva se dice" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini --reinicio
contiene "la causa nombra el reinicio" "$S/salida" "el reinicio falló"
estado mini "$SHA" si "$SHA"
espera "con reinicio y pasada nueva pasa" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina mini --reinicio

# Un estado malo de la PC con CRLF: las causas tienen que ser las de verdad,
# sin el "está en X (se esperaba X)" ni "0 fallan" que producía el \r.
printf 'recibo %s\r\nvivo no\r\npasada %s 2026-11-08T09:00:00Z parcial\r\nruta /runtime/gwpc\r\narbol %s\r\nregistro 3 1\r\n' \
  "$OTRO" "$OTRO" "$OTRO" >"$S/estado-gwpc"
espera "la PC con CRLF y estado malo falla" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina pc
contiene "la causa nombra el recibo real" "$S/salida" "recibo en 222222222222 (se esperaba 111111111111)"
contiene "la causa ve al supervisor caído" "$S/salida" "supervisor no vivo"
contiene "la causa ve la pasada vieja" "$S/salida" "sin pasada con 111111111111"
contiene "la causa ve la pasada parcial" "$S/salida" "la última pasada no fue completa"
contiene "la causa ve el árbol viejo" "$S/salida" "el árbol del supervisor está en 222222222222 (se esperaba 111111111111)"
contiene "la causa ve el registro real" "$S/salida" "el registro no pliega entero (3 encargos, 1 fallan)"
no_contiene "el árbol no se contradice" "$S/salida" "está en 111111111111 (se esperaba 111111111111)"
no_contiene "el registro no dice 0 fallan" "$S/salida" "0 fallan"
estado gwpc "$SHA" si "$SHA"

: >"$FAKE_LOG"
espera "--ensayo no llama a ssh" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc" --ensayo
[ ! -s "$FAKE_LOG" ] || fallo "--ensayo llamó a ssh: $(cat "$FAKE_LOG")"

estado mini "$SHA" si "$SHA"
espera "repetir la comprobación es idempotente" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina mini
espera "y otra vez da el mismo resultado" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina mini

# La pasada nueva puede llegar después de instalar: la espera es acotada y
# termina en verde cuando aparece, sin reiniciar ni desplegar nada.
cat >"$S/dinamico-mini" <<SH
n="\$(cat "$S/cuenta-mini" 2>/dev/null || echo 0)"
n=\$((n + 1)); echo "\$n" >"$S/cuenta-mini"
if [ "\$n" -lt 2 ]; then
  printf 'recibo %s\nvivo si\npasada %s 2026-11-08T09:00:00Z completa\nruta /runtime/mini\narbol %s\nregistro 0 0\n' "$SHA" "$OTRO" "$OTRO"
else
  printf 'recibo %s\nvivo si\npasada %s 2026-11-08T10:01:00Z completa\nruta /runtime/mini\narbol %s\nregistro 0 0\n' "$SHA" "$SHA" "$SHA"
fi
SH
espera "una pasada nueva dentro de la espera acotada pasa" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina mini --espera 5
[ "$(cat "$S/cuenta-mini")" -ge 2 ] || fallo "la comprobación no reintentó la pasada"
rm -f "$S/dinamico-mini" "$S/cuenta-mini"

estado mini "$SHA" si "$OTRO"
espera "una pasada vieja no espera para siempre" '!0' \
  bash "$SCRIPT" --sha "$SHA" --maquina mini --espera 1
contiene "la causa nombra la pasada que no llega" "$S/salida" "sin pasada"

termina
