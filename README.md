# Project Fieldnotes

Manage shared AI skills, MCP connections and project notes across repositories. A private workspace supplies each project with the resources it needs in Claude Code and Codex.

## Connect your first project

From a clone of Project Fieldnotes, run the following with Python 3.11+ and Git, replacing the path with the root of your existing project:

```sh
python fieldnotes.py init /path/to/your-project
```

This creates a local workspace at `~/fieldnotes-workspace`, connects the checkout to a starter skill and prints the start page to edit. Run the same command with another project's path to connect it. After editing the workspace, run `python fieldnotes.py sync` once to update all connected projects. Use `sync --check` for a preview. [Custom paths, skill packs and selective MCP setup](docs/workspaces.md).

Why did the team print a booking code under the QR on each ticket? Start with the [Lantern Film Club's project notes](examples/lantern/START-HERE.md). Follow one decision to the rehearsal that prompted it. The same page also records a question the team still can't answer.

[Читать по-русски](ru/README.md)

## Start a notebook for your project

1. Copy the [template folder](template/) into your repository as `project-notes/`, or choose another unused name. Keep notes about private work in a private repository.
2. Fill in the [start page](template/START-HERE.md). In `project-notes/decisions/`, copy `decision.md` under a name that describes a choice your team made. Write down the reason, link the evidence and say when to revisit it.
3. Link that record under **Current decisions** on your start page. If you use an AI coding assistant, add this sentence to your project's existing instructions, adjusting the path if necessary:

   > When answering questions about project decisions, read `project-notes/START-HERE.md` and follow the relevant source link.

4. In a fresh chat with access to the project, ask why the team made that choice and what would make it reconsider. Open the source in the answer and check that it supports the explanation. If the source is inaccessible, ask which record the assistant could verify.

## Borrow a technique

For Android device checks and localized screenshot reviews, [connect the optional Fieldnotes Skills library](docs/workspaces.md#use-a-skill-pack).

| When you need to… | Try this |
| --- | --- |
| Bring a teammate or a fresh chat up to speed | [Hand off a project on one page](techniques/handoff.md) |
| Preserve the reason behind a choice | [Write a decision with its source](techniques/decision.md) |
| Decide whether an observation warrants a change | [Test an idea before adopting it](techniques/verify-an-idea.md) |

Each technique works on its own in your existing docs. The templates need only a text editor.

## License

The Python CLI and its tests use the [MIT License](LICENSES/MIT.txt). You can use and adapt them commercially. Include the copyright and permission notice when distributing copies or substantial portions.

You can copy the [blank templates](template/) into a private or commercial project and fill them in without attribution. They are available under [CC0 1.0](template/LICENSE).

You can share, translate and adapt the guides and worked examples under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). When sharing them, credit **Project Fieldnotes by StrangePers**, link to the project and license, and indicate any changes. See the [license notice](LICENSE) for a ready-to-copy credit.
