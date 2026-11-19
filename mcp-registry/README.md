# MCP Registry entry

[`server.json`](server.json) lists instabox's hosted MCP server
(`https://api.instabox.sh/mcp`, Streamable HTTP) in the official
[MCP Registry](https://registry.modelcontextprotocol.io). Clients that read the registry show
it as `io.github.InsForge/instabox` and ask the user for an API key, sent as `X-API-Key`.

The server has 12 tools: `list_machines`, `get_machine`, `create_machine`, `exec`, `read_file`,
`write_file`, `suspend_machine`, `resume_machine`, `save_snapshot`, `list_snapshots`,
`delete_machine` and `get_usage`. See the [MCP guide](https://instabox.sh/docs/mcp).

## Publish

1. Install the publisher: `brew install mcp-publisher`, or a release binary from
   [modelcontextprotocol/registry](https://github.com/modelcontextprotocol/registry/releases).
2. From this directory, sign in with a GitHub account in the InsForge organization:
   `mcp-publisher login github`.
3. Publish: `mcp-publisher publish`.
4. Check it: `curl "https://registry.modelcontextprotocol.io/v0/servers?search=instabox"`.

To publish a change, raise `version` in `server.json` first. The registry refuses a version it
already has.
