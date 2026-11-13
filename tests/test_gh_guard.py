"""El `gh` de los agentes no mergea, cierra, marca listo ni revisa PRs, por ninguna de sus formas."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIM = Path(__file__).resolve().parents[1] / "motor" / "shims" / "gh"

MUTACION = 'query=mutation{%s(input:{pullRequestId:"X"}){clientMutationId}}'

BLOQUEADOS = (
    ["pr", "merge", "5"],
    ["pr", "-R", "o/r", "merge", "5"],
    ["pr", "--repo", "o/r", "merge", "5"],
    ["pr", "--repo=o/r", "merge", "5"],
    ["pr", "-Ro/r", "merge", "5"],
    ["pr", "-R", "o/r", "close", "5"],
    ["pr", "--repo", "o/r", "ready", "5"],
    ["pr", "-R", "o/r", "review", "5", "--approve"],
    ["api", "-X", "PUT", "repos/o/r/pulls/5/merge"],
    ["api", "--method=PUT", "repos/{owner}/{repo}/pulls/5/merge"],
    ["api", "-XPUT", "/repos/o/r/pulls/5/merge", "-f", "merge_method=squash"],
    ["api", "-X", "PATCH", "repos/o/r/pulls/5", "-f", "state=closed"],
    ["api", "--method", "PATCH", "repos/o/r/pulls/5", "--raw-field=state=closed"],
    ["api", "repos/o/r/pulls/5/reviews", "-f", "event=APPROVE"],
    ["api", "--paginate", "repos/o/r/pulls/5/reviews/9/events", "-F", "event=APPROVE"],
    ["api", "graphql", "-f", MUTACION % "mergePullRequest"],
    ["api", "graphql", "-f", MUTACION % "enablePullRequestAutoMerge"],
    ["api", "graphql", "-f", MUTACION % "closePullRequest"],
    ["api", "graphql", "-f", MUTACION % "markPullRequestReadyForReview"],
    ["api", "graphql", "-F", MUTACION % "addPullRequestReview"],
    ["api", "graphql", "--raw-field", MUTACION % "submitPullRequestReview"],
)

PERMITIDOS = (
    ["pr", "view", "5"],
    ["pr", "-R", "o/r", "view", "5"],
    ["pr", "list", "--search", "merge"],
    ["pr", "list", "--state", "closed"],
    ["pr", "comment", "5", "--body", "listo para merge y review"],
    ["pr", "create", "--title", "close #3", "--body", "ready"],
    ["api", "repos/o/r/pulls/5"],
    ["api", "repos/o/r/pulls/5/merge"],
    ["api", "repos/o/r/pulls/5/reviews"],
    ["api", "-X", "PATCH", "repos/o/r/pulls/5", "-f", "title=merge"],
    ["api", "repos/o/r/issues/5/comments", "-f", "body=mergePullRequest"],
    [
        "api",
        "graphql",
        "-f",
        'query={repository(owner:"o",name:"r"){pullRequest(number:5){mergeable}}}',
    ],
)


class TestGhDeLosAgentes(unittest.TestCase):
    def test_las_cuatro_acciones_se_bloquean_por_toda_forma_y_lo_demas_pasa(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "gh"
            real.write_text('#!/bin/sh\necho real "$@"\n', encoding="utf-8")
            real.chmod(0o755)
            env = {
                **os.environ,
                "PATH": os.pathsep.join([str(SHIM.parent), tmp, "/usr/bin", "/bin"]),
            }

            def corre(args: list[str]) -> subprocess.CompletedProcess[str]:
                return subprocess.run([str(SHIM), *args], env=env, capture_output=True, text=True)

            fallos = []
            for args in BLOQUEADOS:
                r = corre(args)
                if (r.returncode, r.stdout) != (3, "") or "solo el motor mergea" not in r.stderr:
                    fallos.append(("pasó y debía bloquearse", args))
            for args in PERMITIDOS:
                r = corre(args)
                if (r.returncode, r.stdout.strip()) != (0, " ".join(["real", *args])):
                    fallos.append(("se bloqueó y debía pasar", args))
            self.assertEqual(fallos, [])


if __name__ == "__main__":
    unittest.main()
