# Listings

Where each adapter gets listed once it is published, and what to submit.

## LangChain docs (sandboxes)

Open an issue with the
[integration submission form](https://github.com/langchain-ai/docs/issues/new?template=06-integration-submission.yml)
after `langchain-instabox` is on PyPI. A bot opens the docs PR.

| Field | Value |
|---|---|
| Display or class name | `InstaboxSandbox` |
| Language | Python |
| Component type | sandboxes |
| PyPI package name | `langchain-instabox` |
| Docs URL | https://github.com/InsForge/instabox-integrations/tree/main/langchain#readme |
| Source repository | InsForge/instabox-integrations |
| Short provider description | Run Deep Agents in instabox sandboxes: cloud Linux machines for agents that pause when idle and resume on the next command. |
| Capability notes | `sandbox_id: true`, `snapshot_name: true`, `dcode_provider: true` |

## Hermes Agent plugin catalog

Add [`hermes/plugin-catalog-entry.yaml`](hermes/plugin-catalog-entry.yaml) as
`plugin-catalog/instabox.yaml` in NousResearch/hermes-agent, with `sha` set to the commit that
holds the released plugin.

## Dify marketplace

Package `dify/` with `dify plugin package`, then open a PR to langgenius/dify-plugins that adds
the `.difypkg` under the author's directory. Fill in the PR template there.

## MCP Registry

See [`mcp-registry/README.md`](mcp-registry/README.md).
