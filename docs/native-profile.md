# Connect an existing C++ or Qt project

The example `native` profile selects three public skills. Use `qt-headless-ui-check` for an interaction on a virtual display, `cpp-dll-abi-check` for a Windows DLL upgrade, and `native-decompiler-crosscheck` for a calculation in your own optimized build. [Example manifest](../examples/native-workspace/workspace.json)

## Set up the workspace

Run these commands from a clone of Project Fieldnotes with Python 3.11+ and Git. The first command copies the example to a new sibling directory named `native-workspace`. Keep that directory separate from the project receiving the skills.

```sh
python -c "import shutil; shutil.copytree('examples/native-workspace', '../native-workspace')"
git init ../native-workspace
git -C ../native-workspace submodule add https://github.com/StrangePers/fieldnotes-skills.git packs/fieldnotes-skills
git -C ../native-workspace/packs/fieldnotes-skills checkout --detach 8fa582e8dc84ef369bd9fa2c6d55cc8c948e7cdd
git -C ../native-workspace add .gitmodules packs/fieldnotes-skills workspace.json projects/native
git -C ../native-workspace commit -m "Pin the native Fieldnotes profile"
```

## Connect the checkout

Replace `../my-qt-project` with the root of an existing Git project that you are connecting to Fieldnotes for the first time. The optional `plan` command previews the files before `init` applies the profile.

```sh
python fieldnotes.py plan ../native-workspace native "../my-qt-project"
python fieldnotes.py init "../my-qt-project" --workspace ../native-workspace --project native
python fieldnotes.py sync --workspace ../native-workspace --check
```

The final command should print `0 file(s) to change`. In the receiving checkout, open `.agents/skills/cpp-dll-abi-check/examples/worked-example.md`. The same three skill directories are also available under `.claude/skills/`. The profile selects no MCP servers.

For a checkout already connected to Fieldnotes, keep its existing workspace and profile. Add the pack there, append the three names to that profile's skill list, then run `sync --check` and `sync` against that workspace. [Add a pack to an existing workspace](workspaces.md#use-a-skill-pack)

To share the example workspace, push it to your team's repository. Teammates can clone it with `git clone --recurse-submodules` and run the connection commands using their own checkout paths.
