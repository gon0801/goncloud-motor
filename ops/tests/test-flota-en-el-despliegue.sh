#!/usr/bin/env bash
# ops/comprueba-despliegue.sh compara la flota de las tres máquinas. El ssh falso
# corre el programa real que manda el comprobador, cada máquina con su HOME de
# mentira: un despliegue bueno y una ~/flota armada por la prueba.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=mini MOTOR_SSH_PC=gwpc
SHA="$(git -C "$RAIZ" rev-parse HEAD)"
SNAPSHOT="f483df47ea4444366213a48306c8df46181544206571acd163a3fdb5e202b01a"

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1
export HOME="$S/home-$host"
if [ "$host" = gwpc ]; then
  # La PC recibe el programa en base64 por stdin y contesta cada línea con CRLF.
  python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))' \
    | python3 - 0 | sed 's/$/\r/'
else
  bash -s
fi
FAKE
chmod +x "$S/bin/ssh"

despliegue() { # despliegue <host>: el motor de encargos en el SHA, con un supervisor vivo
  local me="$S/home-$1/.motor-encargos"
  mkdir -p "$me"
  printf '{"destino": {"codigo": "%s"}}\n' "$SHA" >"$me/instalacion.json"
  printf '{"sha": "%s", "hora": "2026-11-14T10:00:00Z", "completa": true, "pid": %s, "ruta": "%s"}\n' \
    "$SHA" "$$" "$RAIZ" >"$me/supervisor-pasada.json"
}

flota() { # flota <host> <tabla de la generación activa> [manifiesto vigente]
  local f="$S/home-$1/flota" gen="$S/home-$1/flota/generaciones/gen-$1"
  rm -rf "$f"
  mkdir -p "$gen"
  printf '%s\n' "${3:-generacion = \"v9\"}" '' '[[puesto]]' 'funcion = "dirigir"' >"$f/manifiesto.toml"
  printf '{"modo": "replica", "snapshot_sha256": "%s"}\n' "$SNAPSHOT" >"$f/fuente-compartida.json"
  printf 'generacion = "%s"\n\n[[puesto]]\n' "$2" >"$gen/manifesto.toml"
  ln -s "$gen" "$f/actual"
}

a_crlf() { sed 's/$/\r/' "$1" >"$1.crlf" && mv "$1.crlf" "$1"; }

SCRIPT="$RAIZ/ops/comprueba-despliegue.sh"
for host in mac mini gwpc; do despliegue "$host"; flota "$host" v9; done

# Cada generación activa tiene otro ID, porque incluye el runtime del host: no cuenta.
espera "tres máquinas con la misma flota comprueban el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "la mac da su huella" "$S/salida" "FLOTA mac: tabla v9, manifiesto "
contiene "la huella trae la fuente compartida" "$S/salida" "fuente ${SNAPSHOT:0:12}"
contiene "la pc da su huella" "$S/salida" "FLOTA pc: tabla v9"
no_contiene "tres flotas iguales no avisan" "$S/salida" "AVISO flota"
[ "$(grep '^FLOTA' "$S/salida" | awk '{print $6}' | sort -u | wc -l)" -eq 1 ] \
  || fallo "las tres huellas no dan el mismo manifiesto: $(grep '^FLOTA' "$S/salida")"
grep '^FLOTA' "$S/salida" | grep -q / && fallo "una huella imprime una ruta: $(grep '^FLOTA' "$S/salida")"

# La PC escribe sus archivos con CRLF: el mismo contenido no es otra flota.
a_crlf "$S/home-gwpc/flota/manifiesto.toml"
a_crlf "$S/home-gwpc/flota/generaciones/gen-gwpc/manifesto.toml"
espera "el manifiesto de la pc con CRLF comprueba el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "la pc con CRLF lee su tabla" "$S/salida" "FLOTA pc: tabla v9"
no_contiene "CRLF no es una diferencia" "$S/salida" "AVISO flota"

flota mac v8
espera "una máquina con otra tabla avisa sin fallar el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "el aviso nombra a la mac y su tabla" "$S/salida" \
  "AVISO flota: las máquinas no corren la misma tabla de modelos: v8 en mac; v9 en mini, pc"
no_contiene "el manifiesto vigente es el mismo" "$S/salida" "el mismo manifiesto"
flota mac v9

flota mini v9 'generacion = "v10"'
espera "una máquina con otro manifiesto avisa sin fallar el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "el aviso nombra a la mini" "$S/salida" "AVISO flota: las máquinas no corren el mismo manifiesto: "
grep 'el mismo manifiesto' "$S/salida" | grep -q '; [0-9a-f]\{12\} en mini$' \
  || fallo "el aviso no separa a la mini: $(grep 'AVISO flota' "$S/salida")"
flota mini v9

rm -rf "$S/home-gwpc/flota"
espera "una máquina sin flota no revienta la comprobación" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "la pc dice que no tiene flota" "$S/salida" "FLOTA pc: sin flota"
contiene "el aviso nombra a la pc sin flota" "$S/salida" \
  "AVISO flota: las máquinas no corren la misma tabla de modelos: v9 en mac, mini; sin flota en pc"

termina
