# instabox for Dify

**Author:** instabox
**Type:** tool
**Source:** https://github.com/InsForge/instabox-integrations/tree/main/dify
**Contact:** support@instabox.sh

A [Dify](https://dify.ai) tool plugin that lets workflows and agents run commands
and code in [instabox](https://instabox.sh) sandboxes, and move files in and out
of them.

An instabox sandbox is a cloud Linux machine for agents. The default sandbox runs
Debian 13 with Python 3, Node.js, Bun, Go, Java, git and a C toolchain. Commands
run as the user `user` with the home directory `/data/home`. A sandbox is ready
about three seconds after it is created.

## Install

In Dify, open **Plugins**, click **Install plugin** and pick one:

- **Marketplace**: search for `instabox`.
- **Local Package File**: build `instabox-<version>.difypkg` (see
  [Development](#development)) and upload it. A self-hosted Dify installs an
  unsigned local package only with `FORCE_VERIFYING_SIGNATURE=false` in its `.env`.

The plugin needs Dify 1.10.0 or newer.

## Setup

1. Sign in to the [instabox dashboard](https://dashboard.instabox.sh).
2. Open **API keys** and click **Create Key**. Choose **Full access**, or
   **Restricted** with Read, Write and Delete for sandboxes. The key starts with
   `ibk_` and is shown once.
3. In Dify, open **Plugins**, select **instabox** and click **Authorize**.
4. Paste the API key and save.

Dify checks the key by listing one sandbox. A wrong key gives
`instabox rejected the API key (invalid_key). Check it in the plugin settings.`

The plugin creates its sandboxes in the workspace the key belongs to. Use a key
for a workspace that holds only sandboxes for Dify (see [Security](#security)).

## Tools

| Tool | Inputs | Outputs |
|---|---|---|
| Run Command | `command`, and optional `sandbox_id`, `cwd`, `stdin`, `timeout` | `stdout`, `stderr`, `exit_code` |
| Run Code | `code`, and optional `language`, `sandbox_id`, `timeout` | `stdout`, `stderr`, `exit_code` |
| Upload File | `sandbox_id`, `file`, and optional `path` | `path` |
| Download File | `sandbox_id`, `path` | the file |
| Create Sandbox | optional `name`, `template`, `delete_after_seconds` | `sandbox_id` |
| Delete Sandbox | `sandbox_id` | none |

`sandbox_id` takes a sandbox ID (`m_...`) or a sandbox name.

Every tool returns a text message and a JSON object. The outputs in the table are
also workflow variables, so a later node can use `stdout`, `exit_code` or
`sandbox_id` directly. Download File returns the file in the node's `files`
output.

The JSON object of Run Command and Run Code also has `timed_out`, `truncated`,
`sandbox_id` and `one_off` (true when the sandbox was made for this call only).
Run Code adds `language`.

**Run Code** languages:

| `language` | Runs the file with |
|---|---|
| `python` (default) | `python3` |
| `javascript` | `node` (CommonJS and ES module syntax both work) |
| `typescript` | `bun run` |
| `bash` | `bash` |
| `go` | `go run` (the first run in a sandbox takes a few seconds longer to build) |
| `java` | `java` (single-file source launch, any class name) |

## Usage

**One-off runs.** Call **Run Command** or **Run Code** without a `sandbox_id`.
The plugin creates a sandbox named `dify-<random>`, runs the command and deletes
the sandbox. Nothing is kept. Each one-off sandbox also gets a 15-minute
delete timer, so instabox removes it even when the plugin stops before it can.

**Work across steps.** Call **Create Sandbox** first and pass its `sandbox_id`
to the other tools. Files and installed packages stay in the sandbox between
steps. Call **Delete Sandbox** at the end. A typical workflow:

1. **Create Sandbox**
2. **Upload File** with a CSV from the user
3. **Run Code** with Python that reads the CSV and writes `chart.png`
4. **Download File** with `chart.png`
5. **Delete Sandbox**

**Delete timer.** **Create Sandbox** sets `delete_after_seconds` (default 3600).
instabox deletes the sandbox that many seconds after it was created, also when
the workflow stops halfway. Set 0 to keep it until you delete it.

**Agents.** In an **Agent** app, add the instabox tools under **Tools**. The
agent can then create a sandbox, install packages, run code and fetch results.

**An existing sandbox.** Pass the ID or name of any sandbox in the key's
workspace, for example one you made in the dashboard. A paused or stopped
sandbox is started first. A sandbox pauses after 30 idle minutes by default.

**Timeouts.** Run Command and Run Code take `timeout` in seconds (1 to 600,
default 120). instabox stops the command when it runs out. The result then has
`timed_out` set to true and `exit_code` 124.

## Errors

A tool that fails makes its workflow node fail with the message below. An agent
gets the message as the tool result.

| What happened | What you get |
|---|---|
| The command or program exits non-zero | No error. The result has that `exit_code`, `stdout` and `stderr` |
| The command runs past `timeout` | No error. `timed_out` is true and `exit_code` is 124 |
| The output is longer than 1 MiB | No error. The output is cut and `truncated` is true |
| The API key is missing | `An instabox API key is required.` |
| The API key is wrong, expired or revoked | `instabox rejected the API key (<code>). Check it in the plugin settings.` The key is never part of a message |
| The key lacks a permission | `The API key is not allowed to do this (missing_scope): ...` |
| The sandbox does not exist | `Sandbox '<id or name>' not found.` |
| A new sandbox does not become ready in 180 seconds | `Sandbox <name> (<id>) did not become ready: ... It was deleted.` |
| The file to download does not exist | `No file at <path> in sandbox <id or name>.` |
| The path to download is a directory | `<path> is not a file.` |
| The file to download is larger than 30 MiB | `<path> is larger than 30 MiB.` |
| Delete Sandbox on a sandbox the plugin did not create | `Sandbox <name> (<id>) was not created by this plugin (no created-by=dify metadata), so it was not deleted.` |
| A parameter is out of range | A message that names the parameter and the allowed values |

## Limits

| | Limit |
|---|---|
| Command timeout | 1 to 600 seconds, default 120. Dify's own `PLUGIN_MAX_EXECUTION_TIMEOUT` (600 seconds by default) also ends the whole tool call |
| Output | 1 MiB each for `stdout` and `stderr` |
| Download | 30 MiB per file |
| Upload | the file size Dify allows for uploads |
| Delete timer | 60 to 86400 seconds, or 0 for none. A one-off sandbox always gets 900 seconds |
| Sandboxes | your instabox plan's limits apply |

## Connection requirements

The plugin connects to the instabox API at `https://api.instabox.sh` (HTTPS,
port 443). Dify must be able to reach that host. Sandboxes have outbound
internet access.

Upload File reads the file from Dify's file server. On a self-hosted Dify, set
`FILES_URL`, or `INTERNAL_FILES_URL` (for example `http://api:5001` in the Docker
setup), so the plugin can reach it. Without it, Upload File fails with
`Ensure the FILES_URL environment variable is set`.

## How it works

- The plugin is built on the [instabox Python SDK](https://pypi.org/project/instabox-sdk/).
- Run Command runs the command with `bash -lc` in the sandbox.
- Run Code sends the program on stdin to a short shell command that saves it to
  a temporary file in `/tmp`, runs it with the interpreter for the language, and
  removes the file.
- Before it uses a sandbox you name, every tool starts or resumes it if it is
  paused or stopped.
- Every sandbox the plugin creates carries the metadata `created-by=dify`.
  Delete Sandbox deletes only sandboxes with that metadata.

## Security

These tools run any command or code the model or user sends. Use them only with
models and users you trust with a Linux machine.

- The API key stays in Dify. It is never sent into a sandbox.
- With an existing `sandbox_id`, the tools can run commands in any sandbox the
  key can reach. Give Dify a key for a workspace that holds only sandboxes for
  Dify, not your own development sandboxes.
- Delete Sandbox deletes only sandboxes that the plugin created.

## Development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest pyyaml
.venv/bin/pytest tests/test_package.py
INSTABOX_API_KEY=ibk_... .venv/bin/pytest tests
```

`tests/test_package.py` runs offline. `tests/test_live.py` calls every tool
against the real instabox API and is skipped unless `INSTABOX_API_KEY` is set.
It creates sandboxes named `integ-dify-*` and `dify-*` and deletes them again.

To try a change in a running Dify, copy `.env.example` to `.env`, fill in the
debugging URL and key from Dify's **Plugins** page and run
`.venv/bin/python -m main`.

Build the package with the
[Dify plugin CLI](https://github.com/langgenius/dify-plugin-daemon/releases):

```bash
dify-plugin plugin package . -o instabox-0.1.0.difypkg
```

## Links

- [instabox](https://instabox.sh)
- [instabox Python SDK](https://pypi.org/project/instabox-sdk/)
- [Privacy policy](https://github.com/InsForge/instabox-integrations/blob/main/dify/PRIVACY.md)

## License

Apache-2.0
