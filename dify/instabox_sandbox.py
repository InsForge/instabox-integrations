"""Shared helpers for the instabox tools."""

from __future__ import annotations

import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from instabox import (
    APIConnectionError,
    AuthenticationError,
    Client,
    ConflictError,
    InstaboxError,
    Machine,
    NotFoundError,
    PermissionDeniedError,
    WaitTimeoutError,
)

NAME_PREFIX = "dify-"
CREATED_BY = ("created-by", "dify")
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
READY_TIMEOUT = 180
# Deletes a one-off sandbox even if the plugin dies before its own delete.
ONE_OFF_DELETE_AFTER = 900
MIN_DELETE_AFTER = 60
MAX_DELETE_AFTER = 86400
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
HOME = "/data/home"


def make_client(credentials: Mapping[str, Any]) -> Client:
    # Checked here so an empty key never falls back to INSTABOX_API_KEY.
    api_key = str(credentials.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("An instabox API key is required.")
    return Client(api_key=api_key)


def friendly_error(err: Exception) -> ValueError:
    if isinstance(err, AuthenticationError):
        return ValueError(f"instabox rejected the API key ({err.code}). Check it in the plugin settings.")
    if isinstance(err, PermissionDeniedError):
        return ValueError(f"The API key is not allowed to do this ({err.code}): {err.message}")
    if isinstance(err, WaitTimeoutError):
        return ValueError(f"instabox did not finish in time: {err}")
    if isinstance(err, APIConnectionError):
        return ValueError(f"Could not reach the instabox API: {err}")
    return ValueError(f"instabox request failed: {err}")


def text_param(params: Mapping[str, Any], name: str) -> str:
    return str(params.get(name) or "").strip()


def required_param(params: Mapping[str, Any], name: str) -> str:
    value = text_param(params, name)
    if not value:
        raise ValueError(f"{name} is required.")
    return value


def int_param(params: Mapping[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = params.get(name)
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number, got {value!r}.") from None
    if not number.is_integer():
        raise ValueError(f"{name} must be a whole number, got {value!r}.")
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low} and {high}, got {int(number)}.")
    return int(number)


def timeout_param(params: Mapping[str, Any]) -> int:
    return int_param(params, "timeout", DEFAULT_TIMEOUT, 1, MAX_TIMEOUT)


def new_name() -> str:
    return NAME_PREFIX + secrets.token_hex(4)


def create_sandbox(
    client: Client,
    *,
    name: str | None = None,
    template: str | None = None,
    delete_after_seconds: int | None = None,
) -> Machine:
    try:
        box = client.machines.create(
            name or new_name(),
            template=template or None,
            metadata=dict([CREATED_BY]),
            delete_after_seconds=delete_after_seconds or None,
            wait=False,
        )
    except InstaboxError as e:
        raise friendly_error(e) from e
    try:
        return client.machines.wait_ready(box.id, timeout=READY_TIMEOUT)
    except BaseException as e:
        # The caller never sees the id, so a sandbox that fails to start is removed here.
        try:
            client.machines.delete(box.id, confirm=box.id, wait=False)
            fate = "It was deleted."
        except Exception:
            fate = f"Deleting it failed too. Delete {box.id} yourself."
        if not isinstance(e, Exception):
            raise
        raise ValueError(f"Sandbox {box.name} ({box.id}) did not become ready: {e}. {fate}") from e


def running_sandbox(client: Client, sandbox: str) -> Machine:
    try:
        return client.machines.ensure_running(sandbox, timeout=READY_TIMEOUT)
    except NotFoundError:
        raise ValueError(f"Sandbox '{sandbox}' not found.") from None
    except InstaboxError as e:
        raise friendly_error(e) from e


def delete_quietly(client: Client, box_id: str) -> None:
    try:
        client.machines.delete(box_id, confirm=box_id, wait=False)
    except Exception:
        pass  # the delete-after timer removes it anyway


@contextmanager
def sandbox_for_call(client: Client, sandbox: str) -> Iterator[tuple[str, bool]]:
    """Yield (sandbox id, one-off): the given sandbox, or a new one deleted afterwards."""
    if sandbox:
        yield running_sandbox(client, sandbox).id, False
        return
    box = create_sandbox(client, delete_after_seconds=ONE_OFF_DELETE_AFTER)
    try:
        yield box.id, True
    finally:
        delete_quietly(client, box.id)


def run(
    client: Client,
    box_id: str,
    command: str,
    *,
    cwd: str | None = None,
    stdin: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    def send() -> Any:
        return client.machines.exec(box_id, command, cwd=cwd, stdin=stdin, timeout=timeout)

    try:
        try:
            result = send()
        except ConflictError as e:
            # Paused again since ensure_running: the command never started, so run it once more.
            if e.code != "machine_not_running":
                raise
            client.machines.ensure_running(box_id, timeout=READY_TIMEOUT)
            result = send()
    except NotFoundError:
        raise ValueError(f"Sandbox '{box_id}' not found.") from None
    except InstaboxError as e:
        raise friendly_error(e) from e
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "truncated": result.truncated,
    }


def format_output(result: Mapping[str, Any]) -> str:
    parts = []
    if result["stdout"]:
        parts.append(result["stdout"].rstrip("\n"))
    if result["stderr"]:
        parts.append("[stderr]\n" + result["stderr"].rstrip("\n"))
    if result["timed_out"]:
        parts.append("[timed out]")
    if result["truncated"]:
        parts.append("[output truncated]")
    parts.append(f"[exit code {result['exit_code']}]")
    return "\n".join(parts)


def exec_messages(tool: Tool, result: Mapping[str, Any]) -> Iterator[ToolInvokeMessage]:
    yield tool.create_text_message(format_output(result))
    yield tool.create_json_message(dict(result))
    yield tool.create_variable_message("stdout", result["stdout"])
    yield tool.create_variable_message("stderr", result["stderr"])
    yield tool.create_variable_message("exit_code", result["exit_code"])
