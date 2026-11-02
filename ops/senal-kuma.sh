#!/usr/bin/env bash
# Deja a Kuma vigilando el motor de la MacBook, la Mini y la PC. El motor de planes de cada máquina
# tiene su monitor push, «Motor de la <máquina>», y su token va a ~/.motor/secretos.env. El supervisor
# de encargos conserva su dirección propia en ~/.motor-encargos/secretos.env: si falta (la MacBook),
# se crea el monitor «Motor de encargos de la <máquina>». Nunca el mismo token en los dos secretos:
# con el mismo monitor, el proceso vivo taparía la caída del otro. La Mac escribe en local, sin ssh a
# sí misma. No reinicia ningún motor: la señal lee el archivo en cada vuelta. Sin argumentos; se puede
# repetir. Nunca imprime un token.
set -uo pipefail
export LC_ALL=C
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/lib.sh"
. "$DIR/lib-kuma.sh"

# El monitor de encargos de la MacBook es del script: se asegura en cada corrida para recrearlo si
# alguien lo borró. En la Mini y la PC solo se crea si falta la dirección o si es la de planes.
crear=" mac"
fallos=0
for m in mac mini pc; do
  linea_en "$m" asegurar_distinto .motor-encargos </dev/null >/dev/null 2>&1
  rc=$?
  case "$rc" in
    0) ;;
    3|6) crear="$crear $m" ;;
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
  if ! printf '%s\n' "$token" | linea_en "$m" poner .motor >/dev/null; then
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
      if ! printf '%s\n' "$token" | linea_en "$m" poner .motor-encargos >/dev/null; then
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
