"""Tests for tools/privacy_check.py using a throw-away Git repository and a fictional policy.

Only invented marker strings appear here; the real policy is private and lives outside
the repository.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "privacy_check.py"
HAS_GIT = shutil.which("git") is not None


def run(args, cwd):
    return subprocess.run([sys.executable, "-I", str(TOOL), *args], cwd=cwd, capture_output=True, text=True)


@unittest.skipUnless(HAS_GIT, "git is required")
class PrivacyCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="relcheck-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Fixture Author")
        self._git("config", "user.email", "fixture" + "@" + "example.invalid")
        (self.repo / "tools").mkdir()
        (self.repo / "tools" / "release_allowlist.txt").write_text("README.md\nsrc/*.py\ntools/release_allowlist.txt\n")
        self.policy = self.tmp / "policy.txt"
        self.policy.write_text("# fictional policy for tests\nZORBLAT-MARKER\nfictional-corp.example\nre:quux[0-9]{3}\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True)

    def _write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_fails_closed_without_policy(self):
        self._write("README.md", "clean readme\n")
        self._git("add", "README.md")
        res = run(["--staged"], self.repo)
        self.assertEqual(res.returncode, 2, res.stderr)

    def test_clean_staged_content_passes(self):
        self._write("README.md", "A neutral readme.\n")
        self._write("src/a.py", "print('hello')\n")
        self._git("add", "README.md", "src/a.py")
        res = run(["--staged", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_staged_marker_is_caught_even_if_removed_from_worktree(self):
        p = self._write("src/a.py", "x = 'zorblat-marker'\n")
        self._git("add", "src/a.py")
        p.write_text("x = 'clean'\n")  # working tree is clean, the index is not
        res = run(["--staged", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 1)
        self.assertIn("P-term-01", res.stdout)
        self.assertNotIn("zorblat", res.stdout.lower())  # the term itself is never echoed

    def test_regex_rule_and_generic_rules(self):
        home = "/" + "Users" + "/someone/project"
        mail = "a" + "@" + "b.example"
        self._write("README.md", f"see quux123 and {home} and mail me at {mail}\n")
        self._git("add", "README.md")
        res = run(["--staged", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 1)
        for rule in ("P-re-01", "G05-home-directory-path", "G06-email-address"):
            self.assertIn(rule, res.stdout)

    def test_path_outside_allowlist_and_binary_are_rejected(self):
        self._write("notes/secret.txt", "harmless text\n")
        (self.repo / "image.bin").write_bytes(b"\x00\x01\x02")
        self._git("add", "notes/secret.txt", "image.bin")
        res = run(["--staged", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 1)
        self.assertIn("A01-path-not-in-allowlist", res.stdout)
        self.assertIn("F03-binary-content", res.stdout)

    def test_history_scan_covers_commit_message_and_old_blobs(self):
        self._write("README.md", "first version with ZORBLAT-MARKER inside\n")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "initial import")
        self._write("README.md", "clean now\n")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "cleanup fictional-corp.example mention in message")
        res = run(["--history", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 1)
        self.assertIn(":README.md", res.stdout)
        self.assertIn(":message", res.stdout)

    def test_commit_body_is_scanned_with_generic_rules_but_author_email_is_not(self):
        self._write("README.md", "clean\n")
        self._git("add", "README.md")
        secret = "api_key = " + "fictionalkey_" + "0123456789abcdef"
        self._git("commit", "-q", "-m", "initial", "-m", secret)
        res = run(["--history", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("G04-secret-assignment", res.stdout)
        self.assertIn(":message", res.stdout)
        self.assertNotIn("G06-email-address", res.stdout)  # the author field is an e-mail by nature

    def test_underscore_and_prefixed_token_formats_are_caught(self):
        tokens = ["ghp_" + "A" * 36, "github_pat_" + "B" * 40, "sk_live_" + "C" * 24, "sk-" + "D" * 24,
                  "xoxb-" + "1234-" + "E" * 24, "glpat-" + "F" * 20]
        for i, t in enumerate(tokens):  # one file per format so every format must be caught on its own
            self._write(f"src/t{i}.py", f"value = '{t}'\n")
            self._git("add", f"src/t{i}.py")
        res = run(["--staged", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.stdout.count("G03-api-token"), len(tokens), res.stdout)
        for t in tokens:
            self.assertNotIn(t, res.stdout)  # the value is never echoed

    def test_private_terms_in_file_paths_are_caught(self):
        self._write("src/fictional-corp.example_notes.py", "value = 1\n")
        self._git("add", "src/fictional-corp.example_notes.py")
        res = run(["--staged", "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("P-term-02", res.stdout)
        self.assertNotIn("fictional-corp", res.stdout.replace("fictional-corp.example_notes.py", ""))  # only the location is echoed

    def test_worktree_files_mode(self):
        p = self._write("src/ok.py", "value = 1\n")
        res = run(["--files", str(p), "--policy", str(self.policy)], self.repo)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
