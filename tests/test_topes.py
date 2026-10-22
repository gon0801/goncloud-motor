from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from motor import workspace
from motor.github import GhGitHub


class TopesTest(unittest.TestCase):
    def test_git_con_tope_segun_operacion_y_sin_prompt(self):
        vistos = []

        def fake_run(argv, **kw):
            vistos.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(workspace.subprocess, "run", fake_run):
            workspace.git("clone", "x", "y")
            workspace.git("fetch", "origin")
            workspace.git("push", "origin", "main")
            workspace.git("status")
            workspace.git("-c", "user.name=x", "push", "origin", "main")
        self.assertEqual([k["timeout"] for _, k in vistos], [180, 180, 180, 60, 180])
        for _, kw in vistos:
            self.assertEqual(kw["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_gh_con_tope_y_sin_prompt(self):
        vistos = []

        def fake_run(argv, **kw):
            vistos.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with mock.patch("motor.github.subprocess.run", fake_run):
            GhGitHub(env={})._gh("pr", "view", "1")
        self.assertEqual(vistos[0][1]["timeout"], 60)
        self.assertEqual(vistos[0][1]["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_git_pasarse_del_tope_lanza(self):
        with mock.patch.object(
            workspace.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("git", 60),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                workspace.git("status")


if __name__ == "__main__":
    unittest.main()
