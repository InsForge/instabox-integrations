from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from instabox_sandbox import (
    MAX_DELETE_AFTER,
    MIN_DELETE_AFTER,
    create_sandbox,
    int_param,
    make_client,
    text_param,
)

DEFAULT_DELETE_AFTER = 3600


class CreateSandboxTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        name = text_param(tool_parameters, "name") or None
        template = text_param(tool_parameters, "template") or None
        delete_after = int_param(tool_parameters, "delete_after_seconds", DEFAULT_DELETE_AFTER, 0, MAX_DELETE_AFTER)
        if 0 < delete_after < MIN_DELETE_AFTER:
            raise ValueError(f"delete_after_seconds must be 0 or at least {MIN_DELETE_AFTER}.")
        with make_client(self.runtime.credentials) as client:
            box = create_sandbox(client, name=name, template=template, delete_after_seconds=delete_after)
        result = {
            "sandbox_id": box.id,
            "name": box.name,
            "template": box.template_id,
            "delete_at": box.delete_at,
        }
        yield self.create_text_message(f"Created sandbox {box.name} ({box.id}).")
        yield self.create_json_message(result)
        yield self.create_variable_message("sandbox_id", box.id)
