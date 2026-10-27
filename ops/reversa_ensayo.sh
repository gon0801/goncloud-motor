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
inst="$home/instalacion.json"
if [ ! -f "$pend" ] && [ ! -f "$inst" ]; then
  echo nada
  exit 0
fi
leer_campo() {
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print((d.get(sys.argv[2]) or {}).get("codigo") or "")' "$1" "$2"
}
anterior=""
destino=""
usado=""
consumir_pend=0
if [ -f "$inst" ] && [ -f "$pend" ]; then
  pend_anterior="$(leer_campo "$pend" anterior)"
  inst_destino="$(leer_campo "$inst" destino)"
  vigente="$inst_destino"
  if [ -L "$home/actual" ]; then
    vigente="$(basename "$(readlink "$home/actual")")"
  fi
  if [ -n "$pend_anterior" ] && { [ "$pend_anterior" = "$vigente" ] || [ "$pend_anterior" = "$inst_destino" ]; }; then
    anterior="$pend_anterior"
    destino="$(leer_campo "$pend" destino)"
    usado="pendiente"
    consumir_pend=1
  else
    anterior="$(leer_campo "$inst" anterior)"
    destino="$inst_destino"
    usado="instalacion"
    consumir_pend=1
  fi
elif [ -f "$inst" ]; then
  etapa="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("etapa") or "")' "$inst")"
  if [ "$etapa" != "completo" ]; then
    echo nada
    exit 0
  fi
  anterior="$(leer_campo "$inst" anterior)"
  destino="$(leer_campo "$inst" destino)"
  usado="instalacion"
else
  anterior="$(leer_campo "$pend" anterior)"
  destino="$(leer_campo "$pend" destino)"
  usado="pendiente"
  consumir_pend=1
fi
if [ -z "$anterior" ]; then
  echo nada
  exit 0
fi
motor="$home/motor"
if [ -d "$motor/.git" ] || [ -f "$motor/.git" ]; then
  cabeza="$(git -C "$motor" rev-parse HEAD)"
  if [ "$cabeza" != "$anterior" ]; then
    git -C "$motor" -c core.hooksPath=/dev/null checkout -q --detach "$anterior"
  fi
fi
if [ -d "$home/generaciones/$anterior" ]; then
  rm -f "$home/actual"
  ln -s "$home/generaciones/$anterior" "$home/actual"
elif [ -L "$home/actual" ]; then
  rm -f "$home/actual"
fi
if [ -n "$destino" ] && [ "$destino" != "$anterior" ] && [ -d "$home/generaciones/$destino" ]; then
  ocupado=0
  if [ -d "$home/trabajos" ]; then
    for trabajo in "$home/trabajos"/*.json; do
      [ -f "$trabajo" ] || continue
      gen="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("generacion") or "")' "$trabajo")"
      if [ "$gen" = "$destino" ]; then
        ocupado=1
      fi
    done
  fi
  if [ "$ocupado" = 0 ] && { [ -d "$motor/.git" ] || [ -f "$motor/.git" ]; }; then
    git -C "$motor" worktree remove --force "$home/generaciones/$destino" 2>/dev/null || true
  fi
fi
if [ "$usado" = "instalacion" ]; then
  mkdir -p "$home/historial"
  if [ -n "$destino" ]; then
    mv "$inst" "$home/historial/instalacion-$destino.json"
  else
    mv "$inst" "$home/historial/instalacion.json"
  fi
fi
if [ "$consumir_pend" = 1 ]; then
  rm -f "$pend"
fi
echo "revertido $anterior"
