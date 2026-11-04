"""Traer la base vigente a la rama de un encargo desde el host.

Se dispara a mano, con `motor traer-main <encargo> --base <registro>`, cuando
el encargo no tiene un agente en curso: al empezar una fase nueva o antes de
pedir una entrega que lleva horas esperando. No corre dentro de la jaula ni
abre otro turno. Con mezcla limpia publica la rama y deja una nota; con
conflicto deja la mezcla en el espacio y devuelve el encargo al responsable
para que la resuelva.
"""

from __future__ import annotations

from pathlib import Path

from .ciclo import (
    _FUERA_DEL_PRODUCTO,
    _devolver,
    _espacio,
    _git,
    _rama_fase,
)
from .frontera import turno

_MOTOR = ("-c", "user.name=motor de encargos", "-c", "user.email=motor@localhost")
_CERRADOS = ("terminado", "cancelado", "deteniendo", "detenido")


def _sha(espacio: Path, ref: str = "HEAD") -> str:
    return _git(espacio, "rev-parse", "--verify", "--quiet", ref).stdout.strip()


def _conflictos(espacio: Path) -> list[str]:
    salida = _git(espacio, "diff", "--name-only", "--diff-filter=U").stdout
    return [linea.strip() for linea in (salida or "").splitlines() if linea.strip()]


def _nota(registro, enc, texto: str, ukey: str, hora: str) -> None:
    registro.anotar(
        enc.id,
        {"kind": "procedimiento", "data": {"nota": texto, "hora": hora}},
        ukey,
    )


def _resultado(enc, base: str, rama: str, estado: str, **datos) -> dict:
    salida = {"encargo": enc.id, "estado": estado, "base": base, "rama": rama}
    salida.update(datos)
    return salida


def _commit_pendiente(enc, espacio: Path, base: str) -> str:
    """El producto sin commit entra antes de mezclar: el choque se ve sobre el
    trabajo real y no se pierde nada si la mezcla falla."""
    if _git(espacio, "ls-files", "--", ".interno").stdout.strip():
        _git(espacio, "rm", "-r", "--cached", "--quiet", ".interno")
    if _git(espacio, "add", "-A", "--", ".", *_FUERA_DEL_PRODUCTO).returncode != 0:
        return "no se pudo preparar el producto para la mezcla"
    pendiente = _git(espacio, "diff", "--cached", "--quiet")
    if pendiente.returncode not in (0, 1):
        return f"no se pudo revisar el índice: {pendiente.stderr.strip()[-300:]}"
    if pendiente.returncode == 0:
        return ""
    clave = str(getattr(enc, "clave", "") or enc.id)
    r = _git(
        espacio, *_MOTOR, "commit", "--quiet", "-m",
        f"{clave}: trabajo pendiente antes de traer {base}",
    )
    if r.returncode != 0:
        return f"no se pudo commitear el trabajo pendiente: {r.stderr.strip()[-300:]}"
    return ""


def _publicar(espacio: Path, remoto: str, rama: str) -> tuple[bool, str]:
    r = _git(espacio, "push", "--quiet", remoto, f"HEAD:refs/heads/{rama}")
    if r.returncode != 0:
        return False, f"no se pudo publicar la rama {rama}: {r.stderr.strip()[-300:]}"
    return True, ""


def _remoto_sha(espacio: Path, remoto: str, rama: str) -> str:
    salida = _git(
        espacio, "ls-remote", "--heads", remoto, f"refs/heads/{rama}"
    ).stdout.split()
    return salida[0] if salida else ""


def _conflicto(
    registro, enc, base: str, base_sha: str, rama: str, hora: str
) -> dict:
    espacio = _espacio(registro.base, enc.id)
    archivos = _conflictos(espacio)
    if archivos:
        detalle = ", ".join(archivos)
        nota = (
            f"traer {base}: conflicto en {detalle}. Resuelve los conflictos en el "
            f"espacio, 'git add' y 'git commit'; la mezcla no se publicó en {rama}."
        )
    else:
        nota = (
            f"traer {base}: la mezcla está en curso. Revisa el estado con 'git status', "
            f"resuelve lo que falte, 'git add' y 'git commit'; no se publicó en {rama}."
        )
    _nota(registro, enc, nota, f"traer-main:{enc.id}:{base}:{_sha(espacio)}:conflicto", hora)
    if enc.estado not in _CERRADOS:
        _devolver(
            registro, enc, "integrar_main", nota,
            "resolver el conflicto con " + base + " conservando el trabajo propio; "
            "lo aprobado que no cambió se conserva",
            hora, f"traer-main-{base}-{_sha(espacio, 'MERGE_HEAD')[:12]}",
        )
    return _resultado(
        enc, base, rama, "conflicto", sha=_sha(espacio), base_sha=base_sha,
        archivos=archivos, motivo=nota, conflicto=True,
    )


def traer_main(
    registro, encargo: str, hora: str, contra: str = "", remoto: str = "origin"
) -> dict:
    """Toma el turno del supervisor: la comprobación de agente y la mezcla son
    una sola operación, y el lanzador no puede abrir un turno en medio."""
    with turno(registro.base):
        return _traer_main(registro, encargo, hora, contra, remoto)


def _traer_main(
    registro, encargo: str, hora: str, contra: str, remoto: str
) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"estado": "error", "motivo": f"no existe el encargo {encargo}"}
    base = str(contra or enc.entrega.base or "main").strip()
    rama = str(_rama_fase(enc) or "").strip()
    if not rama:
        return _resultado(enc, base, rama, "error", motivo="el encargo no tiene rama de entrega")
    if enc.estado not in ("recibido", "ejecutando"):
        return _resultado(
            enc, base, rama, "error",
            motivo=f"el encargo está {enc.estado}; traer {base} corre con el trabajo activo",
        )
    if getattr(enc, "proc", None) is not None or getattr(enc, "reserva", False):
        return _resultado(
            enc, base, rama, "error",
            motivo="hay un agente en curso; traer la base corre sin agente",
        )
    espacio = _espacio(registro.base, enc.id)
    if not (espacio / ".git").exists():
        return _resultado(
            enc, base, rama, "error",
            motivo="el espacio del encargo todavía no es un clon git",
        )
    actual = _git(espacio, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if actual != rama:
        return _resultado(
            enc, base, rama, "error",
            motivo=(
                f"el espacio está en la rama {actual or '(sin rama)'}; "
                f"la fase {getattr(enc, 'fase_activa', '') or '(unica)'} publica en {rama}"
            ),
        )
    if _git(espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0:
        return _conflicto(registro, enc, base, _sha(espacio, "MERGE_HEAD"), rama, hora)

    causa = _commit_pendiente(enc, espacio, base)
    if causa:
        return _resultado(enc, base, rama, "error", motivo=causa)

    r = _git(espacio, "fetch", "--quiet", remoto)
    if r.returncode != 0:
        return _resultado(
            enc, base, rama, "error",
            motivo=f"no se pudo consultar {remoto}: {r.stderr.strip()[-300:]}",
        )
    referencia = f"refs/remotes/{remoto}/{base}"
    if _git(espacio, "rev-parse", "--verify", "--quiet", referencia).returncode != 0:
        r = _git(espacio, "fetch", "--quiet", remoto, f"refs/heads/{base}")
        if r.returncode != 0:
            return _resultado(
                enc, base, rama, "error",
                motivo=f"{remoto} no tiene la rama {base}: {r.stderr.strip()[-300:]}",
            )
        referencia = "FETCH_HEAD"
    base_sha = _sha(espacio, referencia)
    sha = _sha(espacio)

    if _git(espacio, "merge-base", "--is-ancestor", referencia, "HEAD").returncode == 0:
        if _remoto_sha(espacio, remoto, rama) == sha:
            nota = f"traer {base}: ya estaba al día ({base_sha[:12]})"
            _nota(registro, enc, nota, f"traer-main:{enc.id}:{base}:{sha}:aldia", hora)
            return _resultado(
                enc, base, rama, "al-dia", sha=sha, base_sha=base_sha, archivos=[]
            )
        ok, causa = _publicar(espacio, remoto, rama)
        if not ok:
            return _resultado(
                enc, base, rama, "error", motivo=causa, sha=sha, base_sha=base_sha
            )
        nota = f"traer {base}: ya estaba al día y se publicó {sha[:12]} en {rama}"
        _nota(registro, enc, nota, f"traer-main:{enc.id}:{base}:{sha}:publicado", hora)
        return _resultado(
            enc, base, rama, "publicado", sha=sha, base_sha=base_sha, archivos=[]
        )

    r = _git(
        espacio, *_MOTOR, "merge", "--no-ff", "--no-edit", "--quiet",
        "-m", f"traer {base} ({base_sha[:12]})", referencia,
    )
    if r.returncode != 0:
        archivos = _conflictos(espacio)
        if not archivos:
            _git(espacio, "merge", "--abort")
            return _resultado(
                enc, base, rama, "error",
                motivo=f"no se pudo mezclar {base}: {r.stderr.strip()[-300:]}",
            )
        return _conflicto(registro, enc, base, base_sha, rama, hora)

    sha = _sha(espacio)
    ok, causa = _publicar(espacio, remoto, rama)
    if not ok:
        return _resultado(
            enc, base, rama, "error",
            motivo=f"la mezcla quedó local pero {causa}", sha=sha, base_sha=base_sha,
        )
    nota = (
        f"traer {base} ({base_sha[:12]}): mezcla limpia en {sha[:12]}, "
        f"publicada en {rama}"
    )
    _nota(registro, enc, nota, f"traer-main:{enc.id}:{base}:{sha}", hora)
    return _resultado(
        enc, base, rama, "publicado", sha=sha, base_sha=base_sha, archivos=[]
    )
