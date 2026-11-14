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

flota() { # flota <host> <tabla activa> [primera línea del manifiesto deseado] [puesto activo]
  local f="$S/home-$1/flota" gen="$S/home-$1/flota/generaciones/gen-$1"
  rm -rf "$f"
  mkdir -p "$gen"
  printf 'generacion = "%s"\n\n[[puesto]]\nfuncion = "%s"\n' "$2" "${4:-dirigir}" >"$gen/manifesto.toml"
  if [ -n "${3:-}" ]; then
    printf '%s\n' "$3" '' '[[puesto]]' 'funcion = "dirigir"' >"$f/manifiesto.toml"
  else
    cp "$gen/manifesto.toml" "$f/manifiesto.toml"
  fi
  printf '{"modo": "replica", "snapshot_sha256": "%s"}\n' "$SNAPSHOT" >"$f/fuente-compartida.json"
  ln -s "$gen" "$f/actual"
}

huella() { # huella <nombre>: lo que imprime el comprobador en vez de un nombre fuera del juego seguro
  python3 -c 'import hashlib,sys; print("#" + hashlib.sha256(sys.argv[1].encode("utf-8")).hexdigest()[:12])' "$1"
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
grep 'el mismo manifiesto' "$S/salida" | grep -q ' en mac; [0-9a-f]\{12\} en mini, pc$' \
  || fallo "otra tabla activa es otro manifiesto activo: $(grep 'AVISO flota' "$S/salida")"
flota mac v9

# Una actualización pendiente escribe el manifiesto deseado antes de activar su generación:
# se compara lo que corre, y el deseado se muestra aparte.
flota mini v9 'generacion = "v10"'
espera "una actualización pendiente en la mini comprueba el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
no_contiene "la mini corre el mismo manifiesto que las demás" "$S/salida" "el mismo manifiesto"
grep '^FLOTA mini:' "$S/salida" | grep -q ', manifiesto deseado [0-9a-f]\{12\}$' \
  || fallo "la mini no muestra su manifiesto deseado: $(grep '^FLOTA mini:' "$S/salida")"
[ "$(grep '^FLOTA' "$S/salida" | awk '{print $NF}' | sort -u | wc -l)" -eq 2 ] \
  || fallo "el manifiesto deseado de la mini no se distingue: $(grep '^FLOTA' "$S/salida")"

flota mini v9 'generacion = "v9"' revisar
espera "una máquina con otro manifiesto activo avisa sin fallar el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "el aviso nombra a la mini" "$S/salida" "AVISO flota: las máquinas no corren el mismo manifiesto: "
grep 'el mismo manifiesto' "$S/salida" | grep -q '; [0-9a-f]\{12\} en mini$' \
  || fallo "el aviso no separa a la mini: $(grep 'AVISO flota' "$S/salida")"
flota mini v9

# El motor acepta cualquier nombre de generación no vacío: cada uno se compara por lo que es
# y, fuera del juego seguro, se imprime su huella en vez del nombre.
flota mac 'v9+fix'
flota mini 'release/2026-11'
espera "nombres de tabla fuera del juego seguro comprueban el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "la mac da la huella de su tabla" "$S/salida" "FLOTA mac: tabla $(huella 'v9+fix'),"
contiene "el aviso separa cada tabla" "$S/salida" \
  "AVISO flota: las máquinas no corren la misma tabla de modelos: $(huella 'v9+fix') en mac; $(huella 'release/2026-11') en mini; v9 en pc"
no_contiene "el nombre fuera del juego seguro no se imprime" "$S/salida" "v9+fix"
no_contiene "un nombre con / no se imprime" "$S/salida" "release/"
flota mac v9
flota mini v9

# El motor resuelve un home relativo de flota-home.json contra su base (~/.motor-encargos).
mv "$S/home-mac/flota" "$S/home-mac/flota-movida"
ln -sfn "$S/home-mac/flota-movida/generaciones/gen-mac" "$S/home-mac/flota-movida/actual"
printf '{"home": "../flota-movida"}\n' >"$S/home-mac/.motor-encargos/flota-home.json"
espera "un home relativo de la flota comprueba el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "la mac encuentra su flota contra la base del servicio" "$S/salida" "FLOTA mac: tabla v9"
no_contiene "la flota movida es la misma" "$S/salida" "AVISO flota"
rm -r "$S/home-mac/.motor-encargos/flota-home.json" "$S/home-mac/flota-movida"
flota mac v9

# recuperar deja sin-medir.json cuando una lectura de la generación no terminó a tiempo.
for host in mini gwpc; do
  printf '{"generacion": "%s", "orden": "/ruta/python comprobar.py", "tope": 120, "causa": "x"}\n' \
    "$SNAPSHOT" >"$S/home-$host/flota/sin-medir.json"
done
espera "una flota sin medir avisa sin fallar el despliegue" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "el aviso nombra la generación y el tope" "$S/salida" \
  "AVISO mini: la flota no midió la generación ${SNAPSHOT:0:12} (tope 120 s); vuelve a correr motor flota recuperar"
contiene "la pc avisa igual" "$S/salida" "AVISO pc: la flota no midió la generación ${SNAPSHOT:0:12} (tope 120 s);"
no_contiene "el aviso no imprime la orden" "$S/salida" "/ruta/python"
no_contiene "una flota medida no avisa" "$S/salida" "AVISO mac: la flota"
rm "$S/home-mini/flota/sin-medir.json" "$S/home-gwpc/flota/sin-medir.json"

rm -rf "$S/home-gwpc/flota"
espera "una máquina sin flota no revienta la comprobación" 0 \
  bash "$SCRIPT" --sha "$SHA" --maquina "mac mini pc"
contiene "la pc dice que no tiene flota" "$S/salida" "FLOTA pc: sin flota"
contiene "el aviso nombra a la pc sin flota" "$S/salida" \
  "AVISO flota: las máquinas no corren la misma tabla de modelos: v9 en mac, mini; sin flota en pc"

termina
