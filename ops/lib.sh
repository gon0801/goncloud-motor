# shellcheck shell=bash disable=SC2034
# Lo común de ops/*.sh: las tres máquinas y cómo hablarles. Se carga con `.`, no se ejecuta.
# Mac = esta máquina (sin ssh, salvo que MOTOR_MAC_SSH nombre un host); Mini y PC = por ssh.
# Estos scripts viven en el repo del motor, pero nunca tocan ~/dev/goncloud-motor ni ~/.motor-arranque
# (ahí corre el motor de arranque): solo ~/motor, ~/motor-conf y ~/.motor de cada máquina.

MAQUINAS="mac mini pc"
REPO_URL="${MOTOR_REPO_URL:-git@github.com:gon0801/goncloud-motor.git}"
CONF_URL="${MOTOR_CONF_URL:-git@github.com:gon0801/goncloud-motor-conf.git}"
# La Mini no tiene llave para github.com directo: entra por su alias de ssh (claw_github).
REPO_URL_MINI="${MOTOR_REPO_URL_MINI:-git@github-claw:gon0801/goncloud-motor.git}"
CONF_URL_MINI="${MOTOR_CONF_URL_MINI:-git@github-claw:gon0801/goncloud-motor-conf.git}"
ETIQUETA="${MOTOR_ETIQUETA:-v1.0.30}"   # v1.0.0 quedó en un commit sin los arreglos del 2026-10-20
PC_USUARIO="${MOTOR_PC_USUARIO:-ehven}"
PC_UV="${MOTOR_PC_UV:-C:\\Users\\ehven\\.local\\bin\\uv.exe}"
TAREA_PC="GoncloudMotor"
SERVICIO_MAC="com.goncloud.motor"

host_de() {
  case "$1" in
    mac) echo "${MOTOR_MAC_SSH:-}" ;;
    mini) echo "${MOTOR_SSH_MINI:-gon@100.73.187.5}" ;;
    pc) echo "${MOTOR_SSH_PC:-gwpc}" ;;
    *) echo "máquina desconocida: $1" >&2; return 2 ;;
  esac
}

# sha_publicado <raiz>: el commit al que apunta $ETIQUETA en origin (vacío si no existe); sale != 0
# si origin no responde. La última línea manda: ls-remote pone el ^{} de una anotada al final.
sha_publicado() {
  local salida
  salida="$(git -C "$1" ls-remote --tags origin "refs/tags/$ETIQUETA" "refs/tags/$ETIQUETA^{}")" || return 1
  printf '%s\n' "$salida" | tail -n 1 | cut -f1
}

# reintenta <veces> <comando...>: lo de red choca con locks o baches; un fallo suelto no debe tumbar nada.
reintenta() {
  local n=$1 i
  shift
  for i in $(seq "$n"); do
    "$@" && return 0
    [ "$i" -lt "$n" ] && sleep 2
  done
  return 1
}

ssh_a() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$@"; }

es_local() { [ "$1" = mac ] && [ -z "${MOTOR_MAC_SSH:-}" ]; }

# en_posix <mac|mini> args... : el script de stdin corre allá con `bash -s -- args`.
en_posix() {
  local m=$1 cmd
  shift
  if es_local "$m"; then
    bash -s -- "$@"
  else
    cmd="$(printf '%q ' bash -s -- "$@")"
    ssh_a "$(host_de "$m")" "$cmd"
  fi
}

# corre <m> <comando posix> <comando cmd.exe de la PC>
corre() {
  local m=$1
  if [ "$m" = pc ]; then
    ssh_a "$(host_de pc)" "$3"
  elif es_local "$m"; then
    bash -c "$2"
  else
    # bash explícito: el shell de login de la Mini es zsh y no parte `$py` en palabras.
    ssh_a "$(host_de "$m")" "$(printf '%q ' bash -c "$2")"
  fi
}

# info_daemon <m>: "<pid> <epoch de arranque>" del proceso motor daemon (no del supervisor); != 0 si no hay.
info_daemon() {
  local m=$1 out pid inicio
  if [ "$m" = pc ]; then
    out="$(corre pc x "powershell -NoProfile -NonInteractive -Command \"Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*motor daemon*--home*' -and \$_.Name -like 'python*' } | Select-Object -First 1 | ForEach-Object { \$_.ProcessId.ToString() + ' ' + ([DateTimeOffset]\$_.CreationDate).ToUnixTimeSeconds().ToString() }\"" 2>/dev/null)" || return 1
    out="$(printf '%s\n' "$out" | tr -d '\r' | grep -E '^[0-9]+ [0-9]+$' | head -n 1)"
    [ -n "$out" ] || return 1
    printf '%s\n' "$out"
    return 0
  fi
  pid="$(corre "$m" 'pgrep -a -f "[m]otor daemon --home $HOME/.motor( |$)" | head -n 1' x 2>/dev/null | awk '{print $1}' | tr -d '[:space:]')"
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

# codigo_de <m>: "<sha> <epoch>" del commit que tiene ~/motor y de cuándo se puso (mtime de .git/HEAD).
codigo_de() {
  local m=$1 sha epoca
  if [ "$m" = pc ]; then
    sha="$(corre pc x 'git -C %USERPROFILE%\motor rev-parse HEAD' 2>/dev/null | tr -d '[:space:]')" || return 1
    epoca="$(corre pc x "powershell -NoProfile -NonInteractive -Command \"[DateTimeOffset]::new((Get-Item (Join-Path \$env:USERPROFILE 'motor\\.git\\HEAD')).LastWriteTimeUtc).ToUnixTimeSeconds()\"" 2>/dev/null | tr -d '[:space:]')" || return 1
  else
    sha="$(corre "$m" 'git -C "$HOME/motor" rev-parse HEAD' x 2>/dev/null | tr -d '[:space:]')" || return 1
    epoca="$(corre "$m" 'python3 -c "import os,sys;print(int(os.path.getmtime(sys.argv[1])))" "$HOME/motor/.git/HEAD"' x 2>/dev/null | tr -d '[:space:]')" || return 1
  fi
  [ -n "$sha" ] && [ -n "$epoca" ] || return 1
  printf '%s %s\n' "$sha" "$epoca"
}

# espera_daemon_en_sha <maquina> <sha> <tope_s>: 0 cuando el motor daemon de esa máquina arrancó
# con el código de <sha> (~/motor está en ese commit y el proceso nació después de ponerlo); 1 al pasar el tope.
espera_daemon_en_sha() {
  local m=$1 sha=$2 tope=$3 hasta info codigo
  hasta=$((SECONDS + tope))
  while :; do
    if info="$(info_daemon "$m")" && codigo="$(codigo_de "$m")" \
      && [ "${codigo%% *}" = "$sha" ] && [ "${info##* }" -ge "${codigo##* }" ] 2>/dev/null; then
      return 0
    fi
    [ "$SECONDS" -lt "$hasta" ] || return 1
    sleep "${MOTOR_ESPERA_PASO:-5}"
  done
}
