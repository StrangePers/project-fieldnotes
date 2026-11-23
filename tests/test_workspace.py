# SPDX-License-Identifier: MIT
"""Regression tests for project-local synchronization. No installed AI clients required."""

import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import fieldnotes as fn


class WorkspaceSync(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / "shared workspace"
        shutil.copytree(Path(fn.__file__).parent / "examples/workspace", self.workspace)
        self.target = self.root / "project with spaces"
        subprocess.run(["git", "init", "-q", self.target], check=True)

    def run_sync(self, action="apply", project="docs-site"):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            fn.sync(action, self.workspace, project, self.target)
        return output.getvalue()

    def snapshot(self):
        return {p.relative_to(self.target): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.target.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_preview_apply_repeat_and_ignored_private_material(self):
        self.assertIn("create .mcp.json", self.run_sync("plan"))
        self.assertFalse(self.snapshot())
        self.assertFalse((self.target / ".git/project-fieldnotes").exists())
        self.run_sync()
        snapshot = self.snapshot()
        self.assertIn("0 file(s)", self.run_sync())
        self.assertEqual(snapshot, self.snapshot())
        for path in [".fieldnotes/notes/START-HERE.md", ".agents/skills/decision-review/SKILL.md",
                     ".claude/skills/decision-review/SKILL.md"]:
            subprocess.run(["git", "-C", self.target, "check-ignore", "-q", path], check=True)

    def test_source_update_and_unselect_preserve_user_configuration(self):
        (self.target / "AGENTS.md").write_bytes(b"Keep my rules.\r\n")
        mcp = self.target / ".mcp.json"
        local = {"mcpServers": {"other": {"command": "local"}}, "otherKey": 7}
        mcp.write_text(json.dumps(local))
        toml = self.target / ".codex/config.toml"
        toml.parent.mkdir()
        toml.write_bytes(b'# user config\n[mcp_servers.other]\ncommand = "local"\n')
        self.run_sync()
        skill = self.workspace / "skills/decision-review/SKILL.md"
        skill.write_bytes(skill.read_bytes() + b"\nCheck dates.\n")
        self.run_sync(project="lantern")
        for folder in [".claude", ".agents"]:
            self.assertEqual(skill.read_bytes(), (self.target / folder / "skills/decision-review/SKILL.md").read_bytes())
        self.assertEqual(json.loads(mcp.read_text()), local)
        self.assertEqual(tomllib.loads(toml.read_text()), {"mcp_servers": {"other": {"command": "local"}}})
        self.assertTrue((self.target / "AGENTS.md").read_bytes().startswith(b"Keep my rules.\r\n"))
        self.assertIn("Lantern", (self.target / ".fieldnotes/notes/START-HERE.md").read_text())

    def test_local_edit_blocks_all_writes(self):
        self.run_sync()
        p = self.target / ".claude/skills/decision-review/SKILL.md"
        p.write_text("local edits")
        before = self.snapshot()
        with self.assertRaisesRegex(fn.SyncError, "Local edit"):
            self.run_sync(project="lantern")
        self.assertEqual(before, self.snapshot())

    def test_initial_name_collision_leaves_files_alone(self):
        p = self.target / ".mcp.json"
        p.write_text('{"mcpServers":{"protocol-docs":{"url":"https://local.invalid"}}}')
        before = self.snapshot()
        with self.assertRaisesRegex(fn.SyncError, "Already exists"):
            self.run_sync()
        self.assertEqual(before, self.snapshot())

    def test_retry_after_interruption(self):
        real = fn.atomic_write
        def interrupt(path, data):
            if path.name == "CLAUDE.md":
                raise OSError("simulated interruption")
            real(path, data)
        with patch.object(fn, "atomic_write", interrupt), self.assertRaises(OSError):
            self.run_sync()
        self.run_sync()
        self.assertIn("0 file(s)", self.run_sync())

    def test_escape_and_conflicting_toml_are_rejected(self):
        for name in ["../private", "D:/data", ".git/config", "CON.txt"]:
            with self.subTest(name=name), self.assertRaises(fn.SyncError):
                fn.safe_path(self.target, name)
        toml = self.target / ".codex/config.toml"
        toml.parent.mkdir()
        toml.write_text('[mcp_servers.protocol-docs]\nurl="https://local.invalid"')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.run_sync()
        self.assertEqual(before, self.snapshot())

    def test_removed_skill_resources_are_removed_only_if_managed(self):
        self.run_sync()
        other = self.target / ".claude/skills/decision-review/personal.md"
        other.write_text("keep")
        manifest = self.workspace / "workspace.json"
        data = json.loads(manifest.read_text())
        data["projects"]["docs-site"]["skills"] = []
        manifest.write_text(json.dumps(data))
        self.run_sync()
        self.assertTrue(other.exists())
        self.assertFalse((other.parent / "SKILL.md").exists())

    def test_one_skill_update_reaches_two_clients_in_two_projects(self):
        first = self.target
        second = self.root / "second"
        subprocess.run(["git", "init", "-q", second], check=True)
        skill = self.workspace / "skills/decision-review/SKILL.md"
        for suffix in (b"", b"\nCheck the source date.\n"):
            if suffix:
                skill.write_bytes(skill.read_bytes() + suffix)
            for checkout, project in [(first, "lantern"), (second, "docs-site")]:
                self.target = checkout
                self.run_sync(project=project)
                for client in [".agents", ".claude"]:
                    self.assertEqual((checkout / client / "skills/decision-review/SKILL.md").read_bytes(),
                                     skill.read_bytes())
                self.assertEqual((checkout / ".mcp.json").exists(), project == "docs-site")
                self.assertIn("0 file(s)", self.run_sync("plan", project))

    def test_stdio_arguments_and_custom_mcp_keys_survive_rendering(self):
        manifest = self.workspace / "workspace.json"
        data = json.loads(manifest.read_text())
        spec = {"command": "python", "args": ["a path with spaces", 'quote"test']}
        data["mcp"]["protocol-docs"] = spec
        manifest.write_text(json.dumps(data))
        self.run_sync()
        self.assertEqual(json.loads((self.target / ".mcp.json").read_text())["mcpServers"]["protocol-docs"],
                         {"type": "stdio", **spec})
        self.assertEqual(tomllib.loads((self.target / ".codex/config.toml").read_text())
                         ["mcp_servers"]["protocol-docs"], spec)

if __name__ == "__main__":
    unittest.main()
