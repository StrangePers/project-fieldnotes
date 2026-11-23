"""Sync one Fieldnotes workspace profile to a Git checkout (Python 3.11+)."""

import argparse
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
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
    for skill in selection(profile, "skills"):
        resources = source_files(workspace, "skills/" + skill, "SKILL.md")
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


def sync(action, workspace, project, target):
    target = target.resolve()
    if git_path(target, "--show-toplevel") != target:
        raise SyncError("Target must be the root of a Git checkout")
    workspace = workspace.resolve()
    if workspace.is_relative_to(target) or target.is_relative_to(workspace):
        raise SyncError("Keep the source workspace and target checkout in separate directories")
    metadata = git_path(target, "--absolute-git-dir") / "project-fieldnotes"
    with write_lock(metadata) if action == "apply" else nullcontext():
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
        if action != "apply" or (not changes and previous == final):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "apply"])
    parser.add_argument("workspace", type=Path, help="Folder containing workspace.json")
    parser.add_argument("project", help="Project key in the manifest")
    parser.add_argument("target", type=Path, help="Root of the receiving Git checkout")
    args = parser.parse_args()
    try:
        sync(args.action, args.workspace, args.project, args.target)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Fieldnotes: {exc}\n")


if __name__ == "__main__":
    main()
