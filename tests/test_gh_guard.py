"""El `gh` de los agentes no abre, mergea, cierra, marca listo ni revisa PRs, ni integra una rama sin
PR, por ninguna de sus formas."""

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
    ["pr", "create", "--title", "t", "--body", "b"],
    ["pr", "--repo", "o/r", "create", "--fill"],
    ["api", "-X", "PUT", "repos/o/r/pulls/5/merge"],
    ["api", "--method=PUT", "repos/{owner}/{repo}/pulls/5/merge"],
    ["api", "-XPUT", "/repos/o/r/pulls/5/merge", "-f", "merge_method=squash"],
    ["api", "-X", "PATCH", "repos/o/r/pulls/5", "-f", "state=closed"],
    ["api", "--method", "PATCH", "repos/o/r/pulls/5", "--raw-field=state=closed"],
    ["api", "repos/o/r/pulls/5/reviews", "-f", "event=APPROVE"],
    ["api", "--paginate", "repos/o/r/pulls/5/reviews/9/events", "-F", "event=APPROVE"],
    ["api", "repos/o/r/pulls", "-f", "head=f", "-f", "base=main"],
    ["api", "-X", "POST", "/repos/{owner}/{repo}/pulls", "--input", "pr.json"],
    ["api", "graphql", "-f", MUTACION % "mergePullRequest"],
    ["api", "graphql", "-f", MUTACION % "enablePullRequestAutoMerge"],
    ["api", "graphql", "-f", MUTACION % "closePullRequest"],
    ["api", "graphql", "-f", MUTACION % "markPullRequestReadyForReview"],
    ["api", "graphql", "-F", MUTACION % "addPullRequestReview"],
    ["api", "graphql", "--raw-field", MUTACION % "submitPullRequestReview"],
    ["api", "graphql", "-f", MUTACION % "createPullRequest"],
    ["pr", "new", "--fill"],
    ["api", "-X", "PUT", "repos/o/r/pulls/5/merge?merge_method=squash"],
    ["api", "repos/o/r/merges", "-f", "base=main", "-f", "head=encargo/x"],
    ["api", "-X", "PATCH", "repos/o/r/git/refs/heads/main", "-f", "sha=abc"],
    ["api", "--method=PATCH", "/repos/o/r/git/refs/heads/release/1", "-F", "force=true"],
    ["api", "repos/o/r/git/refs", "-f", "ref=refs/heads/main", "-f", "sha=abc"],
    ["api", "graphql", "-f", MUTACION % "mergeBranch"],
    ["api", "graphql", "-f", MUTACION % "updateRef"],
    ["api", "graphql", "-f", MUTACION % "updateRefs"],
    ["api", "graphql", "-f", MUTACION % "createRef"],
    ["api", "/graphql", "-f", MUTACION % "mergePullRequest"],
    ["api", "graphql", "-F", "query=@consulta.graphql"],
    ["api", "graphql", "--field=query=@-"],
    ["api", "graphql", "--input", "cuerpo.json"],
    ["api", "graphql", "--input=-"],
    ["api", "-X", "PATCH", "repos/o/r/pulls/5", "--input", "cuerpo.json"],
    ["alias", "set", "m", "pr merge"],
    ["alias", "set", "--clobber", "m", "pr merge"],
    ["alias", "import", "alias.yml"],
)

PERMITIDOS = (
    ["pr", "view", "5"],
    ["pr", "-R", "o/r", "view", "5"],
    ["pr", "list", "--search", "merge"],
    ["pr", "list", "--state", "closed"],
    ["pr", "comment", "5", "--body", "listo para merge y review"],
    ["issue", "create", "--title", "close #3", "--body", "ready"],
    ["api", "repos/o/r/pulls"],
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
    ["api", "repos/o/r/pulls/5/merge?x=1"],
    ["api", "repos/o/r/git/refs"],
    ["api", "repos/o/r/git/refs/heads/main"],
    ["api", "-X", "GET", "repos/o/r/git/refs", "-f", "per_page=5"],
    ["api", "repos/o/r/git/matching-refs/heads/encargo"],
    ["api", "graphql", "-F", "n=5", "-f", "query=query($n:Int!){viewer{login}}"],
    ["api", "repos/o/r/issues/5/comments", "-F", "body=@nota.md"],
    ["alias", "list"],
)


class TestGhDeLosAgentes(unittest.TestCase):
    def test_integrar_abrir_o_revisar_se_bloquea_por_toda_forma_y_lo_demas_pasa(self):
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
