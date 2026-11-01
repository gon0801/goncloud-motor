# shellcheck shell=bash disable=SC2034
# Lo común de las pruebas de ops/: un ssh falso en el PATH, un HOME de mentira y verificadores.
# Se carga con `.`. Nada de esto toca las máquinas reales.
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$(mktemp -d)"
trap 'rm -rf "$S"' EXIT
mkdir -p "$S/bin" "$S/mac" "$S/mini"
export FAKE_LOG="$S/ssh.log" S
: >"$FAKE_LOG"
PATH="$S/bin:$PATH"
printf '%s\n' '#!/usr/bin/env bash' 'echo "ssh real en una prueba" >&2' 'exit 99' >"$S/bin/ssh"
chmod +x "$S/bin/ssh"
fallos=0

fallo() { echo "  FALLA: $*" >&2; fallos=$((fallos + 1)); }

# espera <nombre> <código|!0> comando... : corre el comando; un número exige ese código exacto, !0 cualquiera distinto de 0.
espera() {
  local nombre=$1 quiero=$2 rc
  shift 2
  "$@" >"$S/salida" 2>&1
  rc=$?
  if { [ "$quiero" = '!0' ] && [ $rc -eq 0 ]; } || { [ "$quiero" != '!0' ] && [ "$rc" != "$quiero" ]; }; then
    fallo "$nombre: salió $rc y se esperaba $quiero"
    sed 's/^/    | /' "$S/salida" >&2
  else
    echo "  ok: $nombre"
  fi
}

contiene() { # contiene <nombre> <archivo> <texto>
  grep -qF -- "$3" "$2" || fallo "$1: '$3' no está en $2"
}

termina() {
  if [ "$fallos" -gt 0 ]; then echo "$fallos prueba(s) fallaron" >&2; exit 1; fi
  echo "todo bien"
}
