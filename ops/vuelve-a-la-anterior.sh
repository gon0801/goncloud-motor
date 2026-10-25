#!/usr/bin/env bash
# Marcha atrás de la receta: deja cada máquina en la etiqueta que tenía antes de la última
# entrega (ops/instala-todo.sh la anota en ~/.motor/etiqueta-anterior antes de cambiarla).
# El motor la corre cada vez que la entrega sale ≠0, también cuando falló antes de tocar una
# máquina: solo se mueve la máquina cuyo commit en ~/motor (`git rev-parse HEAD`) es el commit
# de esta entrega (MOTOR_SHA, o el HEAD de la raíz si se corre a mano); las demás se quedan
# como están, porque su marca es de una entrega más vieja. El commit se compara por SHA, no
# por etiqueta: una instalación que se negó deja a cada máquina en otro commit y nadie se
# mueve. La etiqueta anterior se usa de la copia local (está en la máquina porque se instaló
# antes) y solo si no está se trae de origin: nunca se consulta origin para decidir quién se
# mueve, así que sin origin la máquina que debe volver vuelve igual y las demás no se mueven.
# Sin etiqueta anotada, la máquina se queda como está. Nunca descarga el servicio ni borra la
# tarea programada: con B.6, cada daemon se reinicia solo al ver la versión de vuelta en
# disco, así que basta con el checkout. Sale != 0 si alguna máquina falla (las demás se
# intentan igual y se dice cuál falló).
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

vuelve_posix() {
  local m=$1
  en_posix "$m" "$m" "$entrega" <<'REMOTO'
set -euo pipefail
m=$1 entrega=$2
motor="$HOME/motor" marca="$HOME/.motor/etiqueta-anterior"
if [ ! -s "$marca" ]; then echo "$m: sin etiqueta anterior anotada; se queda como está"; exit 0; fi
etiqueta="$(cat "$marca")"
actual="$(git -C "$motor" rev-parse HEAD)"
if [ "$actual" != "$entrega" ]; then
  echo "$m: está en ${actual:0:12}, no en el commit de la entrega ${entrega:0:12}; se queda"
  exit 0
fi
if ! git -C "$motor" rev-parse -q --verify "refs/tags/$etiqueta" >/dev/null; then
  git -C "$motor" fetch -q --tags --force origin
fi
if ! git -C "$motor" cat-file -e "refs/tags/$etiqueta:motor/formato.py" 2>/dev/null; then
  if [ -f "$HOME/.motor/formato" ]; then
    if python3 -c "import tomllib" 2>/dev/null; then py="python3"; else py="uv run --python 3.12 python"; fi
    if ! (cd "$motor" && PYTHONPATH=. $py -m motor formato --home "$HOME/.motor"); then
      echo "$m: hay una intencion sin cerrar; no cambio a una etiqueta que no la entiende" >&2
      exit 1
    fi
  fi
fi
git -C "$motor" checkout -q --detach "refs/tags/$etiqueta"
echo "$m: motor $(git -C "$motor" rev-parse --short=12 HEAD) de vuelta en $etiqueta"
REMOTO
}

vuelve_pc() {
  local u='%USERPROFILE%' h anterior actual
  h="$(host_de pc)"
  anterior="$(ssh_a "$h" "type $u\\.motor\\etiqueta-anterior 2>nul" 2>/dev/null | tr -d '[:space:]')" || anterior=""
  if [ -z "$anterior" ]; then
    # ¿sin marca, o no hubo ssh? Un ssh que sí responde distingue ambos casos.
    ssh_a "$h" "echo ok" >/dev/null 2>&1 || { echo "pc: no hay ssh" >&2; return 1; }
    echo "pc: sin etiqueta anterior anotada; se queda como está"
    return 0
  fi
  # Un ssh caído acá es error, no "se queda": la salida vacía fingiría otro commit.
  actual="$(ssh_a "$h" "git -C $u\\motor rev-parse HEAD" | tr -d '[:space:]')" || return 1
  [ -n "$actual" ] || { echo "pc: no pude ver el commit de ~/motor" >&2; return 1; }
  if [ "$actual" != "$entrega" ]; then
    echo "pc: está en ${actual:0:12}, no en el commit de la entrega ${entrega:0:12}; se queda"
    return 0
  fi
  if ! ssh_a "$h" "git -C $u\\motor rev-parse -q --verify refs/tags/$anterior" >/dev/null; then
    ssh_a "$h" "git -C $u\\motor fetch -q --tags --force origin" || return 1
  fi
  if ! ssh_a "$h" "git -C $u\\motor cat-file -e refs/tags/$anterior:motor/formato.py"; then
    if ! marca="$(ssh_a "$h" "if exist $u\\.motor\\formato echo si" | tr -d "[:space:]")"; then
      return 1
    fi
    if [ "$marca" = si ]; then
      ssh_a "$h" "cd /d $u\\motor && $PC_UV run --python 3.12 python -m motor formato --home $u\\.motor" || return 1
    fi
  fi
  ssh_a "$h" "git -C $u\\motor checkout -q --detach refs/tags/$anterior" || return 1
  echo "pc: motor de vuelta en $anterior"
}

raiz="${MOTOR_INSTALA_RAIZ:-$(cd "$aqui/.." && pwd)}"
pedido="${MOTOR_SHA:-HEAD}"
if ! entrega="$(git -C "$raiz" rev-parse "$pedido^{commit}" 2>/dev/null)"; then
  echo "no pude saber el commit de esta entrega (MOTOR_SHA='${MOTOR_SHA:-}', raiz=$raiz)" >&2
  exit 2
fi

movidas="" fallaron=""
for m in $MAQUINAS; do
  echo "== $m"
  if [ "$m" = pc ]; then out="$(vuelve_pc)"; else out="$(vuelve_posix "$m")"; fi
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '%s\n' "$out"
    case "$out" in *"de vuelta en"*) movidas="$movidas $m" ;; esac
  else
    printf '%s\n' "$out" >&2
    echo "FALLÓ $m" >&2
    fallaron="$fallaron $m"
  fi
done
if [ -n "$fallaron" ]; then echo "No quedó en la etiqueta anterior:$fallaron" >&2; exit 1; fi
if [ -z "$movidas" ]; then
  echo "nadie se mueve (el servicio sigue cargado en las tres)."
else
  echo "De vuelta en su etiqueta anterior:$movidas (el servicio sigue cargado en las tres)."
fi
