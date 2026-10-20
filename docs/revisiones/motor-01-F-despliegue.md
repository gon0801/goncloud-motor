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

## Desbloqueo (lo hace una persona, una línea)

En `~/.motor-arranque/motor/motor/effects.py`, en `_ya_mergeado`, grabar la
cabeza adoptada como commit del merge externo:

```python
self.cx.store.append(plan.id, block_id, "Merged", {"sha": sha, "commit": sha, "externo": True}, ...)
```

(en vez de `"commit": v.merge_commit`). Así `deploy()` y `prove()` corren el
árbol de la cabeza de la rama, que ya trae el script corregido. El script es
idempotente: la etiqueta `v1.0.0` ya existe y las tres máquinas ya están
instaladas, así que el siguiente despliegue pasa y el checklist
(`ops/huellas.sh`) sale 0.

## Estado real (verificado 2026-10-20)

- `v1.0.0` en GitHub apunta a `4b6cf2d` (el SHA mergeado).
- Las tres máquinas instaladas: `bash ops/huellas.sh` sale 0 (motor 0.1.0
  `4b6cf2d`, conf `988acd0`, doctor ok en Mac, Mini y PC). Lo hizo a mano el
  intento F-implementar-8.
