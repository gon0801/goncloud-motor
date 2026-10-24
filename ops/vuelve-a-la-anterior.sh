#!/usr/bin/env bash
# Marcha atrás de la receta: deja cada máquina en la etiqueta que tenía antes de la última
# entrega (ops/instala-todo.sh la anota en ~/.motor/etiqueta-anterior antes de cambiarla).
# El motor la corre cada vez que la entrega sale ≠0, también cuando falló antes de tocar una
# máquina: solo se mueve la máquina que quedó en la etiqueta de la entrega ($ETIQUETA); las
# demás se quedan como están, porque su marca es de una entrega más vieja. Sin etiqueta
# anotada, la máquina se queda como está. Nunca descarga el servicio ni borra la tarea
# programada: con B.6, cada daemon se reinicia solo al ver la versión de vuelta en disco, así
# que basta con el checkout. Sale != 0 si alguna máquina falla (las demás se intentan igual y
# se dice cuál falló).
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

vuelve_posix() {
  local m=$1
  en_posix "$m" "$m" "$ETIQUETA" <<'REMOTO'
set -euo pipefail
m=$1 entrega=$2
motor="$HOME/motor" marca="$HOME/.motor/etiqueta-anterior"
if [ ! -s "$marca" ]; then echo "$m: sin etiqueta anterior anotada; se queda como está"; exit 0; fi
etiqueta="$(cat "$marca")"
if ! git -C "$motor" tag --points-at HEAD | grep -qxF "$entrega"; then
  echo "$m: no está en $entrega; se queda"
  exit 0
fi
git -C "$motor" fetch -q --tags --force origin
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
  # Un ssh caído acá es error, no "se queda": la salida vacía fingiría otra etiqueta.
  actual="$(ssh_a "$h" "git -C $u\\motor tag --points-at HEAD")" || return 1
  actual="$(printf '%s\n' "$actual" | tr -d '\r')"
  printf '%s\n' "$actual" | grep -qxF "$ETIQUETA" || { echo "pc: no está en $ETIQUETA; se queda"; return 0; }
  ssh_a "$h" "git -C $u\\motor fetch -q --tags --force origin" || return 1
  ssh_a "$h" "git -C $u\\motor checkout -q --detach refs/tags/$anterior" || return 1
  echo "pc: motor de vuelta en $anterior"
}

# Una instalación que se negó (la etiqueta ya estaba publicada en otro commit) no instaló nada:
# las máquinas en $ETIQUETA vienen de una entrega buena, y bajarlas sería deshacerla.
raiz="${MOTOR_INSTALA_RAIZ:-$(cd "$aqui/.." && pwd)}"
entrega="${MOTOR_SHA:-$(git -C "$raiz" rev-parse HEAD 2>/dev/null)}"
publicado="$(sha_publicado "$raiz" 2>/dev/null)" || publicado=""
if [ -n "$publicado" ] && [ -n "$entrega" ] && [ "$publicado" != "$entrega" ]; then
  echo "la etiqueta $ETIQUETA está en ${publicado:0:7} y esta entrega es ${entrega:0:7}: no instaló nada, nadie se mueve"
  exit 0
fi

fallaron=""
for m in $MAQUINAS; do
  echo "== $m"
  if [ "$m" = pc ]; then vuelve_pc; else vuelve_posix "$m"; fi || { echo "FALLÓ $m" >&2; fallaron="$fallaron $m"; }
done
if [ -n "$fallaron" ]; then echo "No quedó en la etiqueta anterior:$fallaron" >&2; exit 1; fi
echo "Las tres máquinas están en su etiqueta anterior (el servicio sigue cargado en las tres)."
