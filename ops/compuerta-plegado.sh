#!/usr/bin/env bash
# Compuerta de plegado: un commit no se activa en una máquina si no pliega el registro
# de encargos completo de esa máquina. Pliega allá, con el intérprete del supervisor y
# con el código del commit sacado a un temporal (ops/compuerta_plegado.py).
# Solo lee: no mueve árboles, no escribe recibos y el registro no sale de su máquina.
# ops/despliega-encargos.sh la corre antes de mover ningún árbol; también corre sola.
# Sale 0 si todas pliegan y 1 si alguna no pliega o no se pudo comprobar.
#
# --sha SHA      Commit candidato (por omisión, origin/main tal como está en este clon).
# --maquina M    mac, mini o pc, o varias separadas por espacios. Sin ella, las tres.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"
raiz="${MOTOR_DESPLEGAR_RAIZ:-$(cd "$aqui/.." && pwd)}"

maquinas="$MAQUINAS"
sha="origin/${MOTOR_RAMA:-main}"
while [ $# -gt 0 ]; do
  case "$1" in
    --sha)
      [ $# -ge 2 ] || { echo "--sha necesita un SHA" >&2; exit 2; }
      sha="$2"; shift 2 ;;
    --maquina)
      [ $# -ge 2 ] || { echo "--maquina necesita mac, mini o pc" >&2; exit 2; }
      maquinas="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
for m in $maquinas; do
  case "$m" in
    mac|mini|pc) ;;
    *) echo "--maquina es mac, mini o pc, no '$m'" >&2; exit 2 ;;
  esac
done
# shellcheck disable=SC2086
set -- $maquinas
[ $# -gt 0 ] || { echo "--maquina necesita mac, mini o pc" >&2; exit 2; }
maquinas="$*"
commit="$(git -C "$raiz" rev-parse --verify "$sha^{commit}" 2>/dev/null)" \
  || { echo "el SHA '$sha' no existe en $raiz" >&2; exit 2; }
sha="$commit"

COMPUERTA_B64="$(_pc_b64 <"$aqui/compuerta_plegado.py")" || exit 2

compuerta_de() { # <maquina> <arbol>
  local m=$1 arbol=$2
  if [ "$m" = pc ]; then
    py_pc_runtime_prog "$COMPUERTA_B64" "$arbol" "$sha"
  else
    en_posix "$m" "$m" "$COMPUERTA_B64" "$arbol" "$sha" <<'REMOTO'
set -euo pipefail
programa=$2
shift 2
python3 -c 'import base64,sys; programa=base64.b64decode(sys.argv.pop(1)); exec(programa)' "$programa" "$@"
REMOTO
  fi
}

tmp="$(mktemp -d "${TMPDIR:-/tmp}/compuerta.XXXXXX")" || exit 2
trap 'rm -rf "$tmp"' EXIT

# Las máquinas pliegan a la vez: cada una tarda lo que su registro, no la suma.
for m in $maquinas; do
  {
    if arbol="$(ruta_runtime_encargos_de "$m" 2>/dev/null)" && [ -n "$arbol" ]; then
      compuerta_de "$m" "$arbol" 2>/dev/null | tr -d '\r'
      # La salida de la PC llega sin el último salto de línea.
      echo
    else
      echo "resultado no_pude no pude leer el árbol del supervisor"
    fi
  } >"$tmp/$m" &
done
wait

fallan=""
for m in $maquinas; do
  resultado="$(sed -n 's/^resultado //p' "$tmp/$m" | head -n 1)"
  read -r tipo encargos rotos _ <<<"$resultado"
  case "${tipo:-}" in
    pliega) echo "OK $m: el registro pliega entero con ${sha:0:12} ($encargos encargos)" ;;
    sin_registro) echo "OK $m: no hay registro de encargos; nada que plegar" ;;
    no_pliega)
      echo "FALLA $m: $rotos de $encargos encargos no pliegan con ${sha:0:12}" >&2
      fallan="$fallan $m" ;;
    no_pude)
      echo "FALLA $m: no pude comprobar el plegado con ${sha:0:12}: ${resultado#no_pude }" >&2
      fallan="$fallan $m" ;;
    *)
      echo "FALLA $m: no pude comprobar el plegado con ${sha:0:12}: la máquina no contestó" >&2
      fallan="$fallan $m" ;;
  esac
  sed -n "s/^comparar /  $m, frente a la versión instalada: /p" "$tmp/$m" | head -n 1
  sed -n "s/^aviso /AVISO $m: /p" "$tmp/$m" >&2
done

if [ -n "$fallan" ]; then
  echo "${sha:0:12} no pasa la compuerta de plegado en:$fallan" >&2
  exit 1
fi
echo "compuerta de plegado pasada en: $maquinas con ${sha:0:12}"
