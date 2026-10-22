#!/usr/bin/env bash
# Prueba real del motor-02 en las tres máquinas: doctor como servicio, el daemon ya corre la
# etiqueta de ops/lib.sh (nadie lo reinicia aquí), un archivo roto termina en inbox/rechazados/
# sin tumbar al daemon y, en la PC, la Acepta de I.2 y git dentro de WSL.
# Sale != 0 si algo falla, diciendo en qué máquina y qué fue.
# Nota: cada corrida deja un evento CommandReceived permanente por máquina y el rechazo
# encola un aviso por Telegram en cada una.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

espera_s="${MOTOR_PRUEBA_ESPERA_S:-300}"
id="motor-prueba-02-$$-$RANDOM"
roto="$id.json"
carpeta_pc="$id"
creada_pc=0
fallos=0
fallo() { echo "FALLA: $*" >&2; fallos=$((fallos + 1)); }

py_posix='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; if python3 -c "import tomllib" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi'

doctor_ok() { # doctor_ok <m>
  local m=$1 out
  out="$(corre "$m" "$py_posix; cd \"\$HOME/motor\" && PYTHONPATH=. \$py -m motor doctor --home \"\$HOME/.motor\" --como-servicio --json" "cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor doctor --home %USERPROFILE%\\.motor --como-servicio --json")" || return 1
  printf '%s' "$out" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
except Exception as e:
    print(f"doctor: salida no es JSON: {e}", file=sys.stderr); sys.exit(1)
if d.get("ok"):
    sys.exit(0)
print("doctor problemas: " + "; ".join(str(p) for p in d.get("problemas") or []), file=sys.stderr)
sys.exit(1)'
}

etiqueta_de() { # etiqueta_de <m>
  corre "$1" 'git -C "$HOME/motor" describe --tags --exact-match 2>/dev/null' "git -C %USERPROFILE%\\motor describe --tags --exact-match 2>nul" 2>/dev/null | tr -d '[:space:]'
}

ultimo_cambio() { # ultimo_cambio <m>: epoch del último reflog de ~/motor
  corre "$1" 'git -C "$HOME/motor" reflog -1 --date=unix --format=%ct 2>/dev/null' "git -C %USERPROFILE%\\motor reflog -1 --date=unix --format=%ct" 2>/dev/null | tr -d '[:space:]'
}

daemon_info() { # daemon_info <m>: "<pid> <epoch de arranque>" del motor daemon, no del supervisor
  local m=$1 out pid inicio
  if [ "$m" = pc ]; then
    out="$(corre pc x "powershell -NoProfile -NonInteractive -Command \"Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*motor daemon*--home*' } | Select-Object -First 1 | ForEach-Object { \$_.ProcessId.ToString() + ' ' + ([DateTimeOffset]\$_.CreationDate).ToUnixTimeSeconds().ToString() }\"" 2>/dev/null)" || return 1
    out="$(printf '%s\n' "$out" | tr -d '\r' | grep -E '^[0-9]+ [0-9]+$' | head -n 1)"
    [ -n "$out" ] || return 1
    printf '%s\n' "$out"
    return 0
  fi
  pid="$(corre "$m" 'pgrep -f "[m]otor daemon --home $HOME/.motor" | head -n 1' x 2>/dev/null | tr -d '[:space:]')"
  [ -n "$pid" ] || return 1
  inicio="$(corre "$m" "ps -o lstart= -p $pid" x 2>/dev/null)"
  inicio="$(printf '%s' "$inicio" | python3 -c 'import sys,datetime
s=" ".join(sys.stdin.read().split())
try:
    print(int(datetime.datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp()))
except Exception:
    sys.exit(1)' 2>/dev/null)" || return 1
  [ -n "$inicio" ] || return 1
  printf '%s %s\n' "$pid" "$inicio"
}

pid_daemon() { # pid_daemon <m>
  local info
  info="$(daemon_info "$1")" || return 1
  printf '%s\n' "${info%% *}"
}

tarea_pc_corriendo() { # la tarea de la PC existe y está Running (un Ready no basta)
  local out
  out="$(corre pc x "schtasks /query /tn $TAREA_PC /fo csv /nh" 2>/dev/null)" || return 1
  printf '%s' "$out" | tr -d '\r' | grep -q '"Running"'
}

servicio_vivo() { # servicio_vivo <m>: el proceso motor daemon existe (y en la PC la tarea corre)
  local m=$1
  pid_daemon "$m" >/dev/null 2>&1 || return 1
  if [ "$m" = pc ]; then
    tarea_pc_corriendo || return 1
  fi
  return 0
}

espera_daemon_en_etiqueta() { # espera_daemon_en_etiqueta <m>
  local m=$1 hasta=$((SECONDS + espera_s)) vista="" info pid inicio cambio
  while [ "$SECONDS" -lt "$hasta" ]; do
    vista="$(etiqueta_de "$m")"
    if [ "$vista" = "$ETIQUETA" ]; then
      info="$(daemon_info "$m" 2>/dev/null)" || info=""
      pid="${info%% *}"
      inicio="${info##* }"
      cambio="$(ultimo_cambio "$m")"
      if [ -n "$pid" ] && [ "$inicio" != "$info" ] && [ -n "$cambio" ] && [ "$inicio" -ge "$cambio" ] 2>/dev/null; then
        if [ "$m" != pc ] || tarea_pc_corriendo; then
          return 0
        fi
      fi
    fi
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

limpia_carpeta_pc() {
  [ "$creada_pc" = 1 ] || return 0
  corre pc x "git -C %USERPROFILE%\\motor worktree remove --force %USERPROFILE%\\.motor\\wt\\$carpeta_pc" >/dev/null 2>&1 || \
    corre pc x "rmdir /s /q %USERPROFILE%\\.motor\\wt\\$carpeta_pc" >/dev/null 2>&1 || true
  creada_pc=0
}

limpia_todo() {
  limpia_carpeta_pc
  limpia_roto mac
  limpia_roto mini
  limpia_roto pc
}

prueba_pc_extra() {
  local out rc
  out="$(corre pc x "cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -c \"import os,sys; from pathlib import Path; from motor.conf import load_maquina; from motor.effects import _run; mq=load_maquina(Path.home() / '.motor'); cmd='test -f README.md && python3 -c '+chr(34)+'print(1)'+chr(34); r=_run(cmd, Path.cwd(), dict(os.environ), bash=mq.bash); sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); sys.exit(r.returncode)\"" 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] || ! printf '%s' "$out" | tr -d '\r' | grep -qx '1'; then
    fallo "pc: la Acepta de I.2 por motor.effects._run salió $rc: $(printf '%s' "$out" | tail -1)"
  else
    echo "pc: Acepta de I.2 ok"
  fi

  creada_pc=1
  corre pc x "git -C %USERPROFILE%\\motor config worktree.useRelativePaths true && git -C %USERPROFILE%\\motor worktree prune && git -C %USERPROFILE%\\motor worktree add --relative-paths %USERPROFILE%\\.motor\\wt\\$carpeta_pc HEAD" >/dev/null 2>&1 || { fallo "pc: no pude crear la carpeta de trabajo $carpeta_pc con worktree add --relative-paths"; limpia_carpeta_pc; return; }
  out="$(corre pc x "wsl.exe -e git -C /mnt/c/Users/$PC_USUARIO/.motor/wt/$carpeta_pc status" 2>&1)"
  rc=$?
  limpia_carpeta_pc
  if [ "$rc" -ne 0 ]; then
    fallo "pc: wsl.exe -e git -C $carpeta_pc status salió $rc: $(printf '%s' "$out" | tail -1)"
  else
    echo "pc: git en WSL ok"
  fi
}

for m in $MAQUINAS; do
  trap limpia_todo EXIT
  echo "== $m (etiqueta esperada $ETIQUETA)"

  vista=""
  if vista="$(espera_daemon_en_etiqueta "$m")"; then
    echo "$m: daemon vivo en $ETIQUETA sin reinicio manual"
  else
    fallo "$m: el daemon no quedó en $ETIQUETA (en disco: $vista) o el proceso motor daemon no arrancó tras el cambio tras ${espera_s}s"
  fi

  if doctor_ok "$m"; then echo "$m: doctor --como-servicio ok"; else fallo "$m: motor doctor --como-servicio no dio ok"; fi

  pid_antes="$(pid_daemon "$m" 2>/dev/null)" || pid_antes=""
  if [ -z "$pid_antes" ]; then
    fallo "$m: no hay proceso motor daemon --home antes del archivo roto"
  elif ! deja_roto "$m"; then
    fallo "$m: no pude dejar el archivo roto en inbox"
  elif ! espera_rechazado "$m"; then
    fallo "$m: $roto no terminó en inbox/rechazados/ tras ${espera_s}s"
  else
    pid_despues="$(pid_daemon "$m" 2>/dev/null)" || pid_despues=""
    if [ -z "$pid_despues" ] || [ "$pid_despues" != "$pid_antes" ] || ! servicio_vivo "$m"; then
      fallo "$m: el daemon no siguió vivo tras rechazar $roto (pid antes=$pid_antes después=${pid_despues:-ninguno})"
    else
      echo "$m: archivo roto rechazado y daemon vivo (pid $pid_antes)"
    fi
  fi
  limpia_roto "$m"

  [ "$m" = pc ] && prueba_pc_extra
done

trap - EXIT
if [ "$fallos" -gt 0 ]; then echo "prueba-motor-02: $fallos falla(s)" >&2; exit 1; fi
echo "prueba-motor-02: las tres máquinas ok en $ETIQUETA"
