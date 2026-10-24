#!/usr/bin/env bash
# Instala el motor en la Mac, la Mini y la PC: etiqueta (la de ops/lib.sh por omisión) sobre el SHA mergeado, y en cada máquina
# ~/motor (en esa etiqueta), ~/motor-conf, ~/.motor/maquina.toml e ~/.motor/instalar-servicio.txt.
# No pide sudo: lo que sí lo necesita queda escrito en instalar-servicio.txt para la persona.
# Antes de tocar ~/motor en ninguna, el código de la etiqueta valida la conf que va a quedar en cada máquina:
# si la rechaza en alguna, no cambia el código en ninguna.
# Sale != 0 si alguna máquina falla (las demás se intentan igual y se dice cuál falló).
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"
raiz="${MOTOR_INSTALA_RAIZ:-$(cd "$aqui/.." && pwd)}"
rama="${MOTOR_RAMA:-main}"

# 1. La etiqueta, sobre un SHA que ya está en la rama principal (nunca sobre algo sin mergear).
# Lo de red se reintenta: este repo lo comparte el daemon, que fetcha seguido, y un fetch o un
# push puede chocar con otro proceso (lock) o con un bache de red. Eso no debe tumbar el despliegue.
reintenta 3 git -C "$raiz" fetch -q --force --tags origin || { echo "no pude traer origin" >&2; exit 3; }
sha="$(git -C "$raiz" rev-parse HEAD)" || exit 3
if ! git -C "$raiz" merge-base --is-ancestor "$sha" "origin/$rama"; then
  echo "el SHA ${sha:0:12} no está mergeado en origin/$rama: no se etiqueta" >&2
  exit 3
fi
niega_otro_commit() {
  echo "la etiqueta $ETIQUETA ya existe en ${1:0:7} y esta instalación es ${sha:0:7}: sube la etiqueta en ops/lib.sh" >&2
  exit 3
}
if ! sha_etiqueta="$(reintenta 3 sha_publicado "$raiz")"; then
  # Sin respuesta no se sabe si la etiqueta existe: peor que fallar es pisarla o duplicarla.
  echo "no pude consultar las etiquetas de origin" >&2
  exit 3
fi
if [ -n "$sha_etiqueta" ]; then
  [ "$sha_etiqueta" = "$sha" ] || niega_otro_commit "$sha_etiqueta"
  echo "etiqueta $ETIQUETA ya existe en origin: se respeta"
else
  # Se empuja el SHA verificado, no la etiqueta local: una etiqueta local vieja publicaría otro commit.
  if ! reintenta 3 git -C "$raiz" push -q origin "$sha:refs/tags/$ETIQUETA"; then
    # Si otro la creó mientras tanto, vale solo si apunta a este commit.
    sha_etiqueta="$(sha_publicado "$raiz")" && [ -n "$sha_etiqueta" ] \
      || { echo "no pude empujar $ETIQUETA" >&2; exit 3; }
    [ "$sha_etiqueta" = "$sha" ] || niega_otro_commit "$sha_etiqueta"
    echo "etiqueta $ETIQUETA la creó otro mientras tanto: se respeta"
  else
    echo "etiqueta $ETIQUETA creada en ${sha:0:12}"
  fi
fi

# 1b. Validación previa: el código NUEVO (una copia temporal de la etiqueta) contra la conf que va a quedar.
# Sale 4 si ese código rechaza la conf; 255 es una máquina sin ssh; cualquier otro código es una
# validación que no pudo cerrarse. Ninguno de los tres deja cambiar el código en ninguna máquina.
valida_posix() {
  local m=$1 repo_url=$REPO_URL conf_url=$CONF_URL
  if [ "$m" = mini ]; then repo_url=$REPO_URL_MINI conf_url=$CONF_URL_MINI; fi
  en_posix "$m" "$m" "$ETIQUETA" "$repo_url" "$conf_url" <<'REMOTO'
set -euo pipefail
m=$1 etiqueta=$2 repo=$3 confurl=$4
motor="$HOME/motor" conf="$HOME/motor-conf"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if python3 -c 'import tomllib' 2>/dev/null; then py="python3"; else py="uv run --python 3.12 python"; fi
t="$(mktemp -d)"
trap 'rm -rf "$t"' EXIT
if [ -d "$motor/.git" ]; then git -C "$motor" fetch -q --tags --force origin; src="$motor"; else git clone -q --no-checkout "$repo" "$t/src"; src="$t/src"; fi
if [ -d "$conf/.git" ]; then git -C "$conf" fetch -q origin; csrc="$conf"; else git clone -q --no-checkout "$confurl" "$t/csrc"; csrc="$t/csrc"; fi
mkdir "$t/codigo" "$t/conf"
git -C "$src" archive "refs/tags/$etiqueta" | tar -x -C "$t/codigo"
git -C "$csrc" archive origin/HEAD | tar -x -C "$t/conf"
[ -f "$t/conf/maquinas/$m.toml" ] || { echo "$m: la conf no trae maquinas/$m.toml" >&2; exit 4; }
if ! (cd "$t/codigo" && PYTHONPATH=. $py -m motor conf valida "$t/conf" >&2); then
  echo "$m: el código de $etiqueta rechaza la conf que va a quedar" >&2
  exit 4
fi
REMOTO
}

valida_pc() {
  local u='%USERPROFILE%' t="%TEMP%\\motor-valida-$$" rc=0 h
  h="$(host_de pc)"
  ssh_a "$h" "if exist $u\\motor\\.git (git -C $u\\motor fetch -q --tags --force origin) else (git clone -q $REPO_URL $u\\motor)" || return $?
  ssh_a "$h" "if exist $u\\motor-conf\\.git (git -C $u\\motor-conf fetch -q origin) else (git clone -q $CONF_URL $u\\motor-conf)" || return $?
  # Los paréntesis cierran el if: en cmd.exe un `& mkdir` sin ellos es parte de su cuerpo y no corre si $t no existe.
  ssh_a "$h" "(if exist $t rmdir /s /q $t) & mkdir $t\\codigo $t\\conf" || return $?
  ssh_a "$h" "git -C $u\\motor archive refs/tags/$ETIQUETA | tar -x -C $t\\codigo" || rc=$?
  [ $rc = 0 ] && { ssh_a "$h" "git -C $u\\motor-conf archive origin/HEAD | tar -x -C $t\\conf" || rc=$?; }
  if [ $rc = 0 ]; then
    ssh_a "$h" "if not exist $t\\conf\\maquinas\\pc.toml exit /b 1" || { echo "pc: la conf no trae maquinas/pc.toml" >&2; rc=4; }
  fi
  if [ $rc = 0 ]; then
    ssh_a "$h" "cd /d $t\\codigo && $PC_UV run --python 3.12 python -m motor conf valida $t\\conf" >&2 || {
      echo "pc: el código de $ETIQUETA rechaza la conf que va a quedar" >&2
      rc=4
    }
  fi
  ssh_a "$h" "rmdir /s /q $t" >/dev/null 2>&1
  return $rc
}

# 2. Mac y Mini: un script POSIX que corre allá.
instala_posix() {
  local m=$1 repo_url=$REPO_URL conf_url=$CONF_URL
  if [ "$m" = mini ]; then repo_url=$REPO_URL_MINI conf_url=$CONF_URL_MINI; fi
  en_posix "$m" "$m" "$ETIQUETA" "$repo_url" "$conf_url" <<'REMOTO'
set -euo pipefail
m=$1 etiqueta=$2 repo=$3 confurl=$4
motor="$HOME/motor" conf="$HOME/motor-conf" home="$HOME/.motor"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# El motor pide Python 3.11+ (tomllib): si el python3 del sistema es viejo (la Mini trae 3.9), uv pone uno.
if python3 -c 'import tomllib' 2>/dev/null; then py="python3"; else py="uv run --python 3.12 python"; fi

if [ -d "$motor/.git" ]; then git -C "$motor" fetch -q --tags --force origin; else git clone -q "$repo" "$motor"; fi
# La marcha atrás (ops/vuelve-a-la-anterior.sh) lee esta marca: la etiqueta que la máquina
# tenía antes de la entrega. Se anota solo cuando la etiqueta cambia; en una instalación
# nueva no hay etiqueta anterior que anotar.
anterior="$(git -C "$motor" describe --tags --exact-match 2>/dev/null || true)"
if [ -n "$anterior" ] && [ "$anterior" != "$etiqueta" ]; then
  mkdir -p "$home"
  echo "$anterior" >"$home/etiqueta-anterior"
fi
git -C "$motor" checkout -q --detach "refs/tags/$etiqueta"
if [ -d "$conf/.git" ]; then git -C "$conf" fetch -q origin; else git clone -q "$confurl" "$conf"; fi
git -C "$conf" checkout -q --detach origin/HEAD

mkdir -p "$home"
[ -f "$conf/maquinas/$m.toml" ] || { echo "la conf no trae maquinas/$m.toml" >&2; exit 1; }
cp "$conf/maquinas/$m.toml" "$home/maquina.toml"
# maquinas/<m>.toml declara la conf en ~/.motor/conf: un enlace la deja ahí sin duplicar el clone.
ln -sfn "$conf" "$home/conf"
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
  local u='%USERPROFILE%' tmp anterior
  ssh_a "$(host_de pc)" "if exist $u\\motor\\.git (git -C $u\\motor fetch -q --tags --force origin) else (git clone -q $REPO_URL $u\\motor)" || return 1
  # Marca para la marcha atrás: la etiqueta que la PC tenía antes, solo cuando cambia.
  # En una instalación nueva (o sobre una rama, no una etiqueta) no hay nada que anotar.
  anterior="$(ssh_a "$(host_de pc)" "git -C $u\\motor describe --tags --exact-match 2>nul" 2>/dev/null | tr -d '[:space:]')" || anterior=""
  if [ -n "$anterior" ] && [ "$anterior" != "$ETIQUETA" ]; then
    ssh_a "$(host_de pc)" "echo $anterior> $u\\.motor\\etiqueta-anterior" || return 1
  fi
  ssh_a "$(host_de pc)" "git -C $u\\motor checkout -q --detach refs/tags/$ETIQUETA" || return 1
  ssh_a "$(host_de pc)" "if exist $u\\motor-conf\\.git (git -C $u\\motor-conf fetch -q origin) else (git clone -q $CONF_URL $u\\motor-conf)" || return 1
  ssh_a "$(host_de pc)" "git -C $u\\motor-conf checkout -q --detach origin/HEAD" || return 1
  ssh_a "$(host_de pc)" "if not exist $u\\.motor mkdir $u\\.motor" || return 1
  ssh_a "$(host_de pc)" "copy /y $u\\motor-conf\\maquinas\\pc.toml $u\\.motor\\maquina.toml" >/dev/null || return 1
  # maquinas/pc.toml declara la conf en .motor\conf: una junction la deja ahí sin duplicar el clone (ni admin).
  ssh_a "$(host_de pc)" "if not exist $u\\.motor\\conf mklink /j $u\\.motor\\conf $u\\motor-conf" || return 1
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

fallaron="" rechazaron="" listas=""
for m in $MAQUINAS; do
  echo "== valida la conf en $m"
  if [ "$m" = pc ]; then valida_pc; else valida_posix "$m"; fi
  case $? in
    0) listas="$listas $m" ;;
    255) echo "FALLÓ $m: sin ssh" >&2; rechazaron="$rechazaron $m" ;;
    *) rechazaron="$rechazaron $m" ;;
  esac
done
if [ -n "$rechazaron" ]; then
  echo "La conf no pasa (o no se pudo validar) con el código de $ETIQUETA en:$rechazaron. No se cambió el código en ninguna máquina." >&2
  exit 1
fi
for m in $listas; do
  echo "== $m"
  if [ "$m" = pc ]; then instala_pc; else instala_posix "$m"; fi || { echo "FALLÓ $m" >&2; fallaron="$fallaron $m"; }
done
if [ -n "$fallaron" ]; then echo "No quedó instalado en:$fallaron" >&2; exit 1; fi
echo "Instalado en las tres. Siguiente: ~/.motor/instalar-servicio.txt en cada máquina."
