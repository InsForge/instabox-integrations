from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from instabox import InstaboxError

from instabox_sandbox import friendly_error, make_client, required_param, running_sandbox, text_param


class UploadFileTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        sandbox = required_param(tool_parameters, "sandbox_id")
        file = tool_parameters.get("file")
        if file is None or not hasattr(file, "blob"):
            raise ValueError("file is required.")
        filename = getattr(file, "filename", None) or "upload" + (getattr(file, "extension", None) or "")
        path = text_param(tool_parameters, "path")
        if not path or path.endswith("/"):
            path += filename
        data = file.blob
        with make_client(self.runtime.credentials) as client:
            box_id = running_sandbox(client, sandbox).id
            try:
                written = client.files.write(box_id, path, data)
            except InstaboxError as e:
                raise friendly_error(e) from e
        result = {"sandbox_id": box_id, "path": written.path, "size": written.size, "sha256": written.sha256}
        yield self.create_text_message(f"Uploaded {written.size} bytes to {written.path}.")
        yield self.create_json_message(result)
        yield self.create_variable_message("path", written.path)
