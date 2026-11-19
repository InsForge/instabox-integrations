"""instabox sandbox backend for Deep Agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from instabox import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)

if TYPE_CHECKING:
    from instabox import Client, Machine

DEFAULT_TIMEOUT = 30 * 60
"""Default command timeout in seconds, as in the other Deep Agents sandboxes."""

MAX_EXEC_TIMEOUT = 600
"""Longest single command instabox runs in the foreground, in seconds."""

DEFAULT_WORKDIR = "/data/home"
"""HOME of the sandbox user, where commands start."""


class InstaboxSandbox(BaseSandbox):
    """Sandbox backend that runs on an instabox sandbox.

    Commands run with `bash -lc` as the sandbox user, in `/data/home`. A
    command runs for at most 600 seconds: a longer `timeout` is capped there.
    A sandbox paused while idle is resumed before the next command.
    """

    def __init__(
        self,
        *,
        client: Client,
        machine: Machine | str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Wrap an existing instabox sandbox.

        Args:
            client: An `instabox.Client`.
            machine: The sandbox, or its ID or name.
            timeout: Default command timeout in seconds.
        """
        self._client = client
        self._machine_id = machine if isinstance(machine, str) else machine.id
        self._default_timeout = timeout

    @property
    def id(self) -> str:
        """Return the sandbox ID."""
        return self._machine_id

    @property
    def client(self) -> Client:
        """Return the `instabox.Client` this backend uses."""
        return self._client

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run a shell command in the sandbox.

        Args:
            command: Shell command string.
            timeout: Seconds to wait, at most 600. `None` uses the default.

        Returns:
            `ExecuteResponse` with stdout and stderr combined.
        """
        requested = self._default_timeout if timeout is None else timeout
        # 0 means no limit elsewhere: here it is the longest instabox allows.
        effective = MAX_EXEC_TIMEOUT if requested <= 0 else min(requested, MAX_EXEC_TIMEOUT)
        try:
            result = self._client.machines.exec(self._machine_id, command, timeout=effective)
        except ConflictError as e:
            if e.code != "machine_not_running":
                raise
            # Nothing ran: resume the paused or stopped sandbox and run it once.
            self._client.machines.ensure_running(self._machine_id)
            result = self._client.machines.exec(self._machine_id, command, timeout=effective)

        output = result.stdout or ""
        if result.stderr:
            output += "\n" + result.stderr if output else result.stderr
        if result.timed_out:
            note = f"[command timed out after {effective} seconds]"
            output += "\n" + note if output else note
        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=bool(result.truncated),
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write files into the sandbox, creating missing parent directories."""
        return [self._write_file(path, content) for path, content in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read files from the sandbox."""
        return [self._read_file(path) for path in paths]

    def _write_file(self, path: str, content: bytes) -> FileUploadResponse:
        if not path.startswith("/"):
            return FileUploadResponse(path=path, error="invalid_path")
        try:
            self._client.files.write(self._machine_id, path, content)
        except PermissionDeniedError:
            return FileUploadResponse(path=path, error="permission_denied")
        except NotFoundError:
            return FileUploadResponse(path=path, error="file_not_found")
        except ConflictError as e:
            if e.code != "not_a_file":
                raise
            return FileUploadResponse(path=path, error="is_directory")
        return FileUploadResponse(path=path, error=None)

    def _read_file(self, path: str) -> FileDownloadResponse:
        if not path.startswith("/"):
            return FileDownloadResponse(path=path, content=None, error="invalid_path")
        try:
            content = self._client.files.read_bytes(self._machine_id, path)
        except PermissionDeniedError:
            return FileDownloadResponse(path=path, content=None, error="permission_denied")
        except NotFoundError:
            return FileDownloadResponse(path=path, content=None, error="file_not_found")
        except ConflictError as e:
            if e.code != "not_a_file":
                raise
            return FileDownloadResponse(path=path, content=None, error="is_directory")
        return FileDownloadResponse(path=path, content=content, error=None)
