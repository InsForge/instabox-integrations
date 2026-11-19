import mimetypes
import posixpath
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from instabox import APIError, ConflictError, InstaboxError, NotFoundError

from instabox_sandbox import MAX_DOWNLOAD_BYTES, friendly_error, make_client, required_param, running_sandbox


class DownloadFileTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        sandbox = required_param(tool_parameters, "sandbox_id")
        path = required_param(tool_parameters, "path")
        with make_client(self.runtime.credentials) as client:
            box_id = running_sandbox(client, sandbox).id
            try:
                data = client.files.read_bytes(box_id, path, max_bytes=MAX_DOWNLOAD_BYTES)
            except NotFoundError:
                raise ValueError(f"No file at {path} in sandbox {sandbox}.") from None
            except ConflictError as e:
                if e.code != "not_a_file":
                    raise friendly_error(e) from e
                raise ValueError(f"{path} is not a file.") from None
            except APIError as e:
                if e.code != "file_too_large":
                    raise friendly_error(e) from e
                raise ValueError(f"{path} is larger than {MAX_DOWNLOAD_BYTES // 2**20} MiB.") from None
            except InstaboxError as e:
                raise friendly_error(e) from e
        filename = posixpath.basename(path.rstrip("/")) or "download.bin"
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        yield self.create_blob_message(blob=data, meta={"mime_type": mime_type, "filename": filename})
        yield self.create_text_message(f"Downloaded {len(data)} bytes from {path}.")
        yield self.create_json_message(
            {"sandbox_id": box_id, "path": path, "size": len(data), "filename": filename, "mime_type": mime_type}
        )
