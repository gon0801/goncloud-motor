#!/bin/sh
set -eu
home=""
while [ $# -gt 0 ]; do
  case "$1" in
    --home)
      home="$2"
      shift 2
      ;;
    *)
      echo "uso: reversa_ensayo.sh --home DIR" >&2
      exit 2
      ;;
  esac
done
if [ -z "$home" ] || [ ! -d "$home" ]; then
  echo "falta el home de ensayo" >&2
  exit 2
fi
pend="$home/instalacion-pendiente.json"
if [ ! -f "$pend" ]; then
  echo nada
  exit 0
fi
anterior="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["anterior"]["codigo"])' "$pend")"
destino="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["destino"]["codigo"])' "$pend")"
motor="$home/motor"
if [ -d "$motor/.git" ] || [ -f "$motor/.git" ]; then
  cabeza="$(git -C "$motor" rev-parse HEAD)"
  if [ "$cabeza" != "$anterior" ]; then
    git -C "$motor" -c core.hooksPath=/dev/null checkout -q --detach "$anterior"
  fi
fi
if [ -d "$home/generaciones/$anterior" ]; then
  ln -sfn "$home/generaciones/$anterior" "$home/actual"
elif [ -L "$home/actual" ]; then
  rm -f "$home/actual"
fi
if [ -n "$destino" ] && [ -d "$home/generaciones/$destino" ]; then
  ocupado=0
  if [ -d "$home/trabajos" ]; then
    for trabajo in "$home/trabajos"/*.json; do
      [ -f "$trabajo" ] || continue
      gen="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["generacion"])' "$trabajo")"
      if [ "$gen" = "$destino" ]; then
        ocupado=1
      fi
    done
  fi
  if [ "$ocupado" = 0 ] && { [ -d "$motor/.git" ] || [ -f "$motor/.git" ]; }; then
    git -C "$motor" worktree remove --force "$home/generaciones/$destino" 2>/dev/null || true
  fi
fi
rm -f "$pend"
echo "revertido $anterior"
