# langchain-instabox

Run [Deep Agents](https://github.com/langchain-ai/deepagents) in [instabox](https://instabox.sh)
sandboxes: cloud Linux machines for agents. The agent's shell commands and file tools run in
the sandbox, not on your machine.

```sh
pip install langchain-instabox
export INSTABOX_API_KEY=ibk_...
```

Create the key on **API keys** in the [dashboard](https://dashboard.instabox.sh). It needs
`machines:read`, `machines:write` and `machines:delete`. Add `images:read` to start sandboxes
from snapshots.

## Use it as a Deep Agents backend

```python
from deepagents import create_deep_agent
from instabox import Client

from langchain_instabox import InstaboxSandbox

client = Client()  # reads INSTABOX_API_KEY
machine = client.machines.create(delete_after_seconds=3600)
prompt = "Write and run a Python script that prints the first 10 primes"
try:
    backend = InstaboxSandbox(client=client, machine=machine)
    agent = create_deep_agent(model="anthropic:claude-sonnet-4-5", backend=backend)
    agent.invoke({"messages": [{"role": "user", "content": prompt}]})
finally:
    client.machines.delete(machine.id, confirm=machine.id)
```

`InstaboxSandbox(client=..., machine=..., timeout=1800)` wraps a sandbox you already have:
pass a `Machine` or its ID or name. Commands run with `bash -lc` as the sandbox user, starting
in `/data/home`.

- A command runs for at most 600 seconds. A longer `timeout` is capped at 600, and a command
  that runs out of time exits with 124 and a note in its output.
- Output is capped at 1 MiB per command. Longer output comes back with `truncated=True`.
- A sandbox pauses after 30 idle minutes by default. The next command resumes it first.
- File paths must be absolute. Writing a file creates missing parent directories.

## Use it from Deep Agents Code

Installing the package registers an `instabox` sandbox provider for the `dcode` CLI:

```sh
dcode install langchain-instabox --package
dcode --sandbox instabox
```

`dcode` creates a sandbox, runs the session in it and deletes it at the end. To keep working in
one sandbox across sessions, pass its ID or name with `--sandbox-id`: `dcode` then leaves it
alone when the session ends, and resumes it if it was paused. `--sandbox-snapshot NAME` starts
the new sandbox from one of your snapshots (by ID or name).

Settings, as environment variables (each also works with a `DEEPAGENTS_CODE_` prefix):

| Variable | Meaning |
|---|---|
| `INSTABOX_API_KEY` | API key (required) |
| `INSTABOX_TEMPLATE` | Template for new sandboxes, such as `universal` or `minimal` (default: instabox's default template) |
| `INSTABOX_DELETE_AFTER` | Seconds (60 to 86400) after which instabox deletes a new sandbox, even if `dcode` exits without cleaning up |
| `INSTABOX_API_URL` | API server (default `https://api.instabox.sh`) |

The same settings can go in `~/.deepagents/config.toml` as provider params: `template`, `name`,
`metadata`, `delete_after` and `timeout`.

## Tests

```sh
pip install -e . pytest pytest-asyncio pytest-timeout langchain-tests
pytest tests/unit_tests
INSTABOX_API_KEY=ibk_... pytest tests/integration_tests  # creates and deletes a sandbox
```

The integration tests run Deep Agents' standard sandbox suite (`SandboxIntegrationTests`).

## License

Apache-2.0
