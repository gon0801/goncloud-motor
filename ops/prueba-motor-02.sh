#!/usr/bin/env bash
# Prueba real del motor-02 en las tres máquinas: doctor como servicio, el daemon ya corre la
# etiqueta de ops/lib.sh (nadie lo reinicia aquí), un archivo roto termina en inbox/rechazados/
# sin tumbar al daemon y, en la PC, la Acepta de I.2 y git dentro de WSL.
# Sale != 0 si algo falla, diciendo en qué máquina y qué fue.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

espera_s="${MOTOR_PRUEBA_ESPERA_S:-300}"
id="motor-prueba-02-$$-$RANDOM"
roto="$id.json"
carpeta_pc="$id"
fallos=0
fallo() { echo "FALLA: $*" >&2; fallos=$((fallos + 1)); }

py_posix='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; if python3 -c "import tomllib" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi'

doctor_ok() { # doctor_ok <m>
  local m=$1 out
  out="$(corre "$m" "$py_posix; cd \"\$HOME/motor\" && PYTHONPATH=. \$py -m motor doctor --home \"\$HOME/.motor\" --como-servicio --json" "cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor doctor --home %USERPROFILE%\\.motor --como-servicio --json" 2>&1)" || return 1
  printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") else 1)' 2>/dev/null
}

etiqueta_de() { # etiqueta_de <m>
  corre "$1" 'git -C "$HOME/motor" describe --tags --exact-match 2>/dev/null' "git -C %USERPROFILE%\\motor describe --tags --exact-match 2>nul" 2>/dev/null | tr -d '[:space:]'
}

servicio_vivo() { # servicio_vivo <m>: cargado y con proceso, sin tocarlo
  local m=$1 out
  if [ "$m" = pc ]; then
    corre pc x "schtasks /query /tn $TAREA_PC" >/dev/null 2>&1
  else
    out="$(corre "$m" "launchctl print system/$SERVICIO_MAC" x 2>/dev/null)" || return 1
    printf '%s' "$out" | grep -q 'pid ='
  fi
}

espera_daemon_en_etiqueta() { # espera_daemon_en_etiqueta <m>
  local m=$1 hasta=$((SECONDS + espera_s)) vista=""
  while [ "$SECONDS" -lt "$hasta" ]; do
    vista="$(etiqueta_de "$m")"
    if [ "$vista" = "$ETIQUETA" ] && servicio_vivo "$m"; then return 0; fi
    sleep 5
  done
  echo "${vista:-sin etiqueta}"
  return 1
}

deja_roto() { # deja_roto <m>
  if [ "$1" = pc ]; then
    corre pc x "if not exist %USERPROFILE%\\.motor\\inbox mkdir %USERPROFILE%\\.motor\\inbox" >/dev/null 2>&1 || return 1
    corre pc x "echo { roto> %USERPROFILE%\\.motor\\inbox\\$roto" >/dev/null 2>&1
  else
    corre "$1" "mkdir -p \"\$HOME/.motor/inbox\" && printf '%s' '{ roto' > \"\$HOME/.motor/inbox/$roto\"" x >/dev/null 2>&1
  fi
}

esta_rechazado() { # esta_rechazado <m>
  if [ "$1" = pc ]; then
    corre pc x "if exist %USERPROFILE%\\.motor\\inbox\\rechazados\\$roto (exit 0) else (exit 1)" >/dev/null 2>&1
  else
    corre "$1" "test -f \"\$HOME/.motor/inbox/rechazados/$roto\"" x >/dev/null 2>&1
  fi
}

espera_rechazado() { # espera_rechazado <m>
  local m=$1 hasta=$((SECONDS + espera_s))
  while [ "$SECONDS" -lt "$hasta" ]; do
    esta_rechazado "$m" && return 0
    sleep 5
  done
  return 1
}

limpia_roto() { # limpia_roto <m>
  corre "$1" "rm -f \"\$HOME/.motor/inbox/$roto\" \"\$HOME/.motor/inbox/rechazados/$roto\"" "del /q %USERPROFILE%\\.motor\\inbox\\$roto %USERPROFILE%\\.motor\\inbox\\rechazados\\$roto" >/dev/null 2>&1 || true
}

prueba_pc_extra() {
  local out rc
  out="$(corre pc x "cd /d %USERPROFILE%\\motor && \"C:\\Program Files\\Git\\bin\\bash.exe\" -c \"test -f README.md && python3 -c \\\"print(1)\\\"\"" 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] || ! printf '%s' "$out" | tr -d '\r' | grep -qx '1'; then
    fallo "pc: la Acepta de I.2 en Git Bash salió $rc: $(printf '%s' "$out" | tail -1)"
  else
    echo "pc: Acepta de I.2 ok"
  fi

  corre pc x "mkdir %USERPROFILE%\\$carpeta_pc && git init -q %USERPROFILE%\\$carpeta_pc" >/dev/null 2>&1 || { fallo "pc: no pude crear la carpeta de trabajo $carpeta_pc"; return; }
  out="$(corre pc x "wsl.exe -e git -C /mnt/c/Users/$PC_USUARIO/$carpeta_pc status" 2>&1)"
  rc=$?
  corre pc x "rmdir /s /q %USERPROFILE%\\$carpeta_pc" >/dev/null 2>&1 || true
  if [ "$rc" -ne 0 ]; then
    fallo "pc: wsl.exe -e git -C $carpeta_pc status salió $rc: $(printf '%s' "$out" | tail -1)"
  else
    echo "pc: git en WSL ok"
  fi
}

for m in $MAQUINAS; do
  echo "== $m (etiqueta esperada $ETIQUETA)"

  if doctor_ok "$m"; then echo "$m: doctor --como-servicio ok"; else fallo "$m: motor doctor --como-servicio no dio ok"; fi

  if vista="$(espera_daemon_en_etiqueta "$m")"; then
    echo "$m: daemon vivo en $ETIQUETA sin reinicio manual"
  else
    fallo "$m: el daemon no quedó en $ETIQUETA (en disco: $vista) o el servicio no está vivo tras ${espera_s}s"
  fi

  if ! deja_roto "$m"; then
    fallo "$m: no pude dejar el archivo roto en inbox"
  elif ! espera_rechazado "$m"; then
    fallo "$m: $roto no terminó en inbox/rechazados/ tras ${espera_s}s"
  elif ! servicio_vivo "$m"; then
    fallo "$m: el daemon no siguió vivo tras rechazar $roto"
  else
    echo "$m: archivo roto rechazado y daemon vivo"
  fi
  limpia_roto "$m"

  [ "$m" = pc ] && prueba_pc_extra
done

if [ "$fallos" -gt 0 ]; then echo "prueba-motor-02: $fallos falla(s)" >&2; exit 1; fi
echo "prueba-motor-02: las tres máquinas ok en $ETIQUETA"
