# instabox integrations

Adapters that let agent frameworks run their tools in [instabox](https://instabox.sh) sandboxes:
cloud Linux machines for agents, built on the
[`instabox-sdk`](https://pypi.org/project/instabox-sdk/) Python SDK.

| Directory | Package | What it plugs into |
|---|---|---|
| [`langchain/`](langchain/) | `langchain-instabox` | [Deep Agents](https://github.com/langchain-ai/deepagents): a sandbox backend, and a provider for the `dcode` CLI |
| [`hermes/`](hermes/) | `instabox-hermes` | [Hermes Agent](https://github.com/NousResearch/hermes-agent): a terminal backend plugin |
| [`dify/`](dify/) | `instabox` Dify plugin | [Dify](https://github.com/langgenius/dify): tools that run commands and code and move files |
| [`mcp-registry/`](mcp-registry/) | `server.json` | The [MCP Registry](https://registry.modelcontextprotocol.io) entry for the hosted instabox MCP server |

Every adapter needs an instabox API key. Create one on **API keys** in the
[dashboard](https://dashboard.instabox.sh) and set it as `INSTABOX_API_KEY`.

## License

Apache-2.0
