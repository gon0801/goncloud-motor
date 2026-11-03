#!/usr/bin/env bash
# ops/instala-todo.sh y ops/desinstala-todo.sh de verdad (git, bash y el motor reales) contra repos
# locales, un HOME de mentira por máquina y un ssh falso: la Mac y la Mini corren su script en su
# HOME falso; la PC solo registra lo que se le pide. Exige ≠0 cuando el remoto falla y 0 cuando todo está bien.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

# --- repos locales: el origen desechable es el candidato B–E, no solo el commit base.
git -C "$RAIZ" rev-parse HEAD >/dev/null || exit 2
rm -rf "$S/semilla"
git clone -q "$RAIZ" "$S/semilla"
git -C "$RAIZ" diff --binary >"$S/candidato.diff"
if [ -s "$S/candidato.diff" ]; then
  git -C "$S/semilla" apply --whitespace=nowarn "$S/candidato.diff" || exit 2
fi
while IFS= read -r -d '' f; do
  mkdir -p "$S/semilla/$(dirname "$f")"
  cp "$RAIZ/$f" "$S/semilla/$f"
done < <(git -C "$RAIZ" ls-files --others --exclude-standard -z)
git -C "$S/semilla" add -A
if ! git -C "$S/semilla" diff --cached --quiet; then
  git -C "$S/semilla" -c user.name=t -c user.email=t@t commit -q -m "candidato B-E" || exit 2
fi
git init -q --bare -b main "$S/origen.git"
git -C "$S/semilla" push -q "$S/origen.git" HEAD:refs/heads/main || exit 2
SHA="$(git -C "$S/origen.git" rev-parse main)"
# Base previa a MOTOR 09: la comprobación debe servir también después del commit/merge.
PADRE="$(git -C "$S/semilla" rev-parse '1b37d63dfbc7157ffa42b5680505ed8e8962f4a1^{commit}')"
[ "$(git -C "$S/semilla" show 'HEAD:motor/formato.py' | sed -n 's/^VERSION = //p')" = 3 ] || exit 2
[ "$(git -C "$S/semilla" show "$PADRE:motor/formato.py" | sed -n 's/^VERSION = //p')" = 2 ] || exit 2
git clone -q "$S/origen.git" "$S/trabajo"
git init -q -b main "$S/conf-src"
cp -R "$RAIZ/conf-ejemplo/." "$S/conf-src/"
mkdir -p "$S/conf-src/maquinas"
for m in mac mini pc; do printf 'nombre = "%s"\nconf = "~/motor-conf"\n' "$m" >"$S/conf-src/maquinas/$m.toml"; done
git -C "$S/conf-src" add -A
git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m conf
git clone -q --bare "$S/conf-src" "$S/conf.git"

export MOTOR_REPO_URL="$S/origen.git" MOTOR_CONF_URL="$S/conf.git" MOTOR_INSTALA_RAIZ="$S/trabajo"
export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc MOTOR_LAUNCHD_DIR="$S/launchd"
mkdir -p "$S/launchd"

# --- ssh falso: la Mac y la Mini corren de verdad en su HOME falso; la PC solo registra.
cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
# FAKE_CAE_DESPUES: contesta la lectura y la validación; cae en la instalación (FASE_INSTALA).
case " ${FAKE_CAE_DESPUES:-} " in
  *" $host "*) case "$*" in *FASE_INSTALA*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac ;;
esac
if [ "$host" = gwpc ]; then
  ok_pc() { [ -z "${FAKE_PC_FALLA:-}" ] || { echo "git: no pude" >&2; exit 1; }; }
  if [[ "$*" == *'python -c "'* && "$*" != *sol_remoto* ]]; then
    cmd="$*"
    tmp="${cmd#*python -c \"}"
    code="${tmp%%\"*}"
    rest="${tmp#*\"}"
    # shellcheck disable=SC2086
    HOME="$S/pc-home" USERPROFILE="$S/pc-home" python3 -c "$code" $rest
    exit $?
  fi
  case "$*" in
    "tar -x"*) cat >"$S/pc-recibido.tar" ;;
    "schtasks /query"*) exit "${FAKE_PC_TAREA_RC:-0}" ;;
    "echo ok") echo ok ;;
    # Estado de mentira de la PC: la etiqueta que tiene ~/motor y la marca de la marcha atrás.
    *"describe --tags --exact-match"*) [ -f "$S/pc-tag" ] && { cat "$S/pc-tag"; exit 0; } || exit 1 ;;
    *"tag --points-at HEAD"*) [ -f "$S/pc-tag" ] && printf '%s\r\n' "$(cat "$S/pc-tag")"; exit 0 ;;
    # La marcha atrás guarda el SHA, no el nombre de la etiqueta. pc-tag sigue siendo el nombre.
    *"rev-parse HEAD")
      if [ -n "${FAKE_PC_REVPARSE_RC:-}" ]; then exit "$FAKE_PC_REVPARSE_RC"; fi
      case "$*" in
        *motor-conf*)
          if [ -s "$S/pc-conf-sha" ]; then tr -d '[:space:]' <"$S/pc-conf-sha"; printf '\r\n'; exit 0; fi
          case "$*" in *"if exist"*) exit 0 ;; *) exit 1 ;; esac ;;
      esac
      if [ ! -f "$S/pc-tag" ]; then
        case "$*" in *"if exist"*) exit 0 ;; *) exit 1 ;; esac
      fi
      nombre="$(tr -d '[:space:]' <"$S/pc-tag")"
      if ! sha_pc="$(git -C "$S/origen.git" rev-parse -q --verify "refs/tags/${nombre}^{commit}" 2>/dev/null)"; then
        sha_pc="$(git -C "$S/origen.git" rev-parse -q --verify "${nombre}^{commit}")" || exit 1
      fi
      printf '%s\r\n' "$sha_pc" ;;
    *"rev-parse"*"--verify "*)
      if [ -n "${FAKE_PC_OBJETO_TRAS_FETCH:-}" ] && [ ! -f "$S/pc-fetch-hecho" ]; then exit 1; fi
      arg="${*##*--verify }"
      arg="${arg%% *}"
      arg="${arg#refs/tags/}"
      if ! sha_pc="$(git -C "$S/origen.git" rev-parse -q --verify "refs/tags/${arg}^{commit}" 2>/dev/null)"; then
        sha_pc="$(git -C "$S/origen.git" rev-parse -q --verify "${arg}^{commit}")" || exit 1
      fi
      printf '%s\r\n' "$sha_pc" ;;
    *"motor-conf checkout -q --detach "*)
      rev="${*##*--detach }"
      rev="${rev%% *}"
      printf '%s\n' "$rev" >"$S/pc-conf-sha"
      mkdir -p "$S/pc-home/motor-conf/maquinas"
      git -C "$S/conf.git" show "$rev:maquinas/pc.toml" >"$S/pc-home/motor-conf/maquinas/pc.toml" || exit 1 ;;
    *"\\motor checkout -q --detach "*)
      rev="${*##*--detach }"
      rev="${rev%% *}"
      rev="${rev#refs/tags/}"
      if git -C "$S/origen.git" rev-parse -q --verify "refs/tags/${rev}^{commit}" >/dev/null 2>&1; then
        printf '%s\n' "$rev" >"$S/pc-tag"
      else
        tag="$(git -C "$S/origen.git" describe --tags --exact-match "$rev" 2>/dev/null || true)"
        printf '%s\n' "${tag:-$rev}" >"$S/pc-tag"
      fi ;;
    "type nul >"*"etiqueta-anterior.tmp"*) : >"$S/pc-etiqueta-anterior" ;;
    "type "*"etiqueta-anterior"*) [ -f "$S/pc-etiqueta-anterior" ] && { cat "$S/pc-etiqueta-anterior"; exit 0; } || exit 1 ;;
    "echo "*"etiqueta-anterior"*) v="${*#echo }"; echo "${v%%>*}" >"$S/pc-etiqueta-anterior" ;;
    # cmd.exe: sin paréntesis, `& mkdir` es parte del cuerpo del if y no corre si la carpeta no existe.
    *"if exist %TEMP%\\motor-valida-"*" & mkdir "*) rm -f "$S/pc-extraido-"*; touch "$S/pc-codigo-creado" ;;
    "if exist "*" & mkdir "*) ;;
    "git clone -q "*)
      ok_pc
      case "$*" in
        *%TEMP%\\motor-valida-*) ;;
        *) echo "clone fuera del temporal: $*" >&2; exit 1 ;;
      esac ;;
    *"\\src archive refs/tags/"*" | tar -x -C "*)
      [ -e "$S/pc-codigo-creado" ] || { echo "tar: could not chdir" >&2; exit 1; }; touch "$S/pc-extraido-codigo" ;;
    *"\\csrc archive "*)
      [ -e "$S/pc-codigo-creado" ] || { echo "tar: could not chdir" >&2; exit 1; }; touch "$S/pc-extraido-conf" ;;
    "if not exist %TEMP%\\motor-valida-"*"\\conf\\maquinas\\pc.toml exit /b 1") [ -z "${FAKE_PC_SIN_TOML:-}" ] || exit 1 ;;
    "cd /d %TEMP%\\motor-valida-"*"\\codigo && "*" conf valida %TEMP%\\motor-valida-"*"\\conf")
      [ -e "$S/pc-extraido-codigo" ] && [ -e "$S/pc-extraido-conf" ] || { echo "No module named motor" >&2; exit 1; }
      exit "${FAKE_PC_VALIDA_RC:-0}" ;;
    "cd /d %USERPROFILE%\\motor && "*" conf valida %USERPROFILE%\\motor-conf") ;;
    "if exist "*"\\.git (git "* | "schtasks /end"* | "schtasks /delete"*) ok_pc ;;
    "git -C "*" fetch -q --tags --force origin")
      [ -z "${FAKE_GIT_ORIGIN_CAIDO:-}" ] || { echo "fatal: no pude conectar (origin caído)" >&2; exit 1; }
      touch "$S/pc-fetch-hecho" ;;
    *"copy /y "*maquina.toml*)
      mkdir -p "$S/pc-home/.motor"
      cp "$S/pc-home/motor-conf/maquinas/pc.toml" "$S/pc-home/.motor/maquina.toml" || exit 1 ;;
    *"mklink /j "*)
      mkdir -p "$S/pc-home/.motor" "$S/pc-home/motor-conf"
      rm -f "$S/pc-home/.motor/conf"
      ln -sfn "$S/pc-home/motor-conf" "$S/pc-home/.motor/conf" ;;
    "if not exist "* | "rmdir /s /q "* | "git -C "*" checkout -q --detach origin/HEAD" | "git -C "*" fetch -q --tags --force origin") ;;
    *"cat-file -e "*":maquinas/"*)
      spec="${*##*cat-file -e }"
      spec="${spec%% *}"
      git -C "$S/conf.git" cat-file -e "$spec" ;;
    *"cat-file -e "*"motor/formato.py")
      [ -z "${FAKE_PC_SIN_FORMATO:-}" ] || exit 1
      if [ -n "${FAKE_PC_OBJETO_TRAS_FETCH:-}" ] && [ ! -f "$S/pc-fetch-hecho" ]; then exit 1; fi
      spec="${*##*cat-file -e }"
      spec="${spec%% *}"
      git -C "$S/origen.git" cat-file -e "$spec" ;;
    *"if exist"*"formato echo si"*) if [ -f "$S/pc-formato" ]; then echo si; else echo no; fi ;;
    *"if exist"*"\\formato (type "*)
      if [ ! -f "$S/pc-formato" ]; then exit 1; fi
      if [ -s "$S/pc-formato" ]; then cat "$S/pc-formato"; fi
      exit 0 ;;
    *"-m motor formato"*) if [ -f "$S/pc-formato-bloquea" ]; then exit 1; fi ;;
    *sol_remoto.py*) ;;
    *) echo "comando no reconocido por la PC: $*" >&2; exit 1 ;;
  esac
  exit 0
fi
case "$*" in *hostname*) hostname; exit 0 ;; esac
if [ "$host" = mac ]; then
  HOME="$S/mac" bash -c "$*"
else
  HOME="$S/mini" bash -c "$*"
fi
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

# git de verdad, pero con fallas de red a pedido: el primer fetch falla si FAKE_GIT_FETCH_FALLA=1
# (transitorio: un reintento lo absorbe), todo ls-remote falla si FAKE_GIT_LSREMOTO_FALLA=1 y
# todo fetch y ls-remote falla siempre que FAKE_GIT_ORIGIN_CAIDO=1.
GIT_REAL="$(command -v git)"
cat >"$S/bin/git" <<FAKE
#!/usr/bin/env bash
if [ -n "\${FAKE_GIT_ORIGIN_CAIDO:-}" ] && [ "\$1" = -C ] && { [ "\$3" = fetch ] || [ "\$3" = ls-remote ]; }; then
  echo "fatal: no pude conectar (origin caído)" >&2; exit 128
fi
if [ -n "\${FAKE_GIT_FETCH_FALLA:-}" ] && [ "\$1" = -C ] && [ "\$3" = fetch ]; then
  if [ ! -e "$S/fetch-fallo" ]; then touch "$S/fetch-fallo"; echo "fatal: cannot lock ref" >&2; exit 128; fi
fi
if [ -n "\${FAKE_GIT_LSREMOTO_FALLA:-}" ] && [ "\$1" = -C ] && [ "\$3" = ls-remote ]; then
  echo "fatal: no pude conectar" >&2; exit 128
fi
if [ -n "\${FAKE_GIT_CHECKOUT_FALLA_EN:-}" ] && [ "\$1" = -C ] && [ "\$3" = checkout ]; then
  n=\$(( \$(cat "$S/checkout-n" 2>/dev/null || echo 0) + 1 ))
  echo "\$n" >"$S/checkout-n"
  if [ "\$n" = "\${FAKE_GIT_CHECKOUT_FALLA_EN}" ]; then echo "fatal: checkout cortado" >&2; exit 128; fi
fi
exec $GIT_REAL "\$@"
FAKE
chmod +x "$S/bin/git"

reset() { git -C "$S/origen.git" tag -l | xargs -r git -C "$S/origen.git" tag -d >/dev/null; rm -rf "$S/mac" "$S/mini" "$S/n-"* "$S/pc-home" "$S/pc-codigo-creado" "$S/pc-formato" "$S/pc-formato-bloquea" "$S/pc-fetch-hecho" "$S/sudo.log" "$S/pc-recibido.tar" "$S/pc-tag" "$S/pc-conf-sha" "$S/pc-etiqueta-anterior" "$S/pc-instalacion.json" "$S/pc-instalacion-pendiente.json" "$S/checkout-n" "$S/launchd"/*; mkdir -p "$S/mac/.local/bin" "$S/mini/.local/bin"; ln -sfn "$S/bin/git" "$S/mac/.local/bin/git"; ln -sfn "$S/bin/git" "$S/mini/.local/bin/git"; : >"$FAKE_LOG"; unset FAKE_CAE FAKE_CAE_DESPUES FAKE_PC_SIN_TOML FAKE_PC_VALIDA_RC FAKE_PC_FALLA FAKE_PC_SIN_FORMATO FAKE_PC_REVPARSE_RC FAKE_PC_OBJETO_TRAS_FETCH FAKE_SUDO_FALLA FAKE_PC_TAREA_RC FAKE_GIT_CHECKOUT_FALLA_EN MOTOR_ETIQUETA MOTOR_SHA; }
TAG="$(unset MOTOR_ETIQUETA; . "$RAIZ/ops/lib.sh"; echo "$ETIQUETA")"
I="$RAIZ/ops/instala-todo.sh"
D="$RAIZ/ops/desinstala-todo.sh"
en_mac() { HOME="$S/mac" "$@"; }

echo "# instalar"
reset
# El motor exporta MOTOR_RAIZ (su propia carpeta, sin git) a quien despliega: no debe romper la instalación.
export MOTOR_RAIZ="$S/no-es-repo"
en_mac espera "instala en las tres" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" = "$SHA" ] || fallo "la etiqueta $TAG no quedó en el SHA mergeado"
for m in mac mini; do
  [ "$(git -C "$S/$m/motor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: ~/motor no está en la etiqueta"
  [ "$(git -C "$S/$m/motor" describe --tags --exact-match)" = $TAG ] || fallo "$m: ~/motor sin $TAG"
  [ -d "$S/$m/motor-conf/.git" ] || fallo "$m: falta ~/motor-conf"
  cmp -s "$S/conf-src/maquinas/$m.toml" "$S/$m/.motor/maquina.toml" || fallo "$m: maquina.toml no es el de la conf"
  [ "$(readlink "$S/$m/.motor/conf")" = "$S/$m/motor-conf" ] || fallo "$m: ~/.motor/conf no enlaza a ~/motor-conf"
  contiene "$m txt" "$S/$m/.motor/instalar-servicio.txt" "motor instala --maquina $m"
  contiene "$m txt" "$S/$m/.motor/instalar-servicio.txt" "--imprime"
done
contiene "sudoers mac" "$S/mac/.motor/instalar-servicio.txt" "pmset"
contiene "sudoers mini" "$S/mini/.motor/instalar-servicio.txt" "/sbin/shutdown"
[ -e "$S/sudo.log" ] && fallo "instalar llamó a sudo"
tar -xf "$S/pc-recibido.tar" -O instalar-servicio.txt >"$S/pc.txt" 2>/dev/null
contiene "txt de la PC" "$S/pc.txt" "motor instala --maquina pc"
contiene "txt de la PC" "$S/pc.txt" "schtasks /create"
contiene "la PC usa la etiqueta" "$FAKE_LOG" "refs/tags/$TAG"
contiene "la PC clona la conf" "$FAKE_LOG" "motor-conf"
contiene "la PC enlaza la conf" "$FAKE_LOG" "mklink /j"
grep -qE 'dev/goncloud-motor|\.motor-arranque' "$FAKE_LOG" && fallo "tocó ~/dev/goncloud-motor o ~/.motor-arranque"
[ -e "$S/mac/dev" ] || [ -e "$S/mac/.motor-arranque" ] && fallo "creó carpetas ajenas en la Mac"
for m in mac mini; do
  [ -f "$S/$m/.motor/etiqueta-anterior" ] && [ ! -s "$S/$m/.motor/etiqueta-anterior" ] || fallo "$m: una instalación nueva debía dejar la marca vacía"
done
[ -f "$S/pc-etiqueta-anterior" ] && [ ! -s "$S/pc-etiqueta-anterior" ] || fallo "pc: una instalación nueva debía dejar la marca vacía"
grep -q MOTOR09_ "$FAKE_LOG" && fallo "el log de la PC usa un prefijo que cmd.exe rechaza"
grep -qF '%TEMP%\motor-valida-' "$FAKE_LOG" || fallo "la PC no validó en %TEMP%"
primero="$(grep 'git clone -q' "$FAKE_LOG" | head -1)"
case "$primero" in *motor-valida-*) ;; *) fallo "la primera copia de la PC no fue el temporal de validación: $primero" ;; esac
[ -s "$S/pc-home/.motor/instalacion.json" ] || fallo "la PC no escribió el recibo"
[ ! -e "$S/pc-home/.motor/instalacion-pendiente.json" ] || fallo "la PC dejó el intento abierto"

reset
en_mac espera "la Mac se instala por ssh" 0 bash "$I"
grep -q "^mac ::" "$FAKE_LOG" || fallo "la Mac no se instaló por ssh: no está mac en el log"
[ -d "$S/mac/motor/.git" ] || fallo "la Mac no quedó instalada en $S/mac/motor"

reset
en_mac espera "instala para el origin de la mini" 0 bash "$I"
git -C "$S/mini/motor" remote set-url origin "$S/origen-viejo.git"
git -C "$S/mini/motor-conf" remote set-url origin "$S/conf-viejo.git"
en_mac espera "el origin de la mini pasa a la direccion comun" 0 bash "$I"
[ "$(git -C "$S/mini/motor" remote get-url origin)" = "$MOTOR_REPO_URL" ] || fallo "el origin de ~/motor de la Mini no quedó en REPO_URL"
[ "$(git -C "$S/mini/motor-conf" remote get-url origin)" = "$MOTOR_CONF_URL" ] || fallo "el origin de ~/motor-conf de la Mini no quedó en CONF_URL"

reset
en_mac espera "segunda corrida (actualiza, no reclona)" 0 bash "$I"
# Instalación nueva: la marca existe y está vacía. No hay revisión anterior.
for m in mac mini; do
  [ -f "$S/$m/.motor/etiqueta-anterior" ] && [ ! -s "$S/$m/.motor/etiqueta-anterior" ] || fallo "$m: una instalación nueva debía dejar la marca vacía"
done
[ -f "$S/pc-etiqueta-anterior" ] && [ ! -s "$S/pc-etiqueta-anterior" ] || fallo "pc: una instalación nueva debía dejar la marca vacía"

# main avanzó con la etiqueta ya publicada: no se mueve y la instalación se niega
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m otro && git -C "$S/trabajo" push -q origin HEAD:main
en_mac espera "etiqueta publicada con main adelantado se niega" 3 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" = "$SHA" ] || fallo "la etiqueta existente se movió"
for m in mac mini; do [ "$(git -C "$S/$m/motor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: ~/motor dejó la etiqueta cuando main avanzó"; done

# La etiqueta ya publicada en otro commit que el que se instala: sale 3 y no toca ninguna máquina.
reset
git -C "$S/trabajo" fetch -q origin && git -C "$S/trabajo" checkout -q --detach origin/main
git -C "$S/trabajo" push -q origin "HEAD~1:refs/tags/$TAG"
en_mac espera "etiqueta en otro commit" 3 bash "$I"
contiene "dice cómo arreglarlo" "$S/salida" "sube la etiqueta en ops/lib.sh"
[ ! -e "$S/mac/motor" ] && [ ! -e "$S/mini/motor" ] && [ ! -e "$S/pc-tag" ] || fallo "tocó alguna máquina con la etiqueta en otro commit"

avanza() { git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$1" && git -C "$S/trabajo" push -q origin HEAD:main; }
tag_en() { git -C "$S/$1/motor" describe --tags --exact-match 2>/dev/null; }
# El árbol de v1.0.23 y el de la etiqueta nueva son el mismo: para distinguir código nuevo de viejo, el commit
# nuevo cambia `conf valida` para que rechace una conf que el viejo acepta (un archivo marca).
rechaza_la_marca() {
  python3 - "$S/trabajo/motor/__main__.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
a = "    errores = _construye(d)\n"
assert s.count(a) == 1
open(p, "w").write(s.replace(a, a + "    if (d / 'solo-el-nuevo-rechaza').exists():\n        errores.append('solo-el-nuevo-rechaza')\n"))
PY
  git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q -am "el código nuevo rechaza la marca" && git -C "$S/trabajo" push -q origin HEAD:main
}
conf_buena() { git -C "$S/conf-src" reset -q --hard "$BUENA" && git -C "$S/conf-src" push -q -f "$S/conf.git" HEAD:refs/heads/main; }
nada_se_movio() { # nada_se_movio <caso>
  for m in mac mini; do [ "$(tag_en $m)" = v1.0.23 ] || fallo "$1: $m cambió de etiqueta (quedó en $(tag_en $m))"; done
  [ "$(cat "$S/pc-tag")" = v1.0.23 ] || fallo "$1: pc cambió de etiqueta"
  grep -q "detach refs/tags/$TAG" "$FAKE_LOG" && fallo "$1: la PC cambió a $TAG"
}
base_y_codigo_nuevo() {
  reset
  MOTOR_ETIQUETA=v1.0.23 en_mac espera "base v1.0.23 $1" 0 bash "$I"
  unset MOTOR_ETIQUETA
  rechaza_la_marca
  BUENA="$(git -C "$S/conf-src" rev-parse HEAD)"
}

echo "# conf que el código nuevo rechaza"
base_y_codigo_nuevo "antes de la conf rota"
git -C "$S/trabajo" -c user.name=t -c user.email=t@t revert --no-edit HEAD >/dev/null && git -C "$S/trabajo" push -q origin HEAD:main && git -C "$S/trabajo" checkout -q --detach HEAD~1
touch "$S/conf-src/solo-el-nuevo-rechaza"
git -C "$S/conf-src" add -A && git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m rota
git -C "$S/conf-src" push -q -f "$S/conf.git" HEAD:refs/heads/main
en_mac espera "conf rechazada por el código nuevo" '!0' bash "$I"
contiene "dice quién la rechaza" "$S/salida" "rechaza la conf"
contiene "dice que nada cambió" "$S/salida" "No se cambió el código en ninguna máquina"
contiene "la PC valida" "$FAKE_LOG" "conf valida"
nada_se_movio "conf rota"
git -C "$S/trabajo" checkout -q --detach origin/main
conf_buena

# El veredicto de `conf valida` en la PC cuenta: si lo rechaza, nadie cambia.
reset; MOTOR_ETIQUETA=v1.0.23 en_mac espera "base v1.0.23 para el veredicto de la PC" 0 bash "$I"; unset MOTOR_ETIQUETA
avanza "la-etiqueta-nueva-en-otro-commit"
FAKE_PC_VALIDA_RC=1 en_mac espera "la PC rechaza la conf" '!0' bash "$I"
contiene "dice que la PC la rechaza" "$S/salida" "pc: el código de $TAG rechaza la conf"
nada_se_movio "la PC rechaza la conf"
FAKE_PC_SIN_TOML=1 en_mac espera "la conf no trae maquinas/pc.toml" '!0' bash "$I"
contiene "dice cuál falta" "$S/salida" "maquinas/pc.toml"
nada_se_movio "falta pc.toml"
contiene "la PC arma la carpeta temporal con paréntesis" "$FAKE_LOG" "(if exist %TEMP%"

git -C "$S/conf-src" rm -q maquinas/mini.toml && git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m sin-mini
git -C "$S/conf-src" push -q -f "$S/conf.git" HEAD:refs/heads/main
en_mac espera "la conf no trae maquinas/mini.toml" '!0' bash "$I"
contiene "dice cuál falta" "$S/salida" "maquinas/mini.toml"
nada_se_movio "falta mini.toml"
conf_buena

# Una máquina sin ssh al validar es lo mismo que una que rechaza: nadie cambia.
FAKE_CAE="gon@mini-fake" en_mac espera "la Mini sin ssh al validar" '!0' bash "$I"
contiene "dice cuál" "$S/salida" "mini"
nada_se_movio "Mini sin ssh"
FAKE_CAE=gwpc en_mac espera "la PC sin ssh al validar" '!0' bash "$I"
contiene "dice que no tocó código" "$S/salida" "reintenta sin tocar codigo"
nada_se_movio "PC sin ssh"
FAKE_PC_FALLA=1 en_mac espera "la PC no puede validar" '!0' bash "$I"
nada_se_movio "la PC no puede validar"

echo "# instalar con fallas"
reset
FAKE_CAE_DESPUES="gon@mini-fake" en_mac espera "ssh a la Mini cae al instalar" '!0' bash "$I"
contiene "dice cuál" "$S/salida" "mini"
[ -d "$S/mac/motor/.git" ] || fallo "la Mac debía instalarse aunque la Mini cayera al instalar"
reset
FAKE_PC_FALLA=1 en_mac espera "el remoto de la PC falla" '!0' bash "$I"
contiene "dice cuál" "$S/salida" "pc"
reset
mkdir -p "$S/pc-home/.motor"
: >"$S/pc-home/.motor/formato"
touch "$S/pc-formato-bloquea"
FAKE_PC_SIN_FORMATO=1 en_mac espera "la PC rechaza una etiqueta sin motor/formato.py" '!0' bash "$I"

reset
git -C "$S/trabajo" checkout -q --detach HEAD
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m sin-mergear
en_mac espera "SHA sin mergear no se etiqueta" '!0' bash "$I"
git -C "$S/origen.git" rev-parse -q --verify refs/tags/$TAG >/dev/null && fallo "etiquetó un SHA sin mergear"
git -C "$S/trabajo" reset -q --hard origin/main

# una $TAG local (no está en origin) sobre un commit sin mergear no debe publicarse
reset
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m viejo-sin-mergear
git -C "$S/trabajo" tag -f $TAG >/dev/null
git -C "$S/trabajo" reset -q --hard origin/main
SHA="$(git -C "$S/trabajo" rev-parse HEAD)"
en_mac espera "etiqueta local vieja no se publica" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" = "$SHA" ] || fallo "origin quedó con $TAG en otro commit que el SHA mergeado"

echo "# red transitoria"
# El repo del despliegue lo comparte el daemon (fetcha seguido): un fetch puede chocar con un lock.
reset; rm -f "$S/fetch-fallo"
FAKE_GIT_FETCH_FALLA=1 en_mac espera "un fetch que falla una vez se reintenta" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" = "$SHA" ] || fallo "no quedó la etiqueta tras el reintento"

# Sin respuesta de origin no se sabe si la etiqueta existe: peor que fallar es pisarla o duplicarla.
reset
FAKE_GIT_LSREMOTO_FALLA=1 en_mac espera "ls-remote caído no es etiqueta ausente" '!0' bash "$I"
git -C "$S/origen.git" rev-parse -q --verify refs/tags/$TAG >/dev/null && fallo "etiquetó sin poder consultar origin"

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

echo "# marcha atrás"
V="$RAIZ/ops/vuelve-a-la-anterior.sh"
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "entrega anterior en v1.0.23" 0 bash "$I"
unset MOTOR_ETIQUETA
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m avance && git -C "$S/trabajo" push -q origin HEAD:main
touch "$S/mac/.cargado" "$S/mini/.cargado"
en_mac espera "entrega nueva en $TAG" 0 bash "$I"
sha_vieja="$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.23^{commit}")"
for m in mac mini; do
  contiene "marca $m" "$S/$m/.motor/etiqueta-anterior" "$sha_vieja"
  [ "$(git -C "$S/$m/motor" describe --tags --exact-match)" = $TAG ] || fallo "$m: no quedó en $TAG"
done
contiene "marca pc" "$S/pc-etiqueta-anterior" "$sha_vieja"
[ "$(cat "$S/pc-tag")" = $TAG ] || fallo "pc: no quedó en $TAG"

# etiqueta-anterior no es fuente. Borrarla no frena la reversa si el recibo tiene el origen.
rm "$S/mac/.motor/etiqueta-anterior"
en_mac espera "marcha atrás en las tres" 0 bash "$V"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "mac: el recibo no mandó sobre la marca borrada"
[ "$(git -C "$S/mini/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "mini: no volvió a v1.0.23"
[ "$(cat "$S/pc-tag")" = v1.0.23 ] || fallo "pc: no volvió a v1.0.23"
[ -e "$S/mac/.cargado" ] && [ -e "$S/mini/.cargado" ] || fallo "la marcha atrás descargó el servicio"
grep -q "schtasks /delete" "$FAKE_LOG" && fallo "la marcha atrás borró la tarea de la PC"
[ ! -e "$S/sudo.log" ] || fallo "la marcha atrás llamó a sudo"

# Una entrega que falla en una máquina, seguida de su marcha atrás: las otras dos vuelven a
# su etiqueta de antes y el servicio sigue cargado en las tres.
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base v1.0.23" 0 bash "$I"
unset MOTOR_ETIQUETA
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m avance2 && git -C "$S/trabajo" push -q origin HEAD:main
touch "$S/mac/.cargado" "$S/mini/.cargado"
rm -f "$S"/n-*
FAKE_CAE_DESPUES="gon@mini-fake" en_mac espera "entrega que falla en la Mini" '!0' bash "$I"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = $TAG ] || fallo "mac: no quedó en $TAG"
[ "$(git -C "$S/mini/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "mini: se movió pese al ssh caído"
FAKE_CAE="gon@mini-fake" en_mac espera "marcha atrás con la Mini caída" '!0' bash "$V"
contiene "dice cuál" "$S/salida" "mini"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "mac: no volvió a v1.0.23"
[ "$(cat "$S/pc-tag")" = v1.0.23 ] || fallo "pc: no volvió a v1.0.23"
[ -e "$S/mac/.cargado" ] && [ -e "$S/mini/.cargado" ] || fallo "la marcha atrás descargó el servicio"
grep -q "schtasks /delete" "$FAKE_LOG" && fallo "la marcha atrás borró la tarea de la PC"
[ ! -e "$S/sudo.log" ] || fallo "la marcha atrás llamó a sudo"

dos_entregas() { # deja las tres en $2, con la marca en $1 (como tras dos entregas buenas)
  MOTOR_ETIQUETA=$1 en_mac espera "entrega $1" 0 bash "$I"; unset MOTOR_ETIQUETA
  avanza "$2"
  MOTOR_ETIQUETA=$2 en_mac espera "entrega $2" 0 bash "$I"; unset MOTOR_ETIQUETA
  avanza "siguiente-$2"
}
en_su_etiqueta() { # en_su_etiqueta <caso> <etiqueta de antes de la entrega>
  for m in mac mini; do [ "$(tag_en $m)" = "$2" ] || fallo "$1: $m tenía $2 antes de la entrega y la marcha atrás la dejó en $(tag_en $m)"; done
  [ "$(cat "$S/pc-tag")" = "$2" ] || fallo "$1: pc tenía $2 antes de la entrega y la marcha atrás la dejó en $(cat "$S/pc-tag")"
}

# La entrega falla antes de tocar ninguna máquina (origin no responde): la marcha atrás no
# puede bajar a nadie a la marca de la entrega anterior.
reset
dos_entregas v1.0.22 v1.0.23
export MOTOR_ETIQUETA=$TAG
FAKE_GIT_LSREMOTO_FALLA=1 en_mac espera "entrega $TAG falla antes de tocar las máquinas" '!0' bash "$I"; unset FAKE_GIT_LSREMOTO_FALLA
en_su_etiqueta "precondición: la entrega no tocó las máquinas" v1.0.23
en_mac espera "marcha atrás de una entrega que no tocó las máquinas" 0 bash "$V"
contiene "dice que se queda" "$S/salida" "no en el commit de la entrega"
en_su_etiqueta "entrega que no tocó las máquinas" v1.0.23
unset MOTOR_ETIQUETA

# La entrega falla en una máquina sin tocarla (ssh caído) y la máquina ya responde en la
# marcha atrás: se queda en su etiqueta, no baja a la marca vieja.
reset
dos_entregas v1.0.32 v1.0.33
export MOTOR_ETIQUETA=v1.0.34
FAKE_CAE="gon@mini-fake" en_mac espera "entrega v1.0.34 falla en la Mini" '!0' bash "$I"; unset FAKE_CAE
[ "$(tag_en mini)" = v1.0.33 ] || fallo "precondición: la Mini debía seguir en v1.0.33"
en_mac espera "marcha atrás con la Mini de vuelta" 0 bash "$V"
en_su_etiqueta "entrega que no tocó la Mini" v1.0.33
unset MOTOR_ETIQUETA

# La instalación se niega porque $TAG ya está publicada en otro commit (una entrega buena la
# dejó en las tres) y el motor corre la marcha atrás con MOTOR_SHA: nadie baja de $TAG.
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "entrega anterior v1.0.23 para la negativa" 0 bash "$I"
avanza "para-la-etiqueta"
en_mac espera "entrega $TAG para la negativa" 0 bash "$I"
avanza "sin-subir-la-etiqueta"
en_mac espera "entrega sin subir la etiqueta se niega" 3 bash "$I"
MOTOR_SHA="$(git -C "$S/trabajo" rev-parse HEAD)" en_mac espera "marcha atrás de una entrega que se negó" 0 bash "$V"
contiene "dice que nadie se mueve" "$S/salida" "nadie se mueve"
en_su_etiqueta "entrega que se negó" "$TAG"

# La instalación se niega (la etiqueta ya está publicada en otro commit) y la marcha atrás
# corre con origin caído: nadie se mueve (revisión 2 de PSTACK 02 B).
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "entrega anterior v1.0.23 para la negativa con origin caído" 0 bash "$I"; unset MOTOR_ETIQUETA
avanza "para-la-negativa-origin-caido"
en_mac espera "entrega $TAG para la negativa con origin caído" 0 bash "$I"
avanza "sin-subir-con-origin-caido"
en_mac espera "entrega que se niega, con origin caído después" 3 bash "$I"
FAKE_GIT_ORIGIN_CAIDO=1 MOTOR_SHA="$(git -C "$S/trabajo" rev-parse HEAD)" en_mac espera "marcha atrás con origin caído de una entrega que se negó" 0 bash "$V"
contiene "dice que nadie se mueve" "$S/salida" "nadie se mueve"
en_su_etiqueta "entrega que se negó con origin caído" "$TAG"

# Con origin caído, una máquina en el commit de la entrega vuelve a su etiqueta anterior
# con la copia local (se instaló antes): la marcha atrás no necesita origin.
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base v1.0.23 para volver sin origin" 0 bash "$I"; unset MOTOR_ETIQUETA
avanza "avance-para-volver-sin-origin"
rm -f "$S"/n-*
FAKE_CAE_DESPUES="gon@mini-fake" en_mac espera "entrega $TAG que falla en la Mini al instalar" '!0' bash "$I"
[ "$(tag_en mac)" = $TAG ] || fallo "precondición: la Mac debía quedar en $TAG"
[ "$(tag_en mini)" = v1.0.23 ] || fallo "precondición: la Mini debía seguir en v1.0.23"
FAKE_GIT_ORIGIN_CAIDO=1 MOTOR_SHA="$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" en_mac espera "marcha atrás sin origin" 0 bash "$V"
en_su_etiqueta "marcha atrás sin origin" v1.0.23

# MOTOR_SHA manda sobre el HEAD de la raíz: la marcha atrás mueve a las máquinas que están
# en el commit de MOTOR_SHA; un script que use el HEAD no mueve a nadie y este caso falla.
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base v1.0.23 para MOTOR_SHA" 0 bash "$I"; unset MOTOR_ETIQUETA
avanza "avance-para-motor-sha"
en_mac espera "entrega $TAG para MOTOR_SHA" 0 bash "$I"
avanza "head-que-no-es-la-entrega"
MOTOR_SHA="$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" en_mac espera "marcha atrás con MOTOR_SHA distinto del HEAD" 0 bash "$V"
en_su_etiqueta "marcha atrás con MOTOR_SHA" v1.0.23

# La etiqueta anterior puede no estar en la copia local de una máquina: se trae de origin.
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base v1.0.23 para traer la etiqueta de origin" 0 bash "$I"; unset MOTOR_ETIQUETA
avanza "avance-para-traer-de-origin"
en_mac espera "entrega $TAG para traer la etiqueta de origin" 0 bash "$I"
# El recibo pide el nombre de la etiqueta, que no está en la copia local. La marca miente.
python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p,encoding="utf-8")); d["anterior"]["codigo"]="v1.0.23"; json.dump(d, open(p,"w",encoding="utf-8"))' "$S/mac/.motor/instalacion.json"
printf '%s' no-es-esta >"$S/mac/.motor/etiqueta-anterior"
git -C "$S/mac/motor" tag -d v1.0.23 >/dev/null
MOTOR_SHA="$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" en_mac espera "marcha atrás trayendo la etiqueta de origin" 0 bash "$V"
en_su_etiqueta "marcha atrás trayendo de origin" v1.0.23

# Con el marcador de formato v2 y los efectos resueltos, la marcha atrás a un código
# que no trae motor/formato.py mueve las tres y deja el marcador.
reset
git -C "$S/trabajo" checkout -q -B main "$SHA"
git -C "$S/trabajo" push -q -f origin HEAD:main
git -C "$S/trabajo" rm -q motor/formato.py
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q -m "sin formato.py"
git -C "$S/trabajo" push -q origin HEAD:main
MOTOR_ETIQUETA=v-sin-formato en_mac espera "base sin formato.py" 0 bash "$I"
unset MOTOR_ETIQUETA
sin="$(git -C "$S/origen.git" rev-parse "refs/tags/v-sin-formato^{commit}")"
git -C "$S/trabajo" checkout -q --detach HEAD~1
en_mac espera "entrega con formato.py" 0 bash "$I"
printf '%s\n' '{"version":2,"nombre":"motor08"}' >"$S/mac/.motor/formato"
cp "$S/mac/.motor/formato" "$S/mini/.motor/formato"
mkdir -p "$S/pc-home/.motor"
cp "$S/mac/.motor/formato" "$S/pc-home/.motor/formato"
cp "$S/pc-home/.motor/formato" "$S/pc-formato-antes"
en_mac espera "reversa del formato v2" 0 bash "$V"
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$sin" ] || fallo "mac no volvió al código sin formato.py"
[ "$(git -C "$S/mini/motor" rev-parse HEAD)" = "$sin" ] || fallo "mini no volvió al código sin formato.py"
[ "$(tr -d '[:space:]' <"$S/pc-tag")" = v-sin-formato ] || fallo "pc no volvió a v-sin-formato"
git -C "$S/mac/motor" cat-file -e "HEAD:motor/formato.py" 2>/dev/null && fallo "mac volvió a un árbol que todavía tiene formato.py"
[ -s "$S/mac/.motor/formato" ] && [ -s "$S/mini/.motor/formato" ] || fallo "la reversa borró el marcador de formato"
cmp -s "$S/pc-formato-antes" "$S/pc-home/.motor/formato" || fallo "la reversa borró el marcador de la PC"

echo "# fase E: recibo, misma versión, rechazo y parcial"
# La reversa del formato dejó trabajo detached y origin/main en el commit sin formato.py.
git -C "$S/trabajo" checkout -q -B main "$SHA"
git -C "$S/trabajo" push -q -f origin HEAD:main
campo_json() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); print(eval(sys.argv[2]))' "$1" "$2"; }

reset
en_mac espera "máquina desconocida" 2 bash "$I" --maquina luna
vieja_conf="$(git -C "$S/conf-src" rev-parse HEAD)"
printf 'nombre = "mac-nueva"\nconf = "~/motor-conf"\n' >"$S/conf-src/maquinas/mac.toml"
git -C "$S/conf-src" add -A && git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m mueve
git -C "$S/conf-src" push -q -f "$S/conf.git" HEAD:refs/heads/main
en_mac espera "solo la Mac con la conf fijada" 0 bash "$I" --maquina mac --conf-sha "$vieja_conf"
[ "$(git -C "$S/mac/motor-conf" rev-parse HEAD)" = "$vieja_conf" ] || fallo "la Mac siguió la rama de conf"
grep -q 'mac-nueva' "$S/mac/.motor/maquina.toml" && fallo "la Mac instaló la conf que se movió"
[ ! -d "$S/mini/motor" ] || fallo "--maquina mac también instaló la Mini"
[ ! -s "$S/mac/.motor/instalacion-pendiente.json" ] && [ -s "$S/mac/.motor/instalacion.json" ] || fallo "la Mac no cerró el recibo"
conf_buena

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base para la misma versión" 0 bash "$I"
unset MOTOR_ETIQUETA
avanza "misma-version"
en_mac espera "entrega que deja un anterior real" 0 bash "$I"
sha_vieja="$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.23^{commit}")"
sha_nueva="$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")"
ant_antes="$(cat "$S/mac/.motor/etiqueta-anterior")"
rec_antes="$(campo_json "$S/mac/.motor/instalacion.json" 'd["anterior"]["codigo"]')"
echo 'TOKEN=super-secreto-motor09' >"$S/mac/.motor/secretos.env"
en_mac espera "reinstalar la misma versión" 0 bash "$I"
[ "$(cat "$S/mac/.motor/etiqueta-anterior")" = "$ant_antes" ] || fallo "reinstalar pisó etiqueta-anterior"
[ "$(campo_json "$S/mac/.motor/instalacion.json" 'd["anterior"]["codigo"]')" = "$rec_antes" ] || fallo "reinstalar pisó el anterior del recibo"
[ "$rec_antes" = "$sha_vieja" ] || fallo "el anterior real no es la versión previa"
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$sha_nueva" ] || fallo "la misma versión movió el código"
[ ! -e "$S/mac/.motor/instalacion-pendiente.json" ] || fallo "la misma versión dejó un intento pendiente"
grep -q super-secreto-motor09 "$S/mac/.motor/instalacion.json" && fallo "el recibo guardó un secreto"
contiene "dice que ya estaba" "$S/salida" "el anterior no cambia"

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base antes del rechazo" 0 bash "$I"
unset MOTOR_ETIQUETA
git -C "$S/conf-src" rm -q maquinas/mac.toml
git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m sin-mac
git -C "$S/conf-src" push -q -f "$S/conf.git" HEAD:refs/heads/main
en_mac espera "rechazo antes de mutar" '!0' bash "$I"
contiene "no cambió el código" "$S/salida" "No se cambió el código en ninguna máquina"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "el rechazo movió la Mac"
[ ! -e "$S/mac/.motor/instalacion-pendiente.json" ] || fallo "el rechazo dejó un intento pendiente"
conf_buena

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base del parcial" 0 bash "$I"
unset MOTOR_ETIQUETA
avanza "parcial"
sha_vieja="$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.23^{commit}")"
cp "$S/mac/.motor/maquina.toml" "$S/toml-preparcial"
rm -f "$S/checkout-n"
FAKE_GIT_CHECKOUT_FALLA_EN=2 en_mac espera "cae después de anotar el origen" '!0' bash "$I"
unset FAKE_GIT_CHECKOUT_FALLA_EN
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")" ] || fallo "el parcial no llegó a cambiar el código"
[ "$(campo_json "$S/mac/.motor/instalacion-pendiente.json" 'd["anterior"]["codigo"]')" = "$sha_vieja" ] || fallo "el parcial anotó el destino como origen"
[ "$(cat "$S/mac/.motor/etiqueta-anterior")" = "" ] || [ ! -s "$S/mac/.motor/etiqueta-anterior" ] || fallo "el parcial publicó el anterior antes de terminar"
intento_parcial="$(campo_json "$S/mac/.motor/instalacion-pendiente.json" 'd["intento"]')"
printf 'nombre = "destino-parcial"\nconf = "~/motor-conf"\n' >"$S/mac/.motor/maquina.toml"
rm -f "$S/checkout-n"
en_mac espera "el reintento conserva el origen" 0 bash "$I"
cmp -s "$S/toml-preparcial" "$S/mac/.motor/privado/maquina-$intento_parcial.toml" || fallo "el reintento reemplazó el TOML de origen por el parcial"
[ "$(cat "$S/mac/.motor/etiqueta-anterior")" = "$sha_vieja" ] || fallo "el reintento tomó el código a medias como anterior"
[ "$(campo_json "$S/mac/.motor/instalacion.json" 'd["anterior"]["codigo"]')" = "$sha_vieja" ] || fallo "el recibo completo no conserva el origen"
[ ! -e "$S/mac/.motor/instalacion-pendiente.json" ] || fallo "el reintento dejó el intento abierto"
[ "$(campo_json "$S/mac/.motor/instalacion.json" 'd["etapa"]')" = completo ] || fallo "el recibo se cerró sin etapa completo"

reset
git -C "$S/trabajo" checkout -q -B main "$PADRE"
git -C "$S/trabajo" push -q -f origin HEAD:main
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base formato 2" 0 bash "$I"
unset MOTOR_ETIQUETA
git -C "$S/trabajo" checkout -q -B main "$SHA"
git -C "$S/trabajo" push -q origin HEAD:main
en_mac espera "entrega formato 3" 0 bash "$I"
printf '%s\n' '{"version":3,"nombre":"motor09"}' >"$S/mac/.motor/formato"
printf '%s\n' '{"version":1,"generation":"gen","request_id":"req","created_at":"2026-10-25T00:00:00Z","reason":"prueba"}' >"$S/mac/.motor/mantenimiento.json"
echo eventos-viejos >"$S/mac/.motor/motor.db"
echo 'TOKEN=super-secreto-motor09' >"$S/mac/.motor/secretos.env"
cp "$S/mac/.motor/formato" "$S/mac/.formato-antes"
cp "$S/mac/.motor/mantenimiento.json" "$S/mac/.retencion-antes"
cp "$S/mac/.motor/motor.db" "$S/mac/.db-antes"
sha_fmt="$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")"
MOTOR_SHA="$sha_fmt" en_mac espera "la reversa no rebaja formato 3" '!0' bash "$V" --maquina mac
contiene "dice la etapa" "$S/salida" "etapa formato"
contiene "dice el límite del código" "$S/salida" "habla hasta 2"
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$sha_fmt" ] || fallo "la reversa rechazada movió el código"
[ ! -e "$S/mac/.motor/reversa-pendiente.json" ] || fallo "el rechazo de formato dejó una reversa pendiente"
cmp -s "$S/mac/.motor/formato" "$S/mac/.formato-antes" || fallo "la reversa rebajó o borró el marcador"
cmp -s "$S/mac/.motor/mantenimiento.json" "$S/mac/.retencion-antes" || fallo "la reversa tocó la retención"
cmp -s "$S/mac/.motor/motor.db" "$S/mac/.db-antes" || fallo "la reversa borró eventos"
grep -q super-secreto-motor09 "$S/mac/.motor/instalacion.json" && fallo "el recibo de la entrega guardó el secreto"
contiene "sigue retenida" "$S/salida" "sigue retenida"

reset
en_mac espera "base de la pareja" 0 bash "$I"
codigo_b="$(git -C "$S/mac/motor" rev-parse HEAD)"
conf_f1="$(git -C "$S/mac/motor-conf" rev-parse HEAD)"
# El origen efectivo puede ser local, distinto del TOML versionado y del enlace usual.
printf 'nombre = "mac-personalizada"\nconf = "~/motor-conf"\n' >"$S/mac/.motor/maquina.toml"
mkdir -p "$S/mac/conf-local"
ln -sfn "$S/mac/conf-local" "$S/mac/.motor/conf"
cp "$S/mac/.motor/maquina.toml" "$S/toml-f1"
printf 'nombre = "mac-f2"\nconf = "~/motor-conf"\n' >"$S/conf-src/maquinas/mac.toml"
git -C "$S/conf-src" add -A && git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m f2
git -C "$S/conf-src" push -q -f "$S/conf.git" HEAD:refs/heads/main
en_mac espera "el mismo código con otra conf" 0 bash "$I"
[ "$(campo_json "$S/mac/.motor/instalacion.json" 'd["anterior"]["codigo"]')" = "$codigo_b" ] || fallo "cambiar la conf guardó otro código como origen"
[ "$(campo_json "$S/mac/.motor/instalacion.json" 'd["anterior"]["conf"]')" = "$conf_f1" ] || fallo "cambiar la conf no guardó la conf que estaba instalada"
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$codigo_b" ] || fallo "cambiar la conf movió el código"
grep -q mac-f2 "$S/mac/.motor/maquina.toml" || fallo "la conf nueva no quedó en maquina.toml"
printf '%s\n' '{"generation":"sonda"}' >"$S/mac/.motor/mantenimiento.json"
intento_local="$(campo_json "$S/mac/.motor/instalacion.json" 'd["intento"]')"
cp "$S/mac/.motor/privado/maquina-$intento_local.toml" "$S/backup-local"
printf 'origen dañado\n' >"$S/mac/.motor/privado/maquina-$intento_local.toml"
MOTOR_SHA="$codigo_b" en_mac espera "reversa rechaza copia privada dañada" '!0' bash "$V" --maquina mac
grep -q mac-f2 "$S/mac/.motor/maquina.toml" || fallo "el rechazo cambió el TOML efectivo"
[ "$(git -C "$S/mac/motor-conf" rev-parse HEAD)" != "$conf_f1" ] || fallo "el rechazo cambió la conf"
cp "$S/backup-local" "$S/mac/.motor/privado/maquina-$intento_local.toml"
MOTOR_SHA="$codigo_b" en_mac espera "reversa de solo la conf" 0 bash "$V" --maquina mac
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$codigo_b" ] || fallo "la reversa de la conf movió el código"
[ "$(git -C "$S/mac/motor-conf" rev-parse HEAD)" = "$conf_f1" ] || fallo "la reversa no volvió el repo de conf"
cmp -s "$S/toml-f1" "$S/mac/.motor/maquina.toml" || fallo "la reversa dejó el maquina.toml de la conf nueva"
[ "$(readlink "$S/mac/.motor/conf")" = "$S/mac/conf-local" ] || fallo "la reversa no restauró el enlace local anterior"
[ -f "$S/mac/.motor/mantenimiento.json" ] || fallo "la reversa de la conf borró la retención"
[ ! -e "$S/mac/.motor/reversa-pendiente.json" ] || fallo "la reversa completa dejó el pendiente"
conf_buena

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base de la reversa parcial" 0 bash "$I"
unset MOTOR_ETIQUETA
avanza "reversa-parcial"
en_mac espera "entrega para cortar la reversa" 0 bash "$I"
entrega="$(git -C "$S/mac/motor" rev-parse HEAD)"
printf '%s\n' '{"generation":"sonda"}' >"$S/mac/.motor/mantenimiento.json"
cp "$S/mac/.motor/mantenimiento.json" "$S/retencion-parcial"
printf 'nombre = "sucio"\n' >"$S/mac/.motor/maquina.toml"
rm -f "$S/checkout-n"
FAKE_GIT_CHECKOUT_FALLA_EN=2 MOTOR_SHA="$entrega" en_mac espera "la reversa cae a medias" '!0' bash "$V" --maquina mac
unset FAKE_GIT_CHECKOUT_FALLA_EN
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" != "$entrega" ] || fallo "el corte no llegó a mover el código"
[ "$(campo_json "$S/mac/.motor/reversa-pendiente.json" 'd["etapa"]')" = conf ] || fallo "el corte no anotó la etapa"
[ -n "$(campo_json "$S/mac/.motor/reversa-pendiente.json" 'd["causa"]')" ] || fallo "el corte no anotó la causa"
[ "$(campo_json "$S/mac/.motor/reversa-pendiente.json" 'd["entrega"]')" = "$entrega" ] || fallo "el corte anotó otra entrega"
cmp -s "$S/mac/.motor/mantenimiento.json" "$S/retencion-parcial" || fallo "el corte tocó la retención"
rm -f "$S/checkout-n"
MOTOR_SHA="$entrega" en_mac espera "la misma reversa continúa" 0 bash "$V" --maquina mac
grep -q 'se queda' "$S/salida" && fallo "la continuación tomó el código ya movido como motivo para omitirla"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "la continuación no completó la reversa"
cmp -s "$S/conf-src/maquinas/mac.toml" "$S/mac/.motor/maquina.toml" || fallo "la continuación no restauró maquina.toml"
[ "$(readlink "$S/mac/.motor/conf")" = "$S/mac/motor-conf" ] || fallo "la continuación no restauró el enlace"
cmp -s "$S/mac/.motor/mantenimiento.json" "$S/retencion-parcial" || fallo "la continuación tocó la retención"
[ ! -e "$S/mac/.motor/reversa-pendiente.json" ] || fallo "la continuación dejó la reversa abierta"

echo "# bloqueos de la revisión"
reset
en_mac espera "base antes del marcador invalido" 0 bash "$I" --maquina mac
marca="$(git -C "$S/mac/motor" rev-parse HEAD)"
rec="$(campo_json "$S/mac/.motor/instalacion.json" 'd["anterior"]["codigo"]')"
printf '%s\n' '{"version":-2}' >"$S/mac/.motor/formato"
en_mac espera "marcador -2 niega la instalación" '!0' bash "$I" --maquina mac
contiene "el marcador invalido se niega" "$S/salida" "version invalida (-2)"
contiene "no rebaja" "$S/salida" "no rebajo el marcador"
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$marca" ] || fallo "el marcador invalido movió el código"
[ "$(campo_json "$S/mac/.motor/instalacion.json" 'd["anterior"]["codigo"]')" = "$rec" ] || fallo "el marcador invalido reescribió el origen"
[ ! -e "$S/mac/.motor/instalacion-pendiente.json" ] || fallo "el marcador invalido dejó un intento"
[ ! -e "$S/mac/.motor/reversa-pendiente.json" ] || fallo "el marcador invalido escribió una reversa"

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base para la conf ausente" 0 bash "$I" --maquina mac
unset MOTOR_ETIQUETA
avanza "entrega-con-conf-anterior"
en_mac espera "entrega que deja conf anterior" 0 bash "$I" --maquina mac
entrega="$(git -C "$S/mac/motor" rev-parse HEAD)"
cp "$S/mac/.motor/maquina.toml" "$S/toml-antes-conf"
cp "$S/mac/.motor/instalacion.json" "$S/recibo-antes-conf"
mv "$S/mac/motor-conf/.git" "$S/mac/motor-conf.git-fuera"
MOTOR_SHA="$entrega" en_mac espera "reversa sin repo de conf" '!0' bash "$V" --maquina mac
contiene "etapa conf" "$S/salida" "etapa conf"
grep -q 'de vuelta' "$S/salida" && fallo "la reversa sin repo dijo que volvió"
[ "$(git -C "$S/mac/motor" rev-parse HEAD)" = "$entrega" ] || fallo "la reversa sin repo movió el código"
cmp -s "$S/toml-antes-conf" "$S/mac/.motor/maquina.toml" || fallo "la reversa sin repo cambió maquina.toml"
cmp -s "$S/recibo-antes-conf" "$S/mac/.motor/instalacion.json" || fallo "la reversa sin repo borró el recibo"
[ ! -e "$S/mac/.motor/reversa-pendiente.json" ] || fallo "la reversa sin repo dejó una reversa pendiente"
mv "$S/mac/motor-conf.git-fuera" "$S/mac/motor-conf/.git"

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "pc base de la misma pareja" 0 bash "$I" --maquina pc
unset MOTOR_ETIQUETA
avanza "pc-misma-pareja"
en_mac espera "pc sube de pareja" 0 bash "$I" --maquina pc
sha_vieja="$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.23^{commit}")"
[ "$(tr -d '[:space:]' <"$S/pc-etiqueta-anterior")" = "$sha_vieja" ] || fallo "la PC no guardó el código anterior"
ant_pc="$(cat "$S/pc-etiqueta-anterior")"
rec_pc="$(campo_json "$S/pc-home/.motor/instalacion.json" 'd["anterior"]["codigo"]')"
en_mac espera "pc misma pareja no toca el anterior" 0 bash "$I" --maquina pc
[ "$(cat "$S/pc-etiqueta-anterior")" = "$ant_pc" ] || fallo "la misma pareja de la PC reescribió etiqueta-anterior"
[ "$(campo_json "$S/pc-home/.motor/instalacion.json" 'd["anterior"]["codigo"]')" = "$rec_pc" ] || fallo "la misma pareja de la PC reescribió el recibo"
contiene "la PC dice que ya estaba" "$S/salida" "el anterior no cambia"
FAKE_PC_REVPARSE_RC=128 en_mac espera "rev-parse roto no reescribe el origen" '!0' bash "$I" --maquina pc
unset FAKE_PC_REVPARSE_RC
[ "$(cat "$S/pc-etiqueta-anterior")" = "$ant_pc" ] || fallo "un rev-parse roto reescribió etiqueta-anterior"
[ "$(campo_json "$S/pc-home/.motor/instalacion.json" 'd["anterior"]["codigo"]')" = "$rec_pc" ] || fallo "un rev-parse roto reescribió el recibo"
[ "$(cat "$S/pc-tag")" = "$TAG" ] || fallo "un rev-parse roto movió la etiqueta de la PC"
contiene "no anota origen vacío" "$S/salida" "no pude guardar la revision anterior"

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "pc base del marcador ilegible" 0 bash "$I" --maquina pc
unset MOTOR_ETIQUETA
avanza "pc-marcador-ilegible"
en_mac espera "pc entrega con marcador" 0 bash "$I" --maquina pc
printf '%s\n' '{"version":3}' >"$S/pc-home/.motor/formato"
chmod 000 "$S/pc-home/.motor/formato"
entrega="$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")"
MOTOR_SHA="$entrega" en_mac espera "marcador de la PC ilegible niega la reversa" '!0' bash "$V" --maquina pc
grep -q 'de vuelta' "$S/salida" && fallo "el marcador ilegible dijo que la PC volvió"
[ "$(cat "$S/pc-tag")" = "$TAG" ] || fallo "el marcador ilegible movió el código de la PC"
contiene "no lee el marcador" "$S/salida" "no pude leer el marcador"
chmod 644 "$S/pc-home/.motor/formato" || true

reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "pc base del recibo ilegible" 0 bash "$I" --maquina pc
unset MOTOR_ETIQUETA
avanza "pc-recibo-ilegible"
en_mac espera "pc entrega con recibo" 0 bash "$I" --maquina pc
printf '%s\n' '{"anterior":{"codigo":"no-debe-usarse","conf":""}}' >"$S/pc-home/.motor/instalacion-pendiente.json"
chmod 000 "$S/pc-home/.motor/instalacion-pendiente.json"
entrega="$(git -C "$S/origen.git" rev-parse "refs/tags/$TAG^{commit}")"
MOTOR_SHA="$entrega" en_mac espera "recibo pendiente ilegible niega la reversa" '!0' bash "$V" --maquina pc
grep -q 'de vuelta' "$S/salida" && fallo "el recibo ilegible dijo que la PC volvió"
[ "$(cat "$S/pc-tag")" = "$TAG" ] || fallo "el recibo ilegible movió el código de la PC"
contiene "no usa el recibo completo" "$S/salida" "instalacion-pendiente.json"
chmod 644 "$S/pc-home/.motor/instalacion-pendiente.json" || true

reset
en_mac espera "pc base formato 3" 0 bash "$I" --maquina pc
avanza "otro-formato-3"
MOTOR_ETIQUETA=v-fmt3b en_mac espera "pc segunda entrega formato 3" 0 bash "$I" --maquina pc
unset MOTOR_ETIQUETA
printf '%s\n' '{"version":3}' >"$S/pc-home/.motor/formato"
entrega="$(git -C "$S/origen.git" rev-parse "refs/tags/v-fmt3b^{commit}")"
rm -f "$S/pc-fetch-hecho"
FAKE_PC_OBJETO_TRAS_FETCH=1 MOTOR_SHA="$entrega" en_mac espera "la PC trae el objeto antes de leer el formato" 0 bash "$V" --maquina pc
unset FAKE_PC_OBJETO_TRAS_FETCH
[ "$(cat "$S/pc-tag")" = "$TAG" ] || fallo "la PC no volvió al formato 3 que había que traer: $(cat "$S/pc-tag")"
grep -q 'fetch -q --tags --force origin' "$FAKE_LOG" || fallo "la PC decidió el formato sin traer el objeto"
[ -s "$S/pc-home/.motor/formato" ] || fallo "traer el objeto borró el marcador"

termina
