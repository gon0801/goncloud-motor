#!/usr/bin/env bash
# ops/instala-todo.sh y ops/desinstala-todo.sh de verdad (git, bash y el motor reales) contra repos
# locales, un HOME de mentira por máquina y un ssh falso: la Mini corre su script en su HOME falso;
# la PC solo registra lo que se le pide. Exige ≠0 cuando el remoto falla y 0 cuando todo está bien.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

# --- repos locales: origen (el motor mergeado en main) y conf
git -C "$RAIZ" rev-parse HEAD >/dev/null || exit 2
git init -q --bare -b main "$S/origen.git"
git -C "$RAIZ" push -q "$S/origen.git" HEAD:refs/heads/main || exit 2
SHA="$(git -C "$S/origen.git" rev-parse main)"
git clone -q "$S/origen.git" "$S/trabajo"
git init -q -b main "$S/conf-src"
cp -R "$RAIZ/conf-ejemplo/." "$S/conf-src/"
mkdir -p "$S/conf-src/maquinas"
for m in mac mini pc; do printf 'nombre = "%s"\nconf = "~/motor-conf"\n' "$m" >"$S/conf-src/maquinas/$m.toml"; done
git -C "$S/conf-src" add -A
git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m conf
git clone -q --bare "$S/conf-src" "$S/conf.git"

export MOTOR_REPO_URL="$S/origen.git" MOTOR_CONF_URL="$S/conf.git" MOTOR_INSTALA_RAIZ="$S/trabajo"
export MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc MOTOR_LAUNCHD_DIR="$S/launchd"
mkdir -p "$S/launchd"

# --- ssh falso: la Mini corre de verdad en su HOME falso; la PC solo registra.
cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
if [ "$host" = gwpc ]; then
  case "$*" in
    "tar -x"*) cat >"$S/pc-recibido.tar" ;;
    "schtasks /query"*) exit "${FAKE_PC_TAREA_RC:-0}" ;;
    "echo ok") echo ok ;;
    *) [ -z "${FAKE_PC_FALLA:-}" ] || { echo "git: no pude" >&2; exit 1; } ;;
  esac
  exit 0
fi
HOME="$S/mini" bash -c "$*"
FAKE
# sudo y launchctl falsos: instalar no debe llamar a sudo jamás; desinstalar lo usa con -n.
cat >"$S/bin/sudo" <<'FAKE'
#!/usr/bin/env bash
echo "sudo $*" >>"$S/sudo.log"
[ -z "${FAKE_SUDO_FALLA:-}" ] || exit 1
[ "$1" = -n ] && shift
case "$1" in
  launchctl) rm -f "$HOME/.cargado" ;;
  rm) shift; rm "$@" ;;
esac
FAKE
cat >"$S/bin/launchctl" <<'FAKE'
#!/usr/bin/env bash
[ "$1" = print ] && [ -e "$HOME/.cargado" ]
FAKE
chmod +x "$S/bin/"*

reset() { rm -rf "$S/mac" "$S/mini" "$S/sudo.log" "$S/pc-recibido.tar" "$S/launchd"/*; mkdir -p "$S/mac" "$S/mini"; : >"$FAKE_LOG"; unset FAKE_CAE FAKE_PC_FALLA FAKE_SUDO_FALLA FAKE_PC_TAREA_RC; }
I="$RAIZ/ops/instala-todo.sh"
D="$RAIZ/ops/desinstala-todo.sh"
en_mac() { HOME="$S/mac" "$@"; }

echo "# instalar"
reset
# El motor exporta MOTOR_RAIZ (su propia carpeta, sin git) a quien despliega: no debe romper la instalación.
export MOTOR_RAIZ="$S/no-es-repo"
en_mac espera "instala en las tres" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.0^{commit}")" = "$SHA" ] || fallo "la etiqueta v1.0.0 no quedó en el SHA mergeado"
for m in mac mini; do
  [ "$(git -C "$S/$m/motor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: ~/motor no está en la etiqueta"
  [ "$(git -C "$S/$m/motor" describe --tags --exact-match)" = v1.0.0 ] || fallo "$m: ~/motor sin v1.0.0"
  [ -d "$S/$m/motor-conf/.git" ] || fallo "$m: falta ~/motor-conf"
  cmp -s "$S/conf-src/maquinas/$m.toml" "$S/$m/.motor/maquina.toml" || fallo "$m: maquina.toml no es el de la conf"
  contiene "$m txt" "$S/$m/.motor/instalar-servicio.txt" "motor instala --maquina $m"
  contiene "$m txt" "$S/$m/.motor/instalar-servicio.txt" "--imprime"
done
contiene "sudoers mac" "$S/mac/.motor/instalar-servicio.txt" "pmset"
contiene "sudoers mini" "$S/mini/.motor/instalar-servicio.txt" "/sbin/shutdown"
[ -e "$S/sudo.log" ] && fallo "instalar llamó a sudo"
tar -xf "$S/pc-recibido.tar" -O instalar-servicio.txt >"$S/pc.txt" 2>/dev/null
contiene "txt de la PC" "$S/pc.txt" "motor instala --maquina pc"
contiene "txt de la PC" "$S/pc.txt" "schtasks /create"
contiene "la PC usa la etiqueta" "$FAKE_LOG" "refs/tags/v1.0.0"
contiene "la PC clona la conf" "$FAKE_LOG" "motor-conf"
grep -qE 'dev/goncloud-motor|\.motor-arranque' "$FAKE_LOG" && fallo "tocó ~/dev/goncloud-motor o ~/.motor-arranque"
[ -e "$S/mac/dev" ] || [ -e "$S/mac/.motor-arranque" ] && fallo "creó carpetas ajenas en la Mac"

en_mac espera "segunda corrida (actualiza, no reclona)" 0 bash "$I"

# una etiqueta avanzada por otro commit: la segunda corrida respeta la v1.0.0 existente
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m otro && git -C "$S/trabajo" push -q origin HEAD:main
en_mac espera "la etiqueta existente se respeta" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.0^{commit}")" = "$SHA" ] || fallo "la etiqueta existente se movió"
for m in mac mini; do [ "$(git -C "$S/$m/motor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: ~/motor dejó la etiqueta cuando main avanzó"; done

echo "# instalar con fallas"
reset; git -C "$S/origen.git" tag -d v1.0.0 >/dev/null
FAKE_CAE="gon@mini-fake" en_mac espera "ssh a la Mini falla" '!0' bash "$I"
contiene "dice cuál" "$S/salida" "mini"
[ -d "$S/mac/motor/.git" ] || fallo "la Mac debía instalarse aunque la Mini fallara"
reset
FAKE_PC_FALLA=1 en_mac espera "el remoto de la PC falla" '!0' bash "$I"
contiene "dice cuál" "$S/salida" "pc"

reset; git -C "$S/origen.git" tag -d v1.0.0 >/dev/null
git -C "$S/trabajo" checkout -q --detach HEAD
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m sin-mergear
en_mac espera "SHA sin mergear no se etiqueta" '!0' bash "$I"
git -C "$S/origen.git" rev-parse -q --verify refs/tags/v1.0.0 >/dev/null && fallo "etiquetó un SHA sin mergear"
git -C "$S/trabajo" reset -q --hard origin/main

# una v1.0.0 local (no está en origin) sobre un commit sin mergear no debe publicarse
reset; git -C "$S/origen.git" tag -d v1.0.0 >/dev/null 2>&1
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m viejo-sin-mergear
git -C "$S/trabajo" tag -f v1.0.0 >/dev/null
git -C "$S/trabajo" reset -q --hard origin/main
SHA="$(git -C "$S/trabajo" rev-parse HEAD)"
en_mac espera "etiqueta local vieja no se publica" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.0^{commit}")" = "$SHA" ] || fallo "origin quedó con v1.0.0 en otro commit que el SHA mergeado"

echo "# desinstalar"
reset; en_mac bash "$I" >/dev/null 2>&1
touch "$S/mac/.cargado" "$S/mini/.cargado" "$S/launchd/com.goncloud.motor.plist"
echo registro >"$S/mac/.motor/registro.log"
en_mac espera "descarga en las tres" 0 bash "$D"
[ -e "$S/mac/.cargado" ] && fallo "la Mac sigue cargada"
[ -e "$S/mini/.cargado" ] && fallo "la Mini sigue cargada"
contiene "la PC borra la tarea" "$FAKE_LOG" "schtasks /delete /tn GoncloudMotor"
[ -d "$S/mac/motor" ] && [ -d "$S/mac/motor-conf" ] && [ "$(cat "$S/mac/.motor/registro.log")" = registro ] || fallo "desinstalar borró carpetas o registros"
en_mac espera "ya descargado también es 0" 0 bash "$D"

touch "$S/mac/.cargado"
FAKE_SUDO_FALLA=1 en_mac espera "sin contraseña no puede descargar" '!0' bash "$D"
[ -e "$S/mac/.motor/desinstalar-servicio.txt" ] || fallo "no dejó los comandos para la persona"
FAKE_CAE="gon@mini-fake" en_mac espera "ssh a la Mini falla al desinstalar" '!0' bash "$D"
FAKE_PC_FALLA=1 FAKE_PC_TAREA_RC=0 en_mac espera "la PC no puede borrar la tarea" '!0' bash "$D"
FAKE_CAE=gwpc FAKE_PC_TAREA_RC=1 en_mac espera "sin ssh a la PC" '!0' bash "$D"

termina
