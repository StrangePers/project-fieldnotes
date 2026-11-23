# SPDX-License-Identifier: MIT
"""Sync one Fieldnotes workspace profile to a Git checkout (Python 3.11+)."""

import argparse
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib


class SyncError(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise SyncError(f"Duplicate JSON key: {key}")
        obj[key] = value
    return obj


def read_json(data):
    obj = json.loads(data, object_pairs_hook=unique_object)
    if not isinstance(obj, dict):
        raise SyncError("Expected a JSON object")
    return obj


def safe_path(root, name):
    if not isinstance(name, str):
        raise SyncError("Expected a relative path string")
    parts = name.split("/")
    for part in parts:
        if (part in {"", ".", ".."} or part.lower() == ".git" or
                re.search(r'[<>:"\\|?*\x00-\x1f]', part) or
                part.endswith((" ", ".")) or
                re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³])(?:\..*)?", part)):
            raise SyncError(f"Invalid relative path: {name}")
    path = root
    for part in parts:
        path /= part
        if path.is_symlink() or (path.exists() and
                getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise SyncError(f"Linked path: {path}")
    return path


def identifier(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise SyncError(f"Invalid identifier: {name!r}")
    return name


def selection(profile, field):
    values = profile[field]
    if not isinstance(values, list) or len({identifier(x) for x in values}) != len(values):
        raise SyncError(f"Expected unique names in {field}")
    return values


def source_files(root, name, entry):
    directory = safe_path(root, name)
    if not (directory / entry).is_file():
        raise SyncError(f"Missing {name}/{entry}")
    result = {}
    for path in sorted(directory.rglob("*")):
        safe_path(root, path.relative_to(root).as_posix())
        if path.is_file():
            key = path.relative_to(directory).as_posix()
            # Source directories are exported as a unit, including skill resources.
            result[key] = path.read_bytes()
    return result


def desired(workspace, project):
    manifest = read_json(safe_path(workspace, "workspace.json").read_bytes())
    if type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise SyncError("Expected workspace.json version 1")
    if not isinstance(manifest.get("mcp"), dict) or not isinstance(manifest.get("projects"), dict):
        raise SyncError("Expected mcp and projects objects in workspace.json")
    if project not in manifest["projects"]:
        raise SyncError(f"Unknown project: {project}")
    profile = manifest["projects"][project]
    if set(profile) != {"skills", "mcp", "notes"}:
        raise SyncError("A project needs exactly skills, mcp and notes")
    units = {".fieldnotes/.gitignore": b"/.gitignore\n/notes/\n"}
    for name, data in source_files(workspace, profile["notes"], "START-HERE.md").items():
        units[".fieldnotes/notes/" + name] = data
    extra_roots = manifest.get("skill_roots", [])
    if not isinstance(extra_roots, list):
        raise SyncError("skill_roots must be a list of relative directories")
    roots = ["skills", *extra_roots]
    for directory in roots:
        safe_path(workspace, directory)
    for skill in selection(profile, "skills"):
        matches = [directory + "/" + skill for directory in roots
                   if safe_path(workspace, directory + "/" + skill + "/SKILL.md").is_file()]
        if len(matches) != 1:
            raise SyncError(f"Expected one source for {skill}, found {len(matches)}. Check skill_roots and installed packs.")
        resources = source_files(workspace, matches[0], "SKILL.md")
        if any(Path(name).name.casefold() == ".gitignore" for name in resources):
            raise SyncError(f"Fieldnotes manages Git exclusion for skill {skill}")
        for folder in (".agents/skills", ".claude/skills"):
            units[f"{folder}/{skill}/.gitignore"] = b"*\n"
            for name, data in resources.items():
                units[f"{folder}/{skill}/{name}"] = data
    hint = b"For project decisions and open questions, read .fieldnotes/notes/START-HERE.md and follow the relevant source links."
    units["AGENTS.md"] = units["CLAUDE.md"] = hint
    tables = []
    for name in sorted(selection(profile, "mcp")):
        spec = manifest["mcp"][name]
        if set(spec) == {"url"} and isinstance(spec["url"], str) and spec["url"].startswith("https://"):
            config = {"type": "http", "url": spec["url"]}
            fields = spec
        elif (set(spec) == {"command", "args"} and isinstance(spec["command"], str)
              and spec["command"] and isinstance(spec["args"], list)
              and all(isinstance(arg, str) for arg in spec["args"])):
            config = {"type": "stdio", **spec}
            fields = spec
        else:
            raise SyncError(f"Invalid MCP definition: {name}")
        units["mcp/" + name] = json_bytes(config)
        table = [f"[mcp_servers.{name}]"]
        table.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
        tables.append("\n".join(table))
    if tables:
        units[".codex/config.toml"] = "\n\n".join(tables).encode()
    if len({k.casefold() for k in units}) != len(units):
        raise SyncError("Output paths collide on a case-insensitive filesystem")
    return units


BLOCK_FILES = {"AGENTS.md", "CLAUDE.md", ".codex/config.toml"}


def markers(key):
    if key.endswith(".toml"):
        return b"# fieldnotes:begin", b"# fieldnotes:end"
    return b"<!-- fieldnotes:begin -->", b"<!-- fieldnotes:end -->"


def split_block(key, raw):
    start, end = markers(key)
    raw = raw or b""
    pattern = rb"(?ms)^" + re.escape(start) + rb"\r?\n(.*?)\r?\n" + re.escape(end) + rb"(?:\r?\n|\Z)"
    pairs = list(re.finditer(pattern, raw))
    if not pairs and start not in raw and end not in raw:
        return raw, None, b""
    if len(pairs) != 1 or raw.count(start) != 1 or raw.count(end) != 1:
        raise SyncError(f"Damaged Fieldnotes block: {key}")
    match = pairs[0]
    return raw[:match.start()], match[1].replace(b"\r\n", b"\n"), raw[match.end():]


def replace_block(key, before, value, after):
    if value is None:
        return before + after or None
    sep = b"\r\n" if b"\r\n" in before else b"\n"
    if before and not before.endswith(b"\n"):
        before += sep
    start, end = markers(key)
    return before + sep.join((start, value.replace(b"\n", sep), end, b"")) + after


def path_for(key):
    if key.startswith("mcp/"):
        identifier(key[4:])
        return ".mcp.json"
    if key in BLOCK_FILES or key == ".fieldnotes/.gitignore" or key.startswith((".fieldnotes/notes/", ".agents/skills/", ".claude/skills/")):
        return key
    raise SyncError(f"Invalid managed path: {key}")


@dataclass
class Change:
    old: bytes | None
    new: bytes | None


def plan(target, wanted, state):
    changes = {}
    seen = {}
    for key in sorted(state.keys() | wanted.keys()):
        name = path_for(key)
        path = safe_path(target, name)
        if name not in changes:
            raw = path.read_bytes() if path.exists() else None
            changes[name] = Change(raw, raw)
        change = changes[name]
        raw, value = change.new, wanted.get(key)
        if key.startswith("mcp/"):
            config = read_json(raw) if raw is not None else {}
            servers = config.get("mcpServers", {})
            if not isinstance(servers, dict):
                raise SyncError("Invalid mcpServers in .mcp.json")
            server = key[4:]
            current = json_bytes(servers[server]) if server in servers else None
        elif key in BLOCK_FILES:
            before, current, after = split_block(key, raw)
            if key == ".codex/config.toml" and current:
                actual = tomllib.loads(raw.decode("utf8")).get("mcp_servers", {})
                for server, definition in tomllib.loads(current.decode("utf8"))["mcp_servers"].items():
                    if actual.get(server) != definition:
                        raise SyncError(f"Local setting extends managed MCP server: {server}")
        else:
            current = raw
        seen[key] = fingerprint(current)
        if key not in state and current is not None and key not in BLOCK_FILES:
            raise SyncError(f"Already exists outside Fieldnotes management: {key}")
        if current == value:
            continue
        allowed = state.get(key, [None])
        if seen[key] not in allowed:
            raise SyncError(f"Local edit or name collision: {key}. Reconcile it before applying.")
        if key.startswith("mcp/"):
            if value is None:
                servers.pop(server, None)
            else:
                servers[server] = read_json(value)
            if servers:
                config["mcpServers"] = servers
            else:
                config.pop("mcpServers", None)
            change.new = json_bytes(config) if config else None
        elif key in BLOCK_FILES:
            change.new = replace_block(key, before, value, after)
            if key.endswith(".toml"):
                tomllib.loads((change.new or b"").decode("utf8"))
        else:
            change.new = value
    return {k: v for k, v in changes.items() if v.old != v.new}, seen


def atomic_write(path, data):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            tmp.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def write_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def git_path(target, option):
    result = subprocess.run(["git", "-C", str(target), "rev-parse", option],
                            capture_output=True, text=True, check=True)
    return Path(result.stdout.strip()).resolve()


def sync(action, workspace, project, target, *, register=False):
    target = target.resolve()
    if git_path(target, "--show-toplevel") != target:
        raise SyncError("Target must be the root of a Git checkout")
    workspace = workspace.resolve()
    if workspace.is_relative_to(target) or target.is_relative_to(workspace):
        raise SyncError("Keep the source workspace and target checkout in separate directories")
    metadata = git_path(target, "--absolute-git-dir") / "project-fieldnotes"
    with write_lock(metadata) if action == "apply" else nullcontext():
        binding = metadata / "binding.json"
        identity = {"workspace": str(workspace), "project": project}
        if binding.exists() and read_json(binding.read_bytes()) != identity:
            raise SyncError(f"Checkout is registered to another profile: {target}")
        ledger = metadata / "state.json"
        previous = read_json(ledger.read_bytes()) if ledger.exists() else {}
        wanted = desired(workspace, project)
        for key in wanted:
            if key.endswith("/.gitignore") and key not in previous:
                folder = safe_path(target, key).parent
                protected = folder / "notes" if key == ".fieldnotes/.gitignore" else folder
                if protected.exists() and any(protected.iterdir()):
                    raise SyncError(f"Existing directory contains unmanaged files: {protected}")
        changes, seen = plan(target, wanted, previous)
        for name, change in changes.items():
            verb = "remove" if change.new is None else "create" if change.old is None else "update"
            print(f"{verb:6} {name}")
        print(f"{len(changes)} file(s) to change")
        final = {k: [fingerprint(v)] for k, v in wanted.items()}
        if action != "apply":
            return
        if not changes and previous == final:
            if register and not binding.exists():
                atomic_write(binding, json_bytes(identity))
            return
        # After interruption either side of each atomic write is safe to retry.
        pending = {k: [seen[k], fingerprint(wanted.get(k))] for k in seen}
        atomic_write(ledger, json_bytes(pending))
        for name, change in changes.items():
            path = safe_path(target, name)
            if (path.read_bytes() if path.exists() else None) != change.old:
                raise SyncError(f"File changed during apply: {name}. Run plan again.")
            atomic_write(path, change.new)
        atomic_write(ledger, json_bytes(final))
        if register and not binding.exists():
            atomic_write(binding, json_bytes(identity))


def registry_directory(workspace):
    if git_path(workspace, "--show-toplevel") != workspace:
        raise SyncError(f"Expected a workspace Git root: {workspace}")
    return git_path(workspace, "--absolute-git-dir") / "project-fieldnotes-workspace"


def install_directory(source, target):
    """Publish a complete new directory without merging into existing user files."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as stage:
        ready = Path(stage) / "ready"
        shutil.copytree(source, ready)
        if target.exists():
            expected = {p.relative_to(ready).as_posix(): p.read_bytes() for p in ready.rglob("*") if p.is_file()}
            actual = {}
            for p in target.rglob("*"):
                relative = p.relative_to(target).as_posix()
                safe_path(target, relative)
                if p.is_file():
                    actual[relative] = p.read_bytes()
            if actual == expected:
                return
            raise SyncError(f"Refusing to replace {target}")
        ready.rename(target)


def initialize(workspace, target, project):
    workspace, target = workspace.resolve(), target.resolve()
    if workspace.is_relative_to(target) or target.is_relative_to(workspace):
        raise SyncError("Keep the workspace and target checkout in separate directories")
    if git_path(target, "--show-toplevel") != target:
        raise SyncError("Run init at a Git checkout root or pass its path")
    project = project or re.sub(r"[^a-z0-9]+", "-", target.name.lower()).strip("-")
    if not project:
        raise SyncError("Pass --project NAME using lowercase ASCII letters or digits")
    project = identifier(project)
    workspace.mkdir(parents=True, exist_ok=True)
    manifest = safe_path(workspace, "workspace.json")
    if not (workspace / ".git").exists():
        if not manifest.exists() and any(workspace.iterdir()):
            raise SyncError(f"Choose an empty workspace or one containing workspace.json: {workspace}")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    directory = registry_directory(workspace)
    with write_lock(directory):
        registry = directory / "checkouts.json"
        targets = read_json(registry.read_bytes()) if registry.exists() else {}
        key = str(target)
        if key in targets and targets[key] != project:
            raise SyncError(f"Checkout already registered as {targets[key]}")
        metadata = git_path(target, "--absolute-git-dir") / "project-fieldnotes"
        binding = metadata / "binding.json"
        identity = {"workspace": str(workspace), "project": project}
        if binding.exists() and read_json(binding.read_bytes()) != identity:
            raise SyncError("Checkout already belongs to a different workspace or profile")
        assets = Path(__file__).resolve().parent
        if not manifest.exists():
            bootstrap = directory / "bootstrap"
            if not bootstrap.exists() and any(p.name != ".git" for p in workspace.iterdir()):
                raise SyncError("Refusing to initialize an existing non-empty workspace")
            atomic_write(bootstrap, b"1")
            starter = assets / "examples/workspace/skills/decision-review"
            install_directory(starter, safe_path(workspace, "skills/decision-review"))
            atomic_write(manifest, json_bytes({"version": 1, "projects": {}, "mcp": {}}))
            bootstrap.unlink(missing_ok=True)
        config = read_json(manifest.read_bytes())
        if config.get("version") != 1 or not isinstance(config.get("projects"), dict) or not isinstance(config.get("mcp"), dict):
            raise SyncError("Expected a version 1 workspace with projects and mcp objects")
        if project not in config["projects"]:
            notes = "projects/" + project
            install_directory(assets / "template", safe_path(workspace, notes))
            skills = ["decision-review"] if safe_path(workspace, "skills/decision-review/SKILL.md").is_file() else []
            config["projects"][project] = {"skills": skills, "mcp": [], "notes": notes}
            atomic_write(manifest, json_bytes(config))
        sync("apply", workspace, project, target, register=True)
        targets[key] = project
        atomic_write(registry, json_bytes(targets))
        print(f"Project {project} is connected. Edit {workspace / config['projects'][project]['notes'] / 'START-HERE.md'}")


def sync_registered(workspace, check):
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise SyncError(f"No workspace at {workspace}. Connect a Git checkout with init first.")
    directory = registry_directory(workspace)
    registry = directory / "checkouts.json"
    if not registry.is_file():
        raise SyncError("No registered checkouts. Run init in a project first.")
    with write_lock(directory):
        targets = read_json(registry.read_bytes())
        for target, project in targets.items():
            path = Path(target)
            if not path.is_dir() or git_path(path, "--show-toplevel") != path:
                raise SyncError(f"Registered checkout moved or missing: {path}. Check {registry}")
            binding = git_path(path, "--absolute-git-dir") / "project-fieldnotes/binding.json"
            if not binding.is_file() or read_json(binding.read_bytes()) != {"workspace": str(workspace), "project": project}:
                raise SyncError(f"Checkout binding changed: {path}. Check {registry}")
            print(f"Plan {project} ({path})")
            sync("plan", workspace, project, path)
        if not check:
            for target, project in targets.items():
                print(f"Apply {project} ({target})")
                sync("apply", workspace, project, Path(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "apply"):
        command = commands.add_parser(action)
        command.add_argument("workspace", type=Path, help="Folder containing workspace.json")
        command.add_argument("project", help="Project key in the manifest")
        command.add_argument("target", type=Path, help="Root of the receiving Git checkout")
    init = commands.add_parser("init", help="Connect an existing Git checkout to a shared workspace")
    init.add_argument("target", type=Path, nargs="?", help="Receiving Git checkout (default: current directory)")
    init.add_argument("--project", help="Override the profile name derived from the checkout folder")
    batch = commands.add_parser("sync", help="Sync all locally registered checkouts")
    batch.add_argument("--check", action="store_true", help="Preview changes without applying them")
    for command in (init, batch):
        command.add_argument("--workspace", type=Path, default=Path.home() / "fieldnotes-workspace")
    args = parser.parse_args()
    try:
        if args.action == "init":
            if args.target is None and Path.cwd().resolve() == Path(__file__).resolve().parent:
                raise SyncError("Pass the path to the Git project you want to connect: python fieldnotes.py init PATH")
            initialize(args.workspace, args.target or Path.cwd(), args.project)
        elif args.action == "sync":
            sync_registered(args.workspace, args.check)
        else:
            sync(args.action, args.workspace, args.project, args.target)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Fieldnotes: {exc}\n")


if __name__ == "__main__":
    main()
