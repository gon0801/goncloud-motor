#!/usr/bin/env bash
# Deja a Kuma vigilando el motor de la MacBook, la Mini y la PC. El motor de planes de cada máquina
# tiene su monitor push, «Motor de la <máquina>», y cada corrida escribe su token en
# ~/.motor/secretos.env, en la base que esa máquina alcanza. El de encargos es otro monitor. En la
# Mini y la PC, una dirección que ya estaba en ~/.motor-encargos/secretos.env y no creó este script
# se deja como está. La de la MacBook sí es de este script: cada corrida la reescribe con la base
# que la Mac alcanza y el token del monitor, y vuelve a crear el monitor si alguien lo borró. Si
# este script creó la de la Mini o la PC (queda la marca .senal-kuma-encargos) y borran ese monitor,
# la siguiente corrida lo recrea y reescribe esa URL. Nunca el mismo token en los dos secretos.
# La base se elige en la máquina: primero 10.13.13.1:3001 y, si no responde, el túnel
# 127.0.0.1:19301. Si ninguna responde, no escribe. La Mac escribe en local, sin ssh a sí misma.
# No reinicia ningún motor: la señal lee el archivo en cada vuelta. Sin argumentos; se puede
# repetir. Nunca imprime un token.
set -uo pipefail
export LC_ALL=C
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/lib.sh"
. "$DIR/lib-kuma.sh"

# La Mac entra siempre: su URL de encargos se reescribe en cada corrida. La Mini y la PC entran
# si falta la URL (3), si es la de planes (6), o si este script la creó (7) para reponer el monitor.
crear=" mac"
fallos=0
for m in mac mini pc; do
  linea_en "$m" asegurar_distinto .motor-encargos </dev/null >/dev/null 2>&1
  rc=$?
  case "$rc" in
    0) ;;
    3|6|7)
      case " $crear " in
        *" $m "*) ;;
        *) crear="$crear $m" ;;
      esac
      ;;
    2)
      echo "senal-kuma: $m: no pude saber si esta máquina es la MacBook" >&2
      fallos=$((fallos + 1))
      ;;
    *)
      echo "senal-kuma: $m: no pude revisar ~/.motor-encargos/secretos.env" >&2
      fallos=$((fallos + 1))
      ;;
  esac
done

tokens="$(servidor_kuma alta $crear)" || { echo "senal-kuma: el servidor no dejó los monitores" >&2; exit 1; }

token_de() { printf '%s\n' "$tokens" | awk -v c="$1" '$1 == c {print $2}'; }

for m in mac mini pc; do
  token="$(token_de "$m")"
  if ! printf '%s' "$token" | grep -Eq '^[A-Za-z0-9]{32}$'; then
    echo "senal-kuma: $m: el servidor no devolvió un token válido" >&2
    fallos=$((fallos + 1))
    continue
  fi
  if ! base="$(base_para "$m")"; then
    fallos=$((fallos + 1))
    continue
  fi
  if ! printf '%s\n' "$token" | linea_en "$m" poner .motor "$base" >/dev/null; then
    echo "senal-kuma: $m: no pude escribir .motor/secretos.env" >&2
    fallos=$((fallos + 1))
    continue
  fi
  case " $crear " in
    *" $m "*)
      token="$(token_de "$m-encargos")"
      if ! printf '%s' "$token" | grep -Eq '^[A-Za-z0-9]{32}$'; then
        echo "senal-kuma: $m: el servidor no devolvió un token de encargos válido" >&2
        fallos=$((fallos + 1))
        continue
      fi
      if ! printf '%s\n' "$token" | linea_en "$m" poner .motor-encargos "$base" >/dev/null; then
        echo "senal-kuma: $m: no pude escribir .motor-encargos/secretos.env" >&2
        fallos=$((fallos + 1))
        continue
      fi
      ;;
  esac
  if senal_configurada "$m"; then
    echo "$m: $SENAL_OK"
  else
    echo "senal-kuma: $m: motor doctor no dice '$SENAL_OK'" >&2
    fallos=$((fallos + 1))
  fi
done
exit $((fallos > 0))
