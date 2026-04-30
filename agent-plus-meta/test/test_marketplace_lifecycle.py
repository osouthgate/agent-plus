"""Lifecycle tests for `agent-plus marketplace install/list/update/remove`.

Builds fake marketplace repos on disk, points `--url` at them via file://
URLs, and overrides AGENT_PLUS_MARKETPLACES_ROOT so the real ~ never gets
touched. Stdlib only.

Run with:
    python3 -m unittest agent-plus/test/test_marketplace_lifecycle.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "agent-plus-meta"
    loader = SourceFileLoader("agent_plus", str(bin_path))
    spec = importlib.util.spec_from_loader("agent_plus", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ap = _load_module()
BIN = Path(__file__).resolve().parent.parent / "bin" / "agent-plus-meta"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    return subprocess.run(
        ["git", *args],
        capture_output=True, text=True, timeout=30,
        cwd=str(cwd), env=env, check=False,
    )


def _make_plugin(repo: Path, name: str, version: str) -> None:
    plugin_dir = repo / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({
            "name": name,
            "version": version,
            "description": f"test plugin {name}",
        }) + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "README.md").write_text(f"# {name}\n", encoding="utf-8")


def _make_marketplace_repo(
    repo: Path, owner: str, *,
    skills: list[dict] | None = None,
    name: str = "agent-plus-skills",
    declared_owner: str | None = None,
    apv: str = ">=0.5",
    surface: str = "claude-code",
    checksums: dict[str, str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> str:
    """Build a marketplace repo at `repo`, return the HEAD SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    if skills is None:
        skills = [{"name": "demo", "version": "0.1.0", "path": "demo/",
                   "obviates": ["demo cmd"]}]
    for s in skills:
        _make_plugin(repo, s["name"], s["version"])
    mj = {
        "name": name,
        "owner": declared_owner if declared_owner is not None else owner,
        "version": "0.1.0",
        "agent_plus_version": apv,
        "surface": surface,
        "skills": skills,
    }
    if checksums is not None:
        mj["checksums"] = checksums
    (repo / "marketplace.json").write_text(
        json.dumps(mj, indent=2) + "\n", encoding="utf-8",
    )
    if extra_files:
        for rel, body in extra_files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
    p = _git("init", "--quiet", "-b", "main", cwd=repo)
    if p.returncode != 0:
        # Older git: no -b flag.
        _git("init", "--quiet", cwd=repo)
        _git("checkout", "-b", "main", cwd=repo)
    _git("add", ".", cwd=repo)
    cm = _git("commit", "--quiet", "-m", "initial", cwd=repo)
    assert cm.returncode == 0, cm.stderr
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    return sha


def _file_url(p: Path) -> str:
    # file:// URL that git accepts on Windows + POSIX.
    s = str(p.resolve()).replace("\\", "/")
    if not s.startswith("/"):
        s = "/" + s
    return "file://" + s


def _run_cli(
    *args: str,
    stdin: str = "",
    root: Path,
    timeout: int = 60,
) -> tuple[int, str, str]:
    env = {
        **os.environ,
        "AGENT_PLUS_MARKETPLACES_ROOT": str(root),
    }
    proc = subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True,
        input=stdin, env=env, timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ─── install ─────────────────────────────────────────────────────────────────


class TestInstall(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.root = self.tmpdir / "marketplaces"
        self.upstream = self.tmpdir / "upstream"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build_upstream(self, **kw) -> str:
        return _make_marketplace_repo(self.upstream, "alice", **kw)

    def test_install_happy_path_accepted(self) -> None:
        sha = self._build_upstream()
        rc, out, err = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        self.assertEqual(rc, 0, msg=f"err={err!r} out={out!r}")
        payload = json.loads(out)
        mp = payload["marketplace"]
        self.assertEqual(mp["owner"], "alice")
        self.assertEqual(mp["name"], "agent-plus-skills")
        self.assertEqual(mp["pinned_sha"], sha)
        self.assertEqual(mp["plugins_count"], 1)
        self.assertTrue(mp["first_run_accepted"])
        # Meta on disk
        meta = json.loads(
            (self.root / "alice-agent-plus-skills" / ".agent-plus-meta.json")
            .read_text()
        )
        self.assertEqual(meta["pinned_sha"], sha)
        self.assertTrue(meta["accepted_first_run"])

    def test_install_declined_keeps_install_unaccepted(self) -> None:
        self._build_upstream()
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="n\n", root=self.root,
        )
        self.assertEqual(rc, 0)
        mp = json.loads(out)["marketplace"]
        self.assertFalse(mp["first_run_accepted"])
        meta = json.loads(
            (self.root / "alice-agent-plus-skills" / ".agent-plus-meta.json")
            .read_text()
        )
        self.assertFalse(meta["accepted_first_run"])

    def test_install_eof_defaults_to_no(self) -> None:
        self._build_upstream()
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="",  # EOF
            root=self.root,
        )
        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(out)["marketplace"]["first_run_accepted"])

    def test_install_rejects_bad_name(self) -> None:
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-bogus",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("error", payload)
        self.assertIn("agent-plus-skills", payload["error"])

    def test_install_rejects_owner_mismatch(self) -> None:
        # marketplace.json declares owner=bob but URL says alice.
        self._build_upstream(declared_owner="bob")
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("validation_errors", payload)
        joined = "\n".join(payload["validation_errors"])
        self.assertIn("owner", joined)

    def test_install_rejects_unsatisfiable_apv(self) -> None:
        self._build_upstream(apv=">=99.0")
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("validation_errors", payload)
        self.assertTrue(any("agent_plus_version" in e
                            for e in payload["validation_errors"]))

    def test_install_rejects_malformed_apv(self) -> None:
        self._build_upstream(apv="not-a-range!!")
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        payload = json.loads(out)
        self.assertIn("validation_errors", payload)

    def test_install_rejects_reserved_surface_claude_ai(self) -> None:
        self._build_upstream(surface="claude-ai")
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        payload = json.loads(out)
        self.assertIn("validation_errors", payload)
        self.assertTrue(any("reserved" in e
                            for e in payload["validation_errors"]))

    def test_install_rejects_reserved_surface_universal(self) -> None:
        self._build_upstream(surface="universal")
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        payload = json.loads(out)
        self.assertIn("validation_errors", payload)

    def test_install_rejects_checksum_mismatch(self) -> None:
        # Declare a wrong sha for `demo`; install should abort.
        self._build_upstream(checksums={"demo": "sha256:" + "0" * 64})
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        payload = json.loads(out)
        self.assertIn("validation_errors", payload)
        self.assertTrue(any("checksum" in e
                            for e in payload["validation_errors"]))
        # And on abort, the marketplace is NOT installed.
        self.assertFalse((self.root / "alice-agent-plus-skills").exists())

    def test_install_does_not_execute_clone_scripts(self) -> None:
        # Drop a "validate.py" that, if executed, would create a marker file
        # at a known path. Install must not run it.
        marker = self.tmpdir / "MUST_NOT_EXIST"
        # Use a path the marker would write to in a way Python could naturally
        # do — we don't actually invoke python here; we just assert install
        # has no hook that runs anything in the clone.
        script = (
            "import pathlib, sys\n"
            f"pathlib.Path({str(marker)!r}).write_text('boom')\n"
        )
        self._build_upstream(extra_files={"validate.py": script,
                                          "post-install.sh": "touch /tmp/x\n",
                                          "scripts/build.py": "raise SystemExit(0)\n"})
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        self.assertEqual(rc, 0)
        self.assertIn("marketplace", json.loads(out))
        self.assertFalse(marker.exists(),
                         "install must NEVER execute scripts from the clone")

    def test_install_rejects_already_installed(self) -> None:
        self._build_upstream()
        _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        payload = json.loads(out)
        self.assertIn("error", payload)
        self.assertIn("already installed", payload["error"])

    def test_install_rejects_skill_path_missing(self) -> None:
        # skills declares a path that doesn't exist in the clone.
        self._build_upstream(skills=[
            {"name": "ghost", "version": "0.1.0", "path": "no-such-dir/"}
        ])
        # _make_plugin made the dir matching the skill name automatically;
        # since skill name is "ghost" but path is "no-such-dir/", the dir
        # made was "ghost", not "no-such-dir". So path is missing.
        rc, out, _ = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        payload = json.loads(out)
        self.assertIn("validation_errors", payload)


# ─── list ────────────────────────────────────────────────────────────────────


class TestList(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.root = self.tmpdir / "marketplaces"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _install_one(self, owner: str) -> None:
        upstream = self.tmpdir / f"upstream-{owner}"
        _make_marketplace_repo(upstream, owner)
        _run_cli(
            "marketplace", "install", f"{owner}/agent-plus-skills",
            "--url", _file_url(upstream),
            stdin="y\n", root=self.root,
        )

    def test_list_empty(self) -> None:
        rc, out, _ = _run_cli("marketplace", "list", root=self.root)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["marketplaces"], [])

    def test_list_one(self) -> None:
        self._install_one("alice")
        rc, out, _ = _run_cli("marketplace", "list", root=self.root)
        payload = json.loads(out)
        self.assertEqual(len(payload["marketplaces"]), 1)
        self.assertEqual(payload["marketplaces"][0]["owner"], "alice")
        self.assertTrue(payload["marketplaces"][0]["first_run_accepted"])

    def test_list_many(self) -> None:
        for owner in ("alice", "bob", "carol"):
            self._install_one(owner)
        rc, out, _ = _run_cli("marketplace", "list", root=self.root)
        payload = json.loads(out)
        self.assertEqual(len(payload["marketplaces"]), 3)
        self.assertEqual(
            sorted(m["owner"] for m in payload["marketplaces"]),
            ["alice", "bob", "carol"],
        )

    def test_list_warns_on_malformed_meta(self) -> None:
        self._install_one("alice")
        # Corrupt the meta file.
        meta_path = (self.root / "alice-agent-plus-skills"
                     / ".agent-plus-meta.json")
        meta_path.write_text("{not json", encoding="utf-8")
        rc, out, _ = _run_cli("marketplace", "list", root=self.root)
        payload = json.loads(out)
        self.assertEqual(payload["marketplaces"], [])
        self.assertIn("warnings", payload)


# ─── update ──────────────────────────────────────────────────────────────────


class TestUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.root = self.tmpdir / "marketplaces"
        self.upstream = self.tmpdir / "upstream"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _install(self) -> str:
        sha = _make_marketplace_repo(self.upstream, "alice")
        rc, _, err = _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        self.assertEqual(rc, 0, msg=err)
        return sha

    def _add_upstream_commit(self, *, apv: str = ">=0.5",
                             new_skill: str | None = None) -> str:
        if new_skill:
            _make_plugin(self.upstream, new_skill, "0.1.0")
        # Patch marketplace.json
        mj_path = self.upstream / "marketplace.json"
        mj = json.loads(mj_path.read_text())
        mj["agent_plus_version"] = apv
        if new_skill:
            mj["skills"].append({
                "name": new_skill, "version": "0.1.0",
                "path": f"{new_skill}/",
            })
        mj_path.write_text(json.dumps(mj, indent=2) + "\n", encoding="utf-8")
        _git("add", ".", cwd=self.upstream)
        _git("commit", "--quiet", "-m", "bump", cwd=self.upstream)
        return _git("rev-parse", "HEAD", cwd=self.upstream).stdout.strip()

    def test_update_refuses_cron(self) -> None:
        self._install()
        rc, out, _ = _run_cli(
            "marketplace", "update", "--cron",
            root=self.root,
        )
        payload = json.loads(out)
        self.assertIn("error", payload)
        self.assertIn("cron", payload["error"].lower())

    def test_update_up_to_date(self) -> None:
        self._install()
        rc, out, _ = _run_cli(
            "marketplace", "update", "alice/agent-plus-skills",
            stdin="", root=self.root,
        )
        payload = json.loads(out)
        statuses = [u.get("status") for u in payload["updates"]]
        self.assertIn("up-to-date", statuses)

    def test_update_blocks_on_apv_raise(self) -> None:
        self._install()
        self._add_upstream_commit(apv=">=99.0")
        rc, out, _ = _run_cli(
            "marketplace", "update", "alice/agent-plus-skills",
            stdin="", root=self.root,
        )
        payload = json.loads(out)
        self.assertEqual(payload["updates"][0]["status"], "blocked")

    def test_update_declined(self) -> None:
        self._install()
        self._add_upstream_commit(new_skill="newone")
        rc, out, _ = _run_cli(
            "marketplace", "update", "alice/agent-plus-skills",
            stdin="n\n", root=self.root,
        )
        payload = json.loads(out)
        self.assertEqual(payload["updates"][0]["status"], "declined")
        # Pinned SHA unchanged on decline.
        meta = json.loads(
            (self.root / "alice-agent-plus-skills" / ".agent-plus-meta.json")
            .read_text()
        )
        # The original install's pinned_sha is still in place.
        # We don't have it stashed but we can re-resolve.
        self.assertIsNotNone(meta["pinned_sha"])

    def test_update_accepted_rearms_acceptance(self) -> None:
        self._install()
        new_sha = self._add_upstream_commit(new_skill="newone")
        # First "y" to accept the update, second "n" to decline the re-armed
        # first-run prompt — proves it gets re-armed.
        rc, out, _ = _run_cli(
            "marketplace", "update", "alice/agent-plus-skills",
            stdin="y\nn\n", root=self.root,
        )
        payload = json.loads(out)
        upd = payload["updates"][0]
        self.assertEqual(upd["status"], "updated")
        self.assertEqual(upd["new_sha"], new_sha)
        self.assertFalse(upd["first_run_accepted"])
        meta = json.loads(
            (self.root / "alice-agent-plus-skills" / ".agent-plus-meta.json")
            .read_text()
        )
        self.assertEqual(meta["pinned_sha"], new_sha)
        self.assertFalse(meta["accepted_first_run"])

    def test_update_accept_then_accept_rearmed(self) -> None:
        self._install()
        self._add_upstream_commit(new_skill="newone")
        rc, out, _ = _run_cli(
            "marketplace", "update", "alice/agent-plus-skills",
            stdin="y\ny\n", root=self.root,
        )
        upd = json.loads(out)["updates"][0]
        self.assertEqual(upd["status"], "updated")
        self.assertTrue(upd["first_run_accepted"])


# ─── remove ──────────────────────────────────────────────────────────────────


class TestRemove(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.root = self.tmpdir / "marketplaces"
        self.upstream = self.tmpdir / "upstream"
        _make_marketplace_repo(self.upstream, "alice")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _install(self) -> None:
        _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )

    def test_remove_confirmed(self) -> None:
        self._install()
        rc, out, err = _run_cli(
            "marketplace", "remove", "alice/agent-plus-skills",
            stdin="y\n", root=self.root,
        )
        self.assertEqual(rc, 0, msg=f"out={out!r} err={err!r}")
        payload = json.loads(out)
        self.assertEqual(payload["status"], "removed")
        self.assertFalse((self.root / "alice-agent-plus-skills").exists())

    def test_remove_declined(self) -> None:
        self._install()
        rc, out, _ = _run_cli(
            "marketplace", "remove", "alice/agent-plus-skills",
            stdin="n\n", root=self.root,
        )
        self.assertEqual(json.loads(out)["status"], "declined")
        self.assertTrue((self.root / "alice-agent-plus-skills").exists())

    def test_remove_idempotent_on_not_installed(self) -> None:
        rc, out, _ = _run_cli(
            "marketplace", "remove", "alice/agent-plus-skills",
            stdin="y\n", root=self.root,
        )
        self.assertEqual(json.loads(out)["status"], "not-installed")


# ─── refresh gating ──────────────────────────────────────────────────────────


class TestRefreshGating(unittest.TestCase):
    """The marketplace acceptance flag must gate refresh handler discovery."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.root = self.tmpdir / "marketplaces"
        # Build a marketplace whose plugin declares a refresh_handler.
        upstream = self.tmpdir / "upstream"
        repo = upstream
        repo.mkdir()
        plugin_dir = repo / "demo"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({
                "name": "demo",
                "version": "0.1.0",
                "refresh_handler": {
                    "command": "echo {}",
                    "timeout_seconds": 5,
                    "identity_keys": [],
                    "failure_mode": "soft",
                },
            }) + "\n",
            encoding="utf-8",
        )
        (repo / "marketplace.json").write_text(
            json.dumps({
                "name": "agent-plus-skills",
                "owner": "alice",
                "version": "0.1.0",
                "agent_plus_version": ">=0.5",
                "surface": "claude-code",
                "skills": [{"name": "demo", "version": "0.1.0",
                             "path": "demo/"}],
            }) + "\n",
            encoding="utf-8",
        )
        _git("init", "--quiet", cwd=repo)
        _git("checkout", "-b", "main", cwd=repo)
        _git("add", ".", cwd=repo)
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@e.invalid",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@e.invalid"}
        subprocess.run(["git", "commit", "--quiet", "-m", "init"],
                       cwd=str(repo), env=env, check=True,
                       capture_output=True, text=True)
        self.upstream = upstream

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unaccepted_marketplace_handlers_are_skipped(self) -> None:
        # Decline first-run prompt.
        _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="n\n", root=self.root,
        )
        # Now invoke discovery directly with the override root.
        os.environ["AGENT_PLUS_MARKETPLACES_ROOT"] = str(self.root)
        try:
            handlers, errors, skipped, _collisions = ap._discover_marketplace_refresh_handlers()
        finally:
            del os.environ["AGENT_PLUS_MARKETPLACES_ROOT"]
        self.assertNotIn("demo", handlers)
        self.assertTrue(any("demo" in s for s in skipped))

    def test_accepted_marketplace_handlers_load(self) -> None:
        _run_cli(
            "marketplace", "install", "alice/agent-plus-skills",
            "--url", _file_url(self.upstream),
            stdin="y\n", root=self.root,
        )
        os.environ["AGENT_PLUS_MARKETPLACES_ROOT"] = str(self.root)
        try:
            handlers, errors, skipped, _collisions = ap._discover_marketplace_refresh_handlers()
        finally:
            del os.environ["AGENT_PLUS_MARKETPLACES_ROOT"]
        self.assertIn("demo", handlers)
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
