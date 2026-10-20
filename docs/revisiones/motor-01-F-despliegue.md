# motor-01 · bloque F · bucle de despliegue (diagnóstico y desbloqueo)

Síntoma: el paso de despliegue del bloque F falla siempre con
`ops/instala-todo.sh: salida 3` y `rollback: salida 0`, una ronda tras otra.
No es un bug del script de la rama: es la combinación del merge adoptado y el
`MOTOR_RAIZ` que exporta el daemon.

## Mecánica del bucle

1. El PR #6 quedó mergeado (merge commit `4b6cf2d`). En cada ronda,
   `poll_checks` llama a `_ya_mergeado` (en el motor instalado,
   `~/.motor-arranque/motor/motor/effects.py`), ve el PR mergeado y adopta
   `Merged{sha: <cabeza nueva de la rama>, commit: 4b6cf2d, externo: True}`.
2. `deploy()` copia el repo en `bv.merged[-1]["commit"]`, o sea `4b6cf2d`:
   ejecuta **siempre el `ops/instala-todo.sh` viejo** de ese commit, nunca el
   corregido de la rama. Lo mismo `prove()`.
3. El script viejo hace `raiz="${MOTOR_RAIZ:-...}"` y
   `git -C "$raiz" fetch -q --tags origin`. El daemon exporta
   `MOTOR_RAIZ=~/.motor-arranque/motor`, que es la instalación del motor y
   **no tiene `.git`**: git sale 128 y el script sale 3. Determinista.
4. Aunque el paso 1 del script pasara, el script viejo clona en la Mini por
   `github.com` directo y la Mini no tiene llave para ese host (el arreglo con
   el alias `github-claw` solo existe en la rama): fallaría igual.

## Por qué no se destraba desde la rama

- Pushear a `main` o mergear el PR está prohibido, y el merge commit adoptado
  (`4b6cf2d`) es historia fija: `gh pr view 6` siempre lo devuelve.
- El `ukey` `deployed:<plan>:F:<sha>` hace imposible re-anotar un despliegue,
  y con `m ∈ deployed` el estado queda en ARREGLAR: cada cabeza nueva repite
  adoptar-merge-viejo → desplegar-script-viejo → salida 3.

## Desbloqueo (lo hace una persona, en el motor instalado)

Dejar `_ya_mergeado` como está (`"commit": v.merge_commit`: el despliegue
tiene que seguir corriendo el árbol mergeado, nunca la cabeza sin mergear de
la rama — grabar `"commit": sha` haría que `deploy()` copie la cabeza, que no
es ancestro de `origin/main`, y el script salga 3 con "no está mergeado", o
peor, despliegue un árbol sin mergear si se quita la guarda).

El cambio va en `deploy()` y `prove()` de
`~/.motor-arranque/motor/motor/effects.py`: que `MOTOR_RAIZ` apunte a la copia
del árbol mergeado (o que no se exporte). `ws.copia()` es un
`git worktree add --detach`, o sea un árbol con `.git`:

```python
env = self.env_base(dict(rc.prueba_env))
env["MOTOR_RAIZ"] = str(w)   # la copia sí tiene .git; la instalación del motor no
```

Así el script de `4b6cf2d` hace `git fetch` en un árbol con `.git`, su HEAD es
`4b6cf2d` (que está en `origin/main`) y `v1.0.0` ya apunta ahí: el paso 1 pasa
y respeta la etiqueta. Probado con el repro de abajo: salida 0.

Si en producción hace falta el script corregido de la rama (el alias
`github-claw` para la Mini, los reintentos), tiene que entrar a `main` en un
PR nuevo que mergee el motor. No se arregla apuntando el despliegue a la
cabeza.

Repro (ssh falso para Mini y PC; la Mac corre de verdad porque es local):

```sh
cd <checkout de goncloud-motor> && git fetch -q origin && T=$(mktemp -d) &&
git worktree add -q --detach "$T/w" 4b6cf2d && mkdir "$T/bin" &&
printf '#!/bin/sh\nexit 0\n' >"$T/bin/ssh" && chmod +x "$T/bin/ssh" &&
(cd "$T/w" && GIT_SSH_COMMAND=/usr/bin/ssh MOTOR_RAIZ="$T/w" PATH="$T/bin:$PATH" \
  bash ops/instala-todo.sh; echo "salida $?")
git worktree remove --force "$T/w"
# -> "etiqueta v1.0.0 ya existe en origin: se respeta", ..., "salida 0"
```

Verificado 2026-10-20: con `MOTOR_RAIZ` apuntando al worktree el script sale 0
(con `MOTOR_RAIZ=~/.motor-arranque/motor`, sin `.git`, sale 3 en el fetch, que
es el bucle observado).

## Estado real (verificado 2026-10-20)

- `v1.0.0` en GitHub apunta a `4b6cf2d` (el SHA mergeado).
- Las tres máquinas instaladas: `bash ops/huellas.sh` sale 0 (motor 0.1.0
  `4b6cf2d`, conf `988acd0`, doctor ok en Mac, Mini y PC). Lo hizo a mano el
  intento F-implementar-8.
