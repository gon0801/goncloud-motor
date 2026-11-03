#!/usr/bin/env bash
# Descarga el servicio del motor en las tres máquinas. Deja ~/motor, ~/motor-conf y ~/.motor
# (con sus registros): desinstalar no borra nada de datos. Si el sistema pide contraseña para
# descargar el servicio (Mac/Mini), escribe el comando en ~/.motor/desinstalar-servicio.txt y falla.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

descarga_posix() {
  local m=$1
  en_posix "$m" "$m" "$SERVICIO_MAC" <<'REMOTO'
set -uo pipefail
m=$1 etiqueta=$2
plist=${MOTOR_LAUNCHD_DIR:-/Library/LaunchDaemons}/$etiqueta.plist
if ! launchctl print "system/$etiqueta" >/dev/null 2>&1 && [ ! -e "$plist" ]; then
  echo "$m: el servicio ya estaba descargado"; exit 0
fi
if sudo -n launchctl bootout "system/$etiqueta" 2>/dev/null || ! launchctl print "system/$etiqueta" >/dev/null 2>&1; then
  sudo -n rm -f "$plist" 2>/dev/null
  if [ ! -e "$plist" ]; then echo "$m: servicio descargado (carpetas y registros intactos)"; exit 0; fi
fi
mkdir -p "$HOME/.motor"
printf 'sudo launchctl bootout system/%s\nsudo rm -f %s\n' "$etiqueta" "$plist" >"$HOME/.motor/desinstalar-servicio.txt"
echo "$m: hace falta tu contraseña; los comandos quedaron en ~/.motor/desinstalar-servicio.txt" >&2
exit 1
REMOTO
}

descarga_pc() {
  local h; h="$(host_de pc)"
  if ! ssh_a "$h" "schtasks /query /tn $TAREA_PC" >/dev/null 2>&1; then
    # ¿no existe, o no hubo ssh? Un ssh que sí responde distingue ambos casos.
    ssh_a "$h" "echo ok" >/dev/null 2>&1 || { echo "pc: no hay ssh" >&2; return 1; }
    echo "pc: el servicio ya estaba descargado"; return 0
  fi
  ssh_a "$h" "schtasks /end /tn $TAREA_PC" >/dev/null 2>&1
  ssh_a "$h" "schtasks /delete /tn $TAREA_PC /f" || return 1
  echo "pc: servicio descargado (carpetas y registros intactos)"
}

fallaron=""
for m in $MAQUINAS; do
  echo "== $m"
  if [ "$m" = pc ]; then descarga_pc; else descarga_posix "$m"; fi || { echo "FALLÓ $m" >&2; fallaron="$fallaron $m"; }
done
if [ -n "$fallaron" ]; then echo "El servicio sigue cargado en:$fallaron" >&2; exit 1; fi
echo "Servicio descargado en las tres."
