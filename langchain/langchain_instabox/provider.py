"""instabox sandbox provider for Deep Agents Code.

Registered under the `deepagents_code.sandbox_providers` entry point, so
`dcode --sandbox instabox` creates (or reattaches to) an instabox sandbox.
Importing this module needs `deepagents-code`. `langchain_instabox` itself
does not.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from deepagents_code.integrations.sandbox_provider import (
    SandboxInstallHint,
    SandboxNotFoundError,
    SandboxProvider,
    SandboxProviderMetadata,
)
from instabox import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    Client,
    NotFoundError,
    PermissionDeniedError,
)

from langchain_instabox.sandbox import DEFAULT_TIMEOUT, DEFAULT_WORKDIR, InstaboxSandbox

EnvResolver = Callable[[str], str | None]


def _environ_resolve_env_var(name: str) -> str | None:
    if not name.startswith("DEEPAGENTS_CODE_"):
        prefixed = f"DEEPAGENTS_CODE_{name}"
        if prefixed in os.environ:
            return os.environ[prefixed] or None
    return os.environ.get(name) or None


def _default_resolve_env_var(name: str) -> str | None:
    # dcode's resolver also sees the active workspace's variables.
    try:
        from deepagents_code.model_config import resolve_env_var  # noqa: PLC0415
    except ImportError:
        return _environ_resolve_env_var(name)
    return resolve_env_var(name)


def _optional_int(value: int | str | None, *, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        msg = f"{name} must be a whole number, got {value!r}"
        raise ValueError(msg) from e


class InstaboxProvider(SandboxProvider):
    """Create, reattach to and delete instabox sandboxes for Deep Agents Code."""

    _metadata = SandboxProviderMetadata(
        name="instabox",
        working_dir=DEFAULT_WORKDIR,
        install=SandboxInstallHint(kind="package", name="langchain-instabox"),
        supports_sandbox_id=True,
        supports_snapshot_name=True,
        backend_module="langchain_instabox",
    )

    def __init__(
        self,
        *,
        client: Client | None = None,
        resolve_env_var: EnvResolver | None = None,
    ) -> None:
        """Set up the provider. Credentials are read on first use.

        Args:
            client: An `instabox.Client` to use instead of one built from
                `INSTABOX_API_KEY`.
            resolve_env_var: Environment lookup for provider settings.
        """
        self._client = client
        self._resolve_env_var = resolve_env_var or _default_resolve_env_var

    @property
    def metadata(self) -> SandboxProviderMetadata:
        """Return the metadata Deep Agents Code shows for this provider."""
        return self._metadata

    def _get_client(self) -> Client:
        if self._client is None:
            api_key = self._resolve_env_var("INSTABOX_API_KEY")
            if not api_key:
                msg = (
                    "No instabox API key found. Set INSTABOX_API_KEY "
                    "(or DEEPAGENTS_CODE_INSTABOX_API_KEY)."
                )
                raise ValueError(msg)
            self._client = Client(
                api_key=api_key,
                base_url=self._resolve_env_var("INSTABOX_API_URL"),
            )
        return self._client

    def get_or_create(  # noqa: PLR0913
        self,
        *,
        sandbox_id: str | None = None,
        snapshot: str | None = None,
        template: str | None = None,
        name: str | None = None,
        metadata: Mapping[str, str] | None = None,
        delete_after: int | str | None = None,
        timeout: int | str | None = None,
        **kwargs: object,
    ) -> InstaboxSandbox:
        """Reattach to an instabox sandbox, or create one.

        Args:
            sandbox_id: An existing sandbox's ID or name, or `None` to create
                one. A paused or stopped sandbox is started.
            snapshot: Snapshot ID or name to create the sandbox from.
            template: Template for a new sandbox. Defaults to
                `INSTABOX_TEMPLATE`, then to instabox's default template.
            name: Name for a new sandbox. instabox picks one if omitted.
            metadata: Labels for a new sandbox.
            delete_after: Seconds (60 to 86400) after which instabox deletes a
                new sandbox. Defaults to `INSTABOX_DELETE_AFTER`, else never.
            timeout: Default command timeout in seconds.
            **kwargs: Unsupported.

        Returns:
            The sandbox backend, running and ready.

        Raises:
            TypeError: If unsupported keyword arguments are passed.
            ValueError: If `snapshot` is combined with `sandbox_id` or with
                `template`, or names no ready snapshot.
            SandboxNotFoundError: If `sandbox_id` names no sandbox.
            RuntimeError: If instabox rejects the key or is unreachable.
        """
        if kwargs:
            msg = f"Received unsupported arguments: {sorted(kwargs)}"
            raise TypeError(msg)
        command_timeout = _optional_int(timeout, name="timeout") or DEFAULT_TIMEOUT
        client = self._get_client()

        try:
            if sandbox_id is not None:
                if snapshot is not None:
                    msg = "snapshot applies only to a new sandbox, not with sandbox_id"
                    raise ValueError(msg)
                try:
                    machine = client.machines.ensure_running(sandbox_id)
                except NotFoundError as e:
                    raise SandboxNotFoundError(sandbox_id) from e
                return InstaboxSandbox(client=client, machine=machine, timeout=command_timeout)

            template = template or self._resolve_env_var("INSTABOX_TEMPLATE")
            snapshot_id = None
            if snapshot is not None:
                if template is not None:
                    msg = "Pass either snapshot or template, not both"
                    raise ValueError(msg)
                snapshot_id = _resolve_snapshot(client, snapshot)
            delete_after_seconds = _optional_int(
                delete_after
                if delete_after is not None
                else self._resolve_env_var("INSTABOX_DELETE_AFTER"),
                name="delete_after",
            )
            machine = client.machines.create(
                name,
                template=template,
                snapshot_id=snapshot_id,
                metadata=dict(metadata) if metadata else None,
                delete_after_seconds=delete_after_seconds,
            )
        except (AuthenticationError, PermissionDeniedError) as e:
            msg = f"instabox rejected the request; check INSTABOX_API_KEY and its scopes: {e}"
            raise RuntimeError(msg) from e
        except APIConnectionError as e:
            msg = f"instabox API unreachable (safe to retry): {e}"
            raise RuntimeError(msg) from e
        return InstaboxSandbox(client=client, machine=machine, timeout=command_timeout)

    def delete(self, *, sandbox_id: str, **kwargs: object) -> None:  # noqa: ARG002
        """Delete a sandbox by ID or name.

        Raises:
            SandboxNotFoundError: If `sandbox_id` names no sandbox.
        """
        try:
            self._get_client().machines.delete(sandbox_id, confirm=sandbox_id)
        except NotFoundError as e:
            raise SandboxNotFoundError(sandbox_id) from e


def _resolve_snapshot(client: Client, ref: str) -> str:
    """Return the ID of the ready snapshot with this ID or name."""
    try:
        snap = client.snapshots.get(ref)
    except (NotFoundError, BadRequestError):
        matches = [s for s in client.snapshots.list() if s.name == ref]
        if not matches:
            msg = f"No instabox snapshot has the ID or name {ref!r}"
            raise ValueError(msg) from None
        if len(matches) > 1:
            msg = f"Several instabox snapshots are named {ref!r}: pass its ID instead"
            raise ValueError(msg) from None
        snap = matches[0]
    if snap.status != "ready":
        msg = f"instabox snapshot {ref!r} is {snap.status}, not ready"
        raise ValueError(msg)
    return snap.id
