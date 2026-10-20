#!/usr/bin/env bash
# Prueba el SHA dado en la PC Windows (ssh gwpc): lo manda con git archive a una carpeta
# temporal, corre test_puro + test_windows con uv y borra la carpeta. Sale != 0 si algo falla,
# si ssh falla o si test_windows se saltó (salvo la prueba de WSL cuando la PC no tiene distro).
set -uo pipefail
export LC_ALL=C

sha="${1:?uso: ops/prueba-pc.sh <sha>}"
sha="$(git rev-parse --verify "$sha^{commit}")" || exit 2
dir="motor-prueba-$sha"
host="${MOTOR_PC:-gwpc}"
uv='C:\Users\ehven\.local\bin\uv.exe'
carpeta="%TEMP%\\$dir"
salida="$(mktemp)"

limpiar() { ssh -o BatchMode=yes "$host" "(if exist $carpeta rmdir /s /q $carpeta)" >/dev/null 2>&1; }
trap 'limpiar; rm -f "$salida"' EXIT

ssh -o BatchMode=yes -o ConnectTimeout=15 "$host" "(if exist $carpeta rmdir /s /q $carpeta) & mkdir $carpeta" || { echo "ssh/mkdir falló" >&2; exit 3; }
git archive "$sha" | ssh -o BatchMode=yes "$host" "tar -x -C $carpeta" || { echo "envío del SHA falló" >&2; exit 3; }

ssh -o BatchMode=yes "$host" "cd /d $carpeta && $uv run --python 3.12 python -m unittest -v tests.test_puro tests.test_windows" >"$salida" 2>&1
codigo=$?
tr -d '\r' <"$salida"
if [ "$codigo" -ne 0 ]; then echo "FALLÓ en la PC (código $codigo)" >&2; exit "$codigo"; fi

# unittest sale 0 con skips: test_windows debe haber CORRIDO. Solo se tolera la prueba de WSL sin distro.
corridas="$(tr -d '\r' <"$salida" | grep -cE '^test_.*\(tests\.test_windows\..*\) \.\.\. ok$')"
saltadas="$(tr -d '\r' <"$salida" | grep -E '\(tests\.test_windows\..*\) \.\.\. skipped' | grep -vc 'sin distro WSL')"
if [ "$corridas" -lt 1 ] || [ "$saltadas" -gt 0 ]; then
  echo "test_windows se saltó (corridas=$corridas, saltadas no toleradas=$saltadas)" >&2; exit 4
fi
echo "OK en la PC: $corridas pruebas de test_windows corrieron"
