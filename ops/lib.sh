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
ETIQUETA="${MOTOR_ETIQUETA:-v1.0.26}"   # v1.0.0 quedó en un commit sin los arreglos del 2026-10-20
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
