from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from instabox_sandbox import (
    exec_messages,
    make_client,
    required_param,
    run,
    sandbox_for_call,
    text_param,
    timeout_param,
)


class RunCommandTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        command = required_param(tool_parameters, "command")
        cwd = text_param(tool_parameters, "cwd") or None
        stdin = str(tool_parameters.get("stdin") or "") or None
        timeout = timeout_param(tool_parameters)
        sandbox = text_param(tool_parameters, "sandbox_id")
        with make_client(self.runtime.credentials) as client:
            with sandbox_for_call(client, sandbox) as (box_id, one_off):
                result = run(client, box_id, command, cwd=cwd, stdin=stdin, timeout=timeout)
        result.update(sandbox_id=box_id, one_off=one_off)
        yield from exec_messages(self, result)
