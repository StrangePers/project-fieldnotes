# One workspace, two projects

A workspace is a directory you control, usually a private Git repository. It holds the source of each shared skill, the definitions of MCP connections and notes for each project. A profile in `workspace.json` selects what a checkout receives. The checkout path is a local command argument, so teammates can use different directory layouts.

## Connect real projects

Run `python fieldnotes.py init /path/to/checkout` from the tool repository for each Git checkout. The default workspace is `~/fieldnotes-workspace`. On a second machine, clone your private workspace there first. For a different location, pass `--workspace PATH` to both `init` and `sync`. Use `init ... --project NAME` to pick an existing profile or give two similarly named projects distinct names. An existing profile keeps its selections. New profiles start with project notes, the local decision-review skill when available, and no MCP connections.

Change the source skill once, then run `python fieldnotes.py sync --check` to preview all registered checkouts or `python fieldnotes.py sync` to apply. All checkouts are checked for conflicts before the first write. An interruption during application can leave some checkouts updated; resolve any conflict and repeat the command. Pull changes to a shared workspace with your usual Git workflow before syncing.

Checkout paths are registered only on this machine in the workspace Git metadata under `project-fieldnotes-workspace/checkouts.json`. If a checkout moves, remove its stale entry in that file and run `init` at the new path. A fresh clone registers its own local checkouts. Select individual checkout operations with the original `plan` and `apply` commands below.

## Use a skill pack

The [Fieldnotes Skills library](https://github.com/StrangePers/fieldnotes-skills) includes `android-device-state-check` for testing a change on a device while preserving user data and `localized-screenshot-review` for comparing screenshots across locales.

1. Connect your project with `init` as described above. From the root of your **private workspace** (by default `~/fieldnotes-workspace`), add the library once:

   ```sh
   git submodule add https://github.com/StrangePers/fieldnotes-skills.git packs/fieldnotes-skills
   ```

2. Edit the workspace's `workspace.json`. At the top level, add `"skill_roots": ["packs/fieldnotes-skills/skills"]`. If the list exists, append the path. In `projects`, find the profile you want to change and append one or both names to its `skills` list. For example, a profile that already uses the starter skill can have:

   ```json
   "skills": ["decision-review", "android-device-state-check", "localized-screenshot-review"]
   ```

3. From the **Project Fieldnotes tool repository**, preview and apply. If you used a custom workspace, add `--workspace PATH` to both commands.

   ```sh
   python fieldnotes.py sync --check
   python fieldnotes.py sync
   ```

   The preview lists the selected skills under `.agents/skills/` and `.claude/skills/` in the receiving checkout. A second preview should report zero changes. If a local skill and a pack supply the same name, resolve the duplicate before syncing.

4. To share the selection, commit `.gitmodules`, the `packs/fieldnotes-skills` submodule pointer and `workspace.json` in your private workspace. After cloning or pulling that workspace, teammates run `git submodule update --init` there, then register their checkout with `init`. Git checks out the library revision recorded by your team.

## Try the sample

**Client compatibility on Windows.** Codex CLI 0.166.0 and Claude Code 2.1.316 passed the skill, instruction and project-specific MCP checks. In a separate test, Codex CLI 0.41.0 loaded the project instructions but missed the shared skill in both checkouts and the selected MCP server in the documentation checkout. Run `codex --version` when investigating this symptom. Versions between these two Codex releases have not been tested here.

You need Git, Python 3.11 or later and a text editor. Run these commands from the Project Fieldnotes repository. The two example targets should be new directories. On Windows, use `python` as shown. On systems where Python 3 is named `python3`, substitute that command.

```sh
git init ../lantern-trial
git init ../docs-trial
python fieldnotes.py plan examples/workspace lantern ../lantern-trial
python fieldnotes.py apply examples/workspace lantern ../lantern-trial
python fieldnotes.py plan examples/workspace docs-site ../docs-trial
python fieldnotes.py apply examples/workspace docs-site ../docs-trial
```

| What the client sees | lantern | docs-site |
| --- | --- | --- |
| Skill | decision-review | decision-review |
| Project notes | Film club and door rehearsal | Documentation team's source policy |
| MCP added by Fieldnotes | None | protocol-docs, the official MCP documentation endpoint |

Open each checkout in Claude Code or Codex. Follow your client's normal project trust or MCP approval flow for a checkout you trust. Start a new session after applying. Look for `decision-review` in the skill list and inspect the client's MCP list. Ask a session in Lantern why the booking code is printed beneath the QR. The handoff points to the rehearsal in this checkout. Other MCP servers the client gets from your own account may also appear.

Edit [the shared skill](../examples/workspace/skills/decision-review/SKILL.md). Run `plan` and `apply` for each checkout again. Both clients should receive the edit. A subsequent plan reports zero files to change. Applying is local and explicit on each checkout.

## Use your own workspace

Copy [the sample workspace](../examples/workspace/) to a separate private directory outside the target checkout. Give each profile a short project name and a notes directory containing `START-HERE.md`. Use a relative path for `notes`, a list of skill names in `skills`, and a list of approved connection names in `mcp`. An empty list selects none. Keep intentionally shared resources together: the CLI copies all files in each selected notes or skill directory. Put credentials and unrelated files elsewhere.

A skill lives in `skills/<name>/SKILL.md` with the client's standard name and description front matter. It may include supporting files. Use a lowercase name with digits or hyphens. Source directories must contain ordinary files and directories. For a remote MCP supply `{"url": "https://…"}`. For a local one supply `{"command": "program-on-PATH", "args": ["argument"]}`. Install that program on each machine separately. Use the client's own login or local credential setup. Sync copies definitions and never starts or installs servers.

## What changes on disk

- The CLI copies notes to `.fieldnotes/notes/` and skills to `.agents/skills/` (Codex) and `.claude/skills/` (Claude Code). Generated ignore files keep newly copied notes and skill folders out of ordinary Git staging. Git's existing tracked files and forced adds are outside that protection.
- It adds a marked section to `AGENTS.md` and `CLAUDE.md`. Existing instructions outside the section are preserved.
- For selected MCP servers it adds a marked block to `.codex/config.toml` and merges named entries into `.mcp.json`. Other Claude config keys survive, though JSON formatting may change. Review the native configuration and instruction-file diffs before committing. Treat a workspace containing local commands as executable configuration and review it before applying.
- The private sync journal lives in this checkout's Git metadata. It keeps hashes of managed content. A conflicting local edit or an existing skill directory stops the plan. Reconcile the local change with the source and restore the managed copy before retrying. After an interrupted apply, repeat the same command. Removing a selection in the manifest removes its previously managed output on the next apply. Files outside Fieldnotes management are preserved.

To keep an existing decision alongside the code that owns it, reference its path in the project's start page. Choose notes and skills suited to the people who can open the receiving checkout.

Run the regression suite from the tool repository with `python -B -m unittest discover -s tests -v`.
