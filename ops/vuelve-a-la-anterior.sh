#!/usr/bin/env bash
# Marcha atrás de la receta: deja cada máquina en la etiqueta que tenía antes de la última
# entrega (ops/instala-todo.sh la anota en ~/.motor/etiqueta-anterior antes de cambiarla).
# Nunca descarga el servicio ni borra la tarea programada: con B.6, cada daemon se reinicia
# solo al ver la versión de vuelta en disco, así que basta con el checkout. Sin etiqueta
# anotada, la máquina se queda como está. Sale != 0 si alguna máquina falla (las demás se
# intentan igual y se dice cuál falló).
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

vuelve_posix() {
  local m=$1
  en_posix "$m" "$m" <<'REMOTO'
set -euo pipefail
m=$1
motor="$HOME/motor" marca="$HOME/.motor/etiqueta-anterior"
if [ ! -s "$marca" ]; then echo "$m: sin etiqueta anterior anotada; se queda como está"; exit 0; fi
etiqueta="$(cat "$marca")"
git -C "$motor" fetch -q --tags --force origin
git -C "$motor" checkout -q --detach "refs/tags/$etiqueta"
echo "$m: motor $(git -C "$motor" rev-parse --short=12 HEAD) de vuelta en $etiqueta"
REMOTO
}

vuelve_pc() {
  local u='%USERPROFILE%' h anterior
  h="$(host_de pc)"
  anterior="$(ssh_a "$h" "type $u\\.motor\\etiqueta-anterior 2>nul" 2>/dev/null | tr -d '[:space:]')" || anterior=""
  if [ -z "$anterior" ]; then
    # ¿sin marca, o no hubo ssh? Un ssh que sí responde distingue ambos casos.
    ssh_a "$h" "echo ok" >/dev/null 2>&1 || { echo "pc: no hay ssh" >&2; return 1; }
    echo "pc: sin etiqueta anterior anotada; se queda como está"
    return 0
  fi
  ssh_a "$h" "git -C $u\\motor fetch -q --tags --force origin" || return 1
  ssh_a "$h" "git -C $u\\motor checkout -q --detach refs/tags/$anterior" || return 1
  echo "pc: motor de vuelta en $anterior"
}

fallaron=""
for m in $MAQUINAS; do
  echo "== $m"
  if [ "$m" = pc ]; then vuelve_pc; else vuelve_posix "$m"; fi || { echo "FALLÓ $m" >&2; fallaron="$fallaron $m"; }
done
if [ -n "$fallaron" ]; then echo "No quedó en la etiqueta anterior:$fallaron" >&2; exit 1; fi
echo "Las tres máquinas están en su etiqueta anterior (el servicio sigue cargado en las tres)."
