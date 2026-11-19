from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from instabox import InstaboxError, NotFoundError

from instabox_sandbox import CREATED_BY, friendly_error, make_client, required_param


class DeleteSandboxTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        sandbox = required_param(tool_parameters, "sandbox_id")
        key, value = CREATED_BY
        with make_client(self.runtime.credentials) as client:
            try:
                box = client.machines.get(sandbox)
                # Only sandboxes this plugin made, so an agent cannot delete your other sandboxes.
                if (box.metadata or {}).get(key) != value:
                    raise ValueError(
                        f"Sandbox {box.name} ({box.id}) was not created by this plugin "
                        f"(no {key}={value} metadata), so it was not deleted."
                    )
                client.machines.delete(box.id, confirm=box.id)
            except NotFoundError:
                raise ValueError(f"Sandbox '{sandbox}' not found.") from None
            except InstaboxError as e:
                raise friendly_error(e) from e
        yield self.create_text_message(f"Deleted sandbox {box.name} ({box.id}).")
        yield self.create_json_message({"deleted": True, "sandbox_id": box.id, "name": box.name})
