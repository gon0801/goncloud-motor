"""El check de merge `deepseek-sin-abiertos`.

Lee solo el sticky auténtico de `github-actions[bot]` para el SHA vigente con el
lector que ya usa el motor (`motor.github.comentarios_deepseek`) y las
excepciones del marcador JSON que el motor publica en el cuerpo del PR. La
regla de severidades sale de `motor.encargos.politica`; aquí no se copia.

El check solo frena el merge: el flujo del motor no lo espera ni lo usa como
compuerta. Su resultado se vuelve a calcular cuando cambia el cuerpo del PR o el
sticky.
"""

from __future__ import annotations
from motor.io import leer_json, leer_texto

import argparse
import json
from pathlib import Path

from .comprobacion import hallazgos_deepseek

MARCA_EXCEPCIONES = "motor:excepciones="
MARCA_STICKY = "<!-- ai-review:sticky -->"
AUTOR = "github-actions[bot]"

PASA = "pasa"
FALLA = "falla"
PENDIENTE = "pendiente"

_TIPOS = ("descarte", "fuera_alcance", "omision")


def excepciones_del_cuerpo(cuerpo: str) -> tuple[dict, ...]:
    """El marcador estructurado del cuerpo del PR, sin inventar nada.

    Un marcador ilegible o un tipo desconocido no exceptúan: se descartan y el
    check sigue con lo que el sticky demuestre."""
    texto = str(cuerpo or "")
    inicio = texto.find(MARCA_EXCEPCIONES)
    if inicio < 0:
        return ()
    resto = texto[inicio + len(MARCA_EXCEPCIONES):].lstrip()
    if resto.startswith("-->"):
        resto = resto[3:].lstrip()
    try:
        valor, _ = json.JSONDecoder().raw_decode(resto)
    except ValueError:
        return ()
    if isinstance(valor, dict):
        valor = [valor]
    if not isinstance(valor, list):
        return ()
    salida = []
    for entrada in valor:
        if not isinstance(entrada, dict):
            continue
        sha = str(entrada.get("sha") or "").strip()
        tipo = str(entrada.get("tipo") or "descarte").strip().lower()
        if not sha or tipo not in _TIPOS:
            continue
        salida.append(
            {
                "sha": sha,
                "id": str(entrada.get("id") or "").strip(),
                "tipo": tipo,
                "motivo": str(entrada.get("motivo") or "").strip(),
                "hora": str(entrada.get("hora") or "").strip(),
            }
        )
    return tuple(salida)


def excepciones_para(excepciones, sha: str) -> list[dict]:
    """Solo las excepciones del SHA vigente. Un descarte de otro SHA no vale."""
    actual = str(sha or "").strip()
    return [e for e in excepciones if actual and e.get("sha") == actual]


def es_sticky_autentico(comentario: dict) -> bool:
    """Autor y marca del sticky. El texto por sí solo no acredita origen."""
    return (
        str((comentario or {}).get("autor") or "") == AUTOR
        and MARCA_STICKY in str((comentario or {}).get("texto") or "")
    )


def decidir(comentarios, cuerpo: str, sha: str) -> dict:
    """El veredicto del check para un SHA. `pasa`, `falla` o `pendiente`.

    - sin sticky y con omisión registrada para ese SHA: pasa con la nota;
    - sin sticky y sin omisión: pendiente;
    - con sticky: falla si queda un Critical, High o Medium abierto que no esté
      exceptuado para ese SHA e id.
    """
    objetivo = str(sha or "").strip()
    if not isinstance(comentarios, (list, tuple)):
        comentarios = ()
    excepciones = excepciones_para(excepciones_del_cuerpo(cuerpo), objetivo)
    ids = {e["id"] for e in excepciones if e.get("id")}
    omision = any(e.get("tipo") == "omision" for e in excepciones)
    autenticos = [
        c
        for c in comentarios or ()
        if es_sticky_autentico(c) and str(c.get("sha") or objetivo) == objetivo
    ]
    if not autenticos:
        if omision:
            return {
                "estado": PASA,
                "sha": objetivo,
                "nota": "DeepSeek se omitió después del tope y no hay sticky: pasa con la omisión registrada",
                "abiertos": [],
                "exceptuados": sorted(ids),
            }
        return {
            "estado": PENDIENTE,
            "sha": objetivo,
            "nota": "no hay sticky de DeepSeek para el SHA vigente ni una omisión registrada",
            "abiertos": [],
            "exceptuados": sorted(ids),
        }
    hallazgos = hallazgos_deepseek(autenticos, objetivo)
    abiertos = [
        h
        for h in hallazgos
        if h.gravedad == "bloqueante" and h.id not in ids
    ]
    if abiertos:
        return {
            "estado": FALLA,
            "sha": objetivo,
            "nota": "hay hallazgos bloqueantes abiertos de DeepSeek: "
            + ", ".join(f"{h.id} {h.titulo}"[:160] for h in abiertos),
            "abiertos": [
                {"id": h.id, "titulo": h.titulo, "gravedad": h.gravedad}
                for h in abiertos
            ],
            "exceptuados": sorted(ids),
        }
    return {
        "estado": PASA,
        "sha": objetivo,
        "nota": "no quedan Critical, High ni Medium abiertos sin excepción",
        "abiertos": [],
        "exceptuados": sorted(ids),
    }


def _cuerpo_de(repo: str, numero: int, ruta: Path | None) -> str:
    if ruta is not None:
        return leer_texto(ruta)
    from motor.github import GhGitHub

    return GhGitHub().ver(repo, numero).cuerpo


def _comentarios_de(repo: str, numero: int, sha: str, ruta: Path | None):
    if ruta is not None:
        return leer_json(ruta)
    from motor.github import GhGitHub

    return GhGitHub().comentarios_deepseek(repo, numero, sha)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="deepseek-sin-abiertos")
    ap.add_argument("--sha", required=True)
    ap.add_argument("--repo", default="")
    ap.add_argument("--pr", type=int, default=0)
    ap.add_argument("--cuerpo", type=Path)
    ap.add_argument("--stickies", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cuerpo is not None:
            cuerpo = leer_texto(args.cuerpo)
        elif args.repo and args.pr:
            cuerpo = _cuerpo_de(args.repo, args.pr, None)
        else:
            raise ValueError("falta --cuerpo o --repo y --pr")
        comentarios = _comentarios_de(args.repo, args.pr, args.sha, args.stickies)
        resultado = decidir(comentarios, cuerpo, args.sha)
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(
                {"estado": PENDIENTE, "sha": args.sha, "nota": f"lectura incompleta: {exc}"},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))
    if resultado["estado"] == PASA:
        return 0
    if resultado["estado"] == FALLA:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
