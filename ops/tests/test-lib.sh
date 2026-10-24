#!/usr/bin/env bash
# ops/lib.sh con un ssh falso que anota el host y el stdin y no ejecuta nada: corre y en_posix
# van por ssh a las tres máquinas, corra el script en la que corra.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

unset MOTOR_SSH_MAC MOTOR_SSH_MINI MOTOR_SSH_PC
. "$RAIZ/ops/lib.sh"

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
{
  echo "=== $host $*"
  cat
  echo "=== fin"
} >>"$S/stdin.log"
exit 0
FAKE
chmod +x "$S/bin/ssh"

desde() { # desde <origen>
  local origen=$1 d testigo
  mkdir -p "$S/home-$origen/.motor"
  printf 'nombre = "%s"\n' "$origen" >"$S/home-$origen/.motor/maquina.toml"
  export HOME="$S/home-$origen"
  : >"$FAKE_LOG"
  : >"$S/stdin.log"
  rm -f "$S"/testigo-*
  for d in mac mini pc; do
    testigo="$S/testigo-$origen-$d"
    corre "$d" "touch $testigo" "touch $testigo" </dev/null || return 1
    printf 'touch %s-enposix\n' "$testigo" | en_posix "$d" || return 1
  done
  for d in mac mini pc; do
    grep -q "^$d ::" "$FAKE_LOG" || return 1
  done
  [ "$(wc -l <"$FAKE_LOG" | tr -d ' ')" = 6 ] || return 1
  compgen -G "$S/testigo-*" >/dev/null && return 1
  return 0
}

espera "desde mac todo va por ssh" 0 desde mac
espera "desde mini todo va por ssh" 0 desde mini
espera "desde pc todo va por ssh" 0 desde pc

pasa_stdin() {
  export HOME="$S/home-mac"
  : >"$FAKE_LOG"
  : >"$S/stdin.log"
  rm -f "$S/testigo-stdin"
  printf 'touch %s\nMARCA-STDIN-123\n' "$S/testigo-stdin" | en_posix mac || return 1
  grep -q '^mac ::' "$FAKE_LOG" || return 1
  grep -qF "MARCA-STDIN-123" "$S/stdin.log" || return 1
  [ ! -e "$S/testigo-stdin" ] || return 1
}
espera "en_posix pasa el script por stdin" 0 pasa_stdin

ataja() {
  local o rc
  o="$(bash -c '. "$1/ops/tests/lib-prueba.sh"; ssh maquina-real' _ "$RAIZ" 2>&1)"
  rc=$?
  [ "$rc" = 99 ] || return 1
  grep -qF "ssh real en una prueba" <<<"$o"
}
espera "lib-prueba ataja el ssh real" 0 ataja

termina
