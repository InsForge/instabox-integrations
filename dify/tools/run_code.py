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

# Language: (file extension, command that runs the file).
RUNNERS = {
    "python": ("py", "python3"),
    "javascript": ("js", "node"),
    "typescript": ("ts", "bun run"),
    "bash": ("sh", "bash"),
    "go": ("go", "go run"),
    "java": ("java", "java"),
}


def runner_command(language: str) -> str:
    """Shell command that saves stdin to a temp file, runs it and removes it."""
    ext, runner = RUNNERS[language]
    return (
        f'f=$(mktemp /tmp/dify-code-XXXXXXXX.{ext}) && cat > "$f" && '
        f'{{ {runner} "$f" </dev/null; rc=$?; rm -f -- "$f"; exit $rc; }}'
    )


class RunCodeTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        required_param(tool_parameters, "code")
        code = str(tool_parameters["code"])  # sent as written, not stripped
        language = (text_param(tool_parameters, "language") or "python").lower()
        if language not in RUNNERS:
            raise ValueError(f"language must be one of {', '.join(RUNNERS)}, got {language!r}.")
        timeout = timeout_param(tool_parameters)
        sandbox = text_param(tool_parameters, "sandbox_id")
        with make_client(self.runtime.credentials) as client:
            with sandbox_for_call(client, sandbox) as (box_id, one_off):
                result = run(client, box_id, runner_command(language), stdin=code, timeout=timeout)
        result.update(language=language, sandbox_id=box_id, one_off=one_off)
        yield from exec_messages(self, result)
