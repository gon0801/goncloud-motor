#!/usr/bin/env bash
# ops/canario/verifica.sh con estados falsos: homes de mentira con el registro real del motor y un ssh
# falso que corre ahí el resumen. Sale 0 solo con todo en regla en las dos máquinas; ≠0 y diciendo
# qué falta cuando algo no está (sin PR, sin merge, corte tratado como falla, aviso sin llegar...).
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../tests/lib-prueba.sh"

V="$RAIZ/ops/canario/verifica.sh"
export MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc
export MOTOR_CANARIO_SONDEO_S=0 MOTOR_CANARIO_TOPE_S=2
export PYTHONPATH="$RAIZ"   # python -I lo ignora: los scripts lo agregan a sys.path a mano

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
if [ "$host" = gwpc ]; then
  resto="${*#*python - }"
  resto="${resto//%USERPROFILE%\\.motor/$S/pc/.motor}"
  eval "set -- $resto"
  exec python3 -I - "$@"
fi
HOME="$S/mini" bash -c "$*"
FAKE
chmod +x "$S/bin/ssh"

# Un home con el plan canario en el estado que se pide. Lo que no se pide, queda en regla.
#   sin_pr=B  sin_merge=C  sin_prueba=A  sin_humano=1  sin_terminado=1  sin_corte=1  corte_falla=1
#   ya_en_base=B (Merged sin PR)  sin_aviso=entregado_B  pendiente=1
home() { # home <carpeta> [clave=valor...]
  local h=$1; shift
  rm -rf "$h"; mkdir -p "$h"
  env "$@" python3 -I - "$h" <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["PYTHONPATH"])
from motor.store import Store

h = Path(sys.argv[1])
st = Store(h / "motor.db")
P = "canario"
e = os.environ.get
for b in "ABC":
    if e("sin_pr") != b and e("ya_en_base") != b:
        st.append(P, b, "PrOpened", {"numero": 1}, ukey=f"pr:{b}")
    if e("sin_merge") != b:
        # ya_en_base=B: un Merged sin PR porque lo entregado ya estaba en la rama principal
        base = e("ya_en_base") == b
        st.append(P, b, "Merged", {"commit": "abc", **({"ya_en_base": True} if base else {})}, ukey=f"merge:{b}")
    st.append(P, b, "Proved", {"sha": "abc", "ok": e("sin_prueba") != b}, ukey=f"proved:{b}")
if e("sin_humano") != "1":
    st.append(P, "H", "HumanDone", {"por": "sonda"}, ukey="humano")
if e("sin_terminado") != "1":
    st.append(P, "", "NoticeQueued", {"key": f"terminado:{P}", "texto": "x"}, ukey="n:terminado")
if e("sin_corte") != "1":
    st.append(P, "A", "AttemptStarted", {"n": 1, "role": "implementar", "dir": "/t/1"}, ukey="s1")
    st.append(P, "A", "AttemptEnded", {"n": 1, "role": "implementar", "outcome": {"kind": "corte"}}, ukey="e1")
    if e("corte_falla") == "1":
        st.append(P, "", "Cooldown", {"cuenta": "anthropic", "hasta": 1}, ukey="cooldown:/t/1")
env = h / "avisos" / "enviados"
env.mkdir(parents=True)
(h / "avisos" / "pendientes").mkdir()
nombres = [f"canario_entregado_{b}" for b in "ABC"] + ["canario_terminado_canario", "canario_retome_boot1"]
for n in nombres:
    if e("sin_aviso") != "todos" and n.split("canario_", 1)[1] != e("sin_aviso"):
        (env / f"1000-{n}.txt").write_text("En la Mini · canario:\nListo", encoding="utf-8")
if e("pendiente") == "1":
    (h / "avisos" / "pendientes" / "2000-canario_avance_1.txt").write_text("x", encoding="utf-8")
PY
}

bien() { home "$S/mini/.motor"; home "$S/pc/.motor"; unset FAKE_CAE; }

bien; espera "todo en regla en las dos" 0 bash "$V"
contiene "lo dice" "$S/salida" "aprobada"

for maq in mini pc; do
  bien; home "$S/$maq/.motor" ya_en_base=B
  espera "$maq con un Merged ya_en_base y sin PR vale" 0 bash "$V"
  if grep -qF "un PR y un merge por bloque" "$S/salida"; then
    fallo "$maq: el mensaje de aprobación afirma un PR por bloque y hubo ya_en_base"
  fi
  bien; home "$S/$maq/.motor" ya_en_base=B sin_merge=B
  espera "$maq sin PR ni merge no vale" '!0' bash "$V"
done

# ya_en_base en LAS DOS máquinas: ninguna hizo el PR de verdad -> no aprueba.
bien; home "$S/mini/.motor" ya_en_base=B; home "$S/pc/.motor" ya_en_base=B
espera "ya_en_base en las dos máquinas no vale" '!0' bash "$V"
contiene "dice que no hubo PR real" "$S/salida" "no tiene PR en ninguna máquina"

casos=(
  "sin_pr=B|no tiene PR"
  "sin_merge=C|no tiene merge"
  "sin_prueba=A|no pasó su prueba real"
  "sin_humano=1|paso humano"
  "sin_terminado=1|no ha terminado"
  "sin_corte=1|ningún corte"
  "corte_falla=1|se trató como falla"
  "sin_aviso=entregado_B|no llegó el aviso «entregado_B»"
  "sin_aviso=terminado_canario|no llegó el aviso"
  "sin_aviso=retome_boot1|«retome»"
  "pendiente=1|sin enviarse"
)
for maq in mini pc; do
  for c in "${casos[@]}"; do
    bien; home "$S/$maq/.motor" "${c%%|*}"
    espera "$maq con ${c%%|*}" '!0' bash "$V"
    contiene "dice qué falta" "$S/salida" "${c##*|}"
  done
done

# Avisos de OTRO plan de la máquina no cuentan: sin los del canario, no llegó ninguno.
for maq in mini pc; do
  bien; home "$S/$maq/.motor" sin_aviso=todos
  for n in entregado_A entregado_B entregado_C terminado_viejo retome_boot0; do
    touch "$S/$maq/.motor/avisos/enviados/1000-viejo_$n.txt"
  done
  espera "$maq con avisos solo de otro plan" '!0' bash "$V"
  contiene "dice que no llegó" "$S/salida" "no llegó el aviso"
done

bien; rm -rf "$S/mini/.motor"; mkdir -p "$S/mini/.motor"
espera "la Mini sin plan" '!0' bash "$V"
contiene "dice cuál" "$S/salida" "claw (Mini): el motor no tiene el plan"

bien; FAKE_CAE="gwpc" espera "la PC no contesta (reiniciándose)" '!0' bash "$V"
contiene "dice cuál" "$S/salida" "Hermi (PC): no contestó"


termina
