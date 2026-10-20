#!/usr/bin/env bash
# Instala el motor en la Mac, la Mini y la PC: etiqueta v1.0.0 sobre el SHA mergeado, y en cada máquina
# ~/motor (en esa etiqueta), ~/motor-conf, ~/.motor/maquina.toml e ~/.motor/instalar-servicio.txt.
# No pide sudo: lo que sí lo necesita queda escrito en instalar-servicio.txt para la persona.
# Sale != 0 si alguna máquina falla (las demás se intentan igual y se dice cuál falló).
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"
raiz="${MOTOR_INSTALA_RAIZ:-$(cd "$aqui/.." && pwd)}"
rama="${MOTOR_RAMA:-main}"

# 1. La etiqueta, sobre un SHA que ya está en la rama principal (nunca sobre algo sin mergear).
git -C "$raiz" fetch -q --tags origin || { echo "no pude traer origin" >&2; exit 3; }
sha="$(git -C "$raiz" rev-parse HEAD)" || exit 3
if ! git -C "$raiz" merge-base --is-ancestor "$sha" "origin/$rama"; then
  echo "el SHA ${sha:0:12} no está mergeado en origin/$rama: no se etiqueta" >&2
  exit 3
fi
if [ -n "$(git -C "$raiz" ls-remote --tags origin "refs/tags/$ETIQUETA")" ]; then
  echo "etiqueta $ETIQUETA ya existe en origin: se respeta"
else
  # Se empuja el SHA verificado, no la etiqueta local: una v1.0.0 local vieja publicaría otro commit.
  git -C "$raiz" tag -f "$ETIQUETA" "$sha" >/dev/null 2>&1
  git -C "$raiz" push -q origin "$sha:refs/tags/$ETIQUETA" || { echo "no pude empujar $ETIQUETA" >&2; exit 3; }
  echo "etiqueta $ETIQUETA creada en ${sha:0:12}"
fi

# 2. Mac y Mini: un script POSIX que corre allá.
instala_posix() {
  local m=$1
  en_posix "$m" "$m" "$ETIQUETA" "$REPO_URL" "$CONF_URL" <<'REMOTO'
set -euo pipefail
m=$1 etiqueta=$2 repo=$3 confurl=$4
motor="$HOME/motor" conf="$HOME/motor-conf" home="$HOME/.motor"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# El motor pide Python 3.11+ (tomllib): si el python3 del sistema es viejo (la Mini trae 3.9), uv pone uno.
if python3 -c 'import tomllib' 2>/dev/null; then py="python3"; else py="uv run --python 3.12 python"; fi

if [ -d "$motor/.git" ]; then git -C "$motor" fetch -q --tags --force origin; else git clone -q "$repo" "$motor"; fi
git -C "$motor" checkout -q --detach "refs/tags/$etiqueta"
if [ -d "$conf/.git" ]; then git -C "$conf" fetch -q origin; else git clone -q "$confurl" "$conf"; fi
git -C "$conf" checkout -q --detach origin/HEAD

mkdir -p "$home"
[ -f "$conf/maquinas/$m.toml" ] || { echo "la conf no trae maquinas/$m.toml" >&2; exit 1; }
cp "$conf/maquinas/$m.toml" "$home/maquina.toml"
(cd "$motor" && PYTHONPATH=. $py -m motor conf valida "$conf" >/dev/null && PYTHONPATH=. $py -m motor version >/dev/null)

usuario="$(id -un)"
plist=/Library/LaunchDaemons/com.goncloud.motor.plist
{
  echo "# Lo que sigue lo corre una persona, una vez, con su contraseña (el motor no usa sudo)."
  echo
  echo "# 1. Servicio (arranca al prender, sin sesión iniciada):"
  echo "cd $motor && PYTHONPATH=. $py -m motor instala --maquina $m --usuario $usuario --home $home --imprime | sudo tee $plist >/dev/null"
  echo "sudo chown root:wheel $plist && sudo chmod 644 $plist"
  echo "sudo launchctl bootstrap system $plist"
  echo
  echo "# 2. sudoers (sudo visudo -f /etc/sudoers.d/motor):"
  if [ "$m" = mac ]; then
    echo "$usuario ALL=(root) NOPASSWD: /usr/bin/pmset -a disablesleep 1, /usr/bin/pmset -a disablesleep 0"
  else
    echo "$usuario ALL=(root) NOPASSWD: /sbin/shutdown"
  fi
} >"$home/instalar-servicio.txt"
echo "$m: motor $(git -C "$motor" rev-parse --short=12 HEAD) en $etiqueta, conf $(git -C "$conf" rev-parse --short=12 HEAD)"
REMOTO
}

# 3. PC: cmd.exe por ssh (mismo uv que ops/prueba-pc.sh).
instala_pc() {
  local u='%USERPROFILE%' tmp
  ssh_a "$(host_de pc)" "if exist $u\\motor\\.git (git -C $u\\motor fetch -q --tags --force origin) else (git clone -q $REPO_URL $u\\motor)" || return 1
  ssh_a "$(host_de pc)" "git -C $u\\motor checkout -q --detach refs/tags/$ETIQUETA" || return 1
  ssh_a "$(host_de pc)" "if exist $u\\motor-conf\\.git (git -C $u\\motor-conf fetch -q origin) else (git clone -q $CONF_URL $u\\motor-conf)" || return 1
  ssh_a "$(host_de pc)" "git -C $u\\motor-conf checkout -q --detach origin/HEAD" || return 1
  ssh_a "$(host_de pc)" "if not exist $u\\.motor mkdir $u\\.motor" || return 1
  ssh_a "$(host_de pc)" "copy /y $u\\motor-conf\\maquinas\\pc.toml $u\\.motor\\maquina.toml" >/dev/null || return 1
  ssh_a "$(host_de pc)" "cd /d $u\\motor && $PC_UV run --python 3.12 python -m motor conf valida $u\\motor-conf" >/dev/null || return 1
  tmp="$(mktemp -d)"
  cat >"$tmp/instalar-servicio.txt" <<TXT
# Lo que sigue lo corre una persona, una vez (el motor no pide permisos de administrador).
# Servicio: tarea programada al arrancar, aunque nadie inicie sesión.
cd /d %USERPROFILE%\\motor
$PC_UV run --python 3.12 python -m motor instala --maquina pc --usuario $PC_USUARIO --home %USERPROFILE%\\.motor --imprime > %USERPROFILE%\\.motor\\tarea.xml
schtasks /create /tn $TAREA_PC /xml %USERPROFILE%\\.motor\\tarea.xml /ru $PC_USUARIO /rp *
TXT
  tar -c -C "$tmp" instalar-servicio.txt | ssh_a "$(host_de pc)" "tar -x -C $u\\.motor"
  local rc=$?
  rm -rf "$tmp"
  return $rc
}

fallaron=""
for m in $MAQUINAS; do
  echo "== $m"
  if [ "$m" = pc ]; then instala_pc; else instala_posix "$m"; fi || { echo "FALLÓ $m" >&2; fallaron="$fallaron $m"; }
done
if [ -n "$fallaron" ]; then echo "No quedó instalado en:$fallaron" >&2; exit 1; fi
echo "Instalado en las tres. Siguiente: ~/.motor/instalar-servicio.txt en cada máquina."
