#!/usr/bin/env bash
# Desbloquea el bucle de despliegue del arranque (lo vio el bloque F de motor-01 y lo
# vería cualquier bloque con despliegue): el daemon exporta MOTOR_RAIZ=~/.motor-arranque/motor,
# que NO tiene .git, y los scripts de despliegue que hacen `git -C "$MOTOR_RAIZ" fetch`
# salen 3 siempre. El parche hace que deploy() y prove() del motor instalado apunten
# MOTOR_RAIZ a la copia de trabajo (un worktree con .git).
# Detalle y porqué: docs/revisiones/motor-01-F-despliegue.md.
#
# LO CORRE UNA PERSONA, una sola vez, en la Mac donde corre el arranque:
#   bash ops/desbloquea-arranque.sh            # solo muestra el estado (no toca nada)
#   bash ops/desbloquea-arranque.sh --aplicar  # parchea effects.py y reinicia el daemon
set -euo pipefail
arranque="${ARRANQUE_HOME:-$HOME/.motor-arranque}"
efectos="$arranque/motor/motor/effects.py"
label=com.goncloud.motor-arranque
marca='env["MOTOR_RAIZ"] = str(w)'

[ -f "$efectos" ] || { echo "no encuentro $efectos" >&2; exit 1; }

if grep -qF "$marca" "$efectos"; then
  parcheado=si
else
  parcheado=no
fi

if [ "${1:-}" != "--aplicar" ]; then
  echo "arranque: $arranque"
  echo "effects.py parcheado: $parcheado"
  if launchctl list 2>/dev/null | grep -q "$label"; then
    echo "daemon $label: corriendo"
  else
    echo "daemon $label: NO aparece en launchctl list"
  fi
  echo
  echo "Para aplicar el parche y reiniciar el daemon:"
  echo "  bash ops/desbloquea-arranque.sh --aplicar"
  exit 0
fi

if [ "$parcheado" = no ]; then
  respaldo="$efectos.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$efectos" "$respaldo"
  echo "respaldo: $respaldo"
  python3 - "$efectos" <<'PY'
import sys

ruta = sys.argv[1]
texto = open(ruta).read()

viejo_deploy = (
    "        env = self.env_base(dict(rc.prueba_env))\n"
    "        if not desplegar:"
)
nuevo_deploy = (
    "        env = self.env_base(dict(rc.prueba_env))\n"
    '        env["MOTOR_RAIZ"] = str(w)  # la copia (worktree) sí tiene .git; la instalación del motor no\n'
    "        if not desplegar:"
)
viejo_prove = (
    "            p = _run(block.prueba_real, w, self.env_base(dict(rc.prueba_env)))"
)
nuevo_prove = (
    "            env = self.env_base(dict(rc.prueba_env))\n"
    '            env["MOTOR_RAIZ"] = str(w)\n'
    "            p = _run(block.prueba_real, w, env)"
)

for viejo, nuevo in ((viejo_deploy, nuevo_deploy), (viejo_prove, nuevo_prove)):
    if texto.count(viejo) != 1:
        sys.exit(f"el ancla no aparece exactamente una vez en {ruta}:\n{viejo}")
    texto = texto.replace(viejo, nuevo)

open(ruta, "w").write(texto)
print("parche aplicado en deploy() y prove()")
PY
  if ! python3 -m py_compile "$efectos"; then
    cp "$respaldo" "$efectos"
    echo "el parche no compilaba; se restauró el respaldo" >&2
    exit 1
  fi
else
  echo "effects.py ya estaba parcheado: solo reinicio el daemon"
fi

launchctl kickstart -k "gui/$(id -u)/$label"
sleep 2
if launchctl list 2>/dev/null | grep -q "$label"; then
  echo "daemon $label reiniciado con el parche. El próximo despliegue ya corre con MOTOR_RAIZ sano."
else
  echo "el daemon no volvió a aparecer en launchctl list: revisalo a mano" >&2
  exit 1
fi
