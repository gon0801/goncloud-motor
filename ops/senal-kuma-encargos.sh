#!/usr/bin/env bash
# Deja al supervisor de encargos de la Mini con su propia señal a Kuma: escribe (o quita) MOTOR_KUMA_ENCARGOS_URL en
# ~/.motor/secretos.env. Es una dirección distinta de MOTOR_KUMA_URL, que es del daemon del modo planes: con una sola
# compartida los dos procesos se taparían la caída. Este script NO toca el servidor de Kuma: el monitor push del
# supervisor se crea a mano en Kuma (conectado al aviso "Server Alerts") y su token entra por stdin.
#   printf '%s\n' "$TOKEN" | ops/senal-kuma-encargos.sh poner     (token de 32 letras y números)
#   ops/senal-kuma-encargos.sh quitar </dev/null
# El supervisor lee el archivo en cada minuto, pero avisa de que le falta la dirección solo al arrancar: reinícialo
# después de poner la línea para que deje de decirlo. Nunca imprime el token.
set -uo pipefail
export LC_ALL=C
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/lib.sh"
. "$DIR/lib-kuma.sh"

accion="${1:-}"
case "$accion" in poner | quitar) ;; *) echo "uso: senal-kuma-encargos.sh poner|quitar" >&2; exit 2 ;; esac

# El mismo escritor atómico de lib-kuma.sh, con la otra variable. Quita solo su propia línea: MOTOR_KUMA_URL
# (la del daemon) queda intacta porque el patrón de grep termina en MOTOR_KUMA_ENCARGOS_URL=.
escritor_encargos() {
  escritor_posix "$1" | sed 's/MOTOR_KUMA_URL/MOTOR_KUMA_ENCARGOS_URL/g'
}

if [ "$accion" = poner ]; then
  IFS= read -r token || true
  if ! printf '%s' "$token" | grep -Eq '^[A-Za-z0-9]{32}$'; then
    echo "senal-kuma-encargos: el token debe llegar por stdin y tener 32 letras y números" >&2
    exit 1
  fi
  if ! printf '%s\n' "$token" | corre mini "$(escritor_encargos poner)" x >/dev/null; then
    echo "senal-kuma-encargos: no pude escribir secretos.env en la Mini" >&2
    exit 1
  fi
  echo "mini: MOTOR_KUMA_ENCARGOS_URL escrita"
else
  if ! corre mini "$(escritor_encargos quitar)" x </dev/null >/dev/null; then
    echo "senal-kuma-encargos: no pude quitar la línea de secretos.env en la Mini" >&2
    exit 1
  fi
  echo "mini: sin MOTOR_KUMA_ENCARGOS_URL"
fi
