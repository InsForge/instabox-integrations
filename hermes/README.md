# instabox backend for Hermes Agent

Run the terminal work of [Hermes Agent](https://github.com/NousResearch/hermes-agent) in an
[instabox](https://instabox.sh) sandbox: a cloud Linux machine for agents.

The agent keeps running on your machine. Your model calls, sessions, memory and approvals stay
there. Only the execution moves: the `terminal`, `execute_code` and file tools (`read_file`,
`write_file`, `patch`, `search_files`) run in an instabox box with its own filesystem. Two things
cross over through Hermes file sync. Your skills, cache and skill-registered credential files are
uploaded to the box, and skill and cache files changed in the box are copied back to your machine.

This is a Hermes terminal backend plugin. It registers the backend `instabox`, which you select like
a built-in one with `terminal.backend`.

## Install

```bash
hermes plugins install InsForge/instabox-integrations#hermes
hermes plugins enable instabox
hermes config set terminal.backend instabox
```

The install asks for `INSTABOX_API_KEY` and saves it to your Hermes `.env`. Create a key on
**API keys** in the [dashboard](https://dashboard.instabox.sh). The plugin depends on the
[`instabox-sdk`](https://pypi.org/project/instabox-sdk/) Python package, which Hermes installs from
`pyproject.toml` after you consent. `hermes doctor` shows whether the SDK and the key are in place.

## Configure

The defaults work as they are. Every key is optional and lives under `terminal:` in
`config.yaml`:

```yaml
terminal:
  backend: instabox
  container_persistent: true            # one box per task, paused on exit and resumed by name
  instabox_template: agent-browser      # template of new boxes
  instabox_vcpus: 4                     # size of new boxes: set both or neither
  instabox_mem_mib: 8192
  instabox_name_prefix: hermes          # box names are <prefix>-<task id>
  instabox_delete_after_seconds: 86400  # delete new boxes this long after creation (60 to 86400)
```

| Setting | Default | Meaning |
|---|---|---|
| `INSTABOX_API_KEY` | required | Workspace credential, read through the Hermes secret scope of the active profile. |
| `INSTABOX_API_URL` | `https://api.instabox.sh` | API endpoint, read the same way. |
| `terminal.container_persistent` | `true` | Persistence mode, see below. |
| `terminal.instabox_template` | `agent-browser` | Template of new boxes. A batch run's per-task image override is used as the template. |
| `terminal.instabox_vcpus`, `terminal.instabox_mem_mib` | the template's size | One of the sizes your workspace offers. Another size is refused by the API with `spec_not_allowed`. |
| `terminal.instabox_name_prefix` | `hermes` | Prefix of box names. Give each machine or profile its own to keep their boxes apart. |
| `terminal.instabox_delete_after_seconds` | none | A delete timer for new boxes, a safety net for boxes Hermes never cleans up. |

`terminal.container_cpu`, `container_memory` and `container_disk` are ignored. They are Docker
oriented defaults (1 CPU and 5 GB), and mapping them would quietly shrink every box. Use
`instabox_vcpus` and `instabox_mem_mib` instead.

**Why `agent-browser`.** It is the instabox default template. In our tests it was created in
1 to 2.5 s, against 4 to 6.5 s for `universal` and `minimal`. It has the full `universal` toolchain
(Python, Node.js, Go, Rust, Docker, git, ripgrep and more) plus a desktop with Chromium, which
the agent can use from the command line. The idle desktop used about the same memory as
`universal`. Pick `universal` for the same tools without a desktop, or `minimal` for a smaller
box without ripgrep.

## How it behaves

- **Persistent mode** (`container_persistent: true`, the default). Each task gets a box named
  `<prefix>-<task id>` with the metadata `hermes_task_id`. When Hermes starts the task again it
  finds the box by name and starts or resumes it, so files and installed tools are still there.
  On exit the box is paused, not deleted. Pausing keeps memory and running programs. A box in the
  error state is reported, not replaced, so nothing is lost silently.
- **Per-session boxes** (`container_persistent: false`). Every session creates its own box,
  `<prefix>-<task id>-<random suffix>`, and deletes it when the session ends. The box lasts for the
  whole session, so files carry over from one turn to the next. The plugin only deletes the box it
  created in that session, by its ID.
- **The first command of a session takes longer**, about 5 to 7 s in our tests: the box is
  created or resumed, your Hermes files are uploaded as one archive and the shell is set up. Each
  later command is one API round trip, about 0.8 s in our tests.
- **Output comes back when a command exits.** Stdout and stderr are merged, in order. instabox
  keeps the first 1 MiB of a command's output. Past that the output is cut and a note says so.
  Media that the gateway sends from the box is copied over the file API, with no such limit.
- **Timeouts and interrupts stop the command, not the box.** A command that runs past its
  timeout, or that you interrupt, returns at once with exit code `124` or `130`. A second call
  then ends its whole process group in the box (`SIGTERM`, then `SIGKILL` a second later). The box
  and other commands keep running.
- **A foreground command runs at most 600 s.** instabox ends it then (exit code `124`, with a
  note). Run longer work with `background=true`. Background processes keep running after the
  command that started them.
- **A paused box is resumed.** If a command finds its box paused (instabox pauses a box after 30
  idle minutes, or you paused it in the dashboard), the plugin resumes it once and runs the
  command.
- **A deleted box is replaced.** If a command finds that its box no longer exists, it says so and
  does not run. The next command runs in a new box, and Hermes tells the model that the
  environment was recreated.
- **Commands start in the box user's home**, `/data/home`. An absolute `terminal.cwd` is used as
  given. A `cd` carries over to later commands, as on the other Hermes backends.
- **Your Hermes files are synced.** Skills, the cache and credential files registered by skills
  are uploaded to `~/.hermes` in the box before commands run. When the box is cleaned up, skill and
  cache files changed in the box are copied back to your machine (credential files are upload only).
- **Dangerous-command approval is skipped inside the box**, as for the other cloud backends,
  because commands run in the box and not on your machine. The box user has passwordless `sudo`.
- **The prompt-time backend probe gets no box**, so the first reply never waits on one. The system
  prompt describes the backend as an instabox cloud sandbox.

## What leaves your machine

Before you install, know what this plugin does:

- It sends every command the agent runs, and the files the file tools read and write, to instabox
  over HTTPS.
- It uploads your Hermes skills, cache and skill-registered credential files to `~/.hermes` in the
  box.
- It copies skill and cache files changed in the box back to your machine when the box is cleaned
  up.
- It creates, starts, pauses, resumes and deletes boxes in the workspace of your API key, billed to
  that workspace. Paused boxes keep their disk until they are deleted.
- It reads `INSTABOX_API_KEY` (and `INSTABOX_API_URL` if set) through the Hermes secret scope and no
  other credential store. The key is listed in the backend's `strip_env_keys`, so Hermes removes it
  from every process it starts. It is never sent into the box or logged.

It sends no telemetry. The SDK identifies itself only with the `User-Agent` `instabox-python`. It
never updates itself.

## Requirements

- Hermes Agent `>=0.22.3` with `plugins.isolation: in_process` (the default). A terminal backend
  cannot run in the plugin host.
- Python `>=3.11` and `instabox-sdk>=0.5,<1`.
- An instabox workspace and an API key that can create and delete sandboxes.

## Limitations

- **No Bot Desktop or `computer_use` in the box.** The backend does not declare
  `supports_streams`, because no local command reaches a box with stdin and stdout held open.
- **No streaming.** You see a command's output when it exits, not while it runs.
- **Persistent boxes add up.** Persistent mode keeps one paused box per task. Hermes CLI runs all
  use the task id `default` (so `hermes-default`), but every gateway session is its own task.
  List them with `instabox ls` and delete the ones you no longer need with `instabox rm <name>`.
- **Box names are per workspace.** Two machines or profiles with the same prefix resume the same
  persistent box, and one of them pauses it when it exits. Give each its own
  `instabox_name_prefix`.
- **Leftover boxes after a crash.** If Hermes is killed before cleanup, its box stays in your
  workspace. instabox pauses it after 30 idle minutes. `instabox_delete_after_seconds` deletes new
  boxes on a timer.

## Development

The tests import Hermes core, so run them with the interpreter of a hermes-agent checkout that has
`instabox-sdk` and `pytest` installed:

```bash
pytest                                      # unit tests with a fake client, no network
INSTABOX_API_KEY=... pytest -m integration  # real boxes, billed to the key's workspace
hermes plugins validate .                   # the catalog checks
```

The unit tests run the real Hermes command path (shell snapshot, cwd tracking, stdin, timeouts,
kills, file sync) against a fake client whose boxes are temporary directories and whose exec calls
run local `bash`. The integration tests name their boxes `hermes-integ-...`, give them a one-hour
delete timer and delete them when they finish.

## License

Apache-2.0
