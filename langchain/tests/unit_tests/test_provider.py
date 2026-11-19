from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("deepagents_code")

from deepagents_code.integrations.sandbox_provider import SandboxNotFoundError  # noqa: E402

from langchain_instabox.provider import InstaboxProvider  # noqa: E402
from langchain_instabox.sandbox import DEFAULT_TIMEOUT, InstaboxSandbox  # noqa: E402
from tests.unit_tests.fakes import FakeClient  # noqa: E402


def _provider(client: FakeClient, env: dict[str, str] | None = None) -> InstaboxProvider:
    values = env or {}
    return InstaboxProvider(client=client, resolve_env_var=values.get)  # type: ignore[arg-type]


def _create_kwargs(client: FakeClient) -> dict[str, object]:
    [(name, (machine_name, kwargs))] = [c for c in client.machines.calls if c[0] == "create"]
    assert name == "create"
    return {"name": machine_name, **kwargs}


def test_metadata() -> None:
    meta = InstaboxProvider().metadata
    assert meta.name == "instabox"
    assert meta.working_dir == "/data/home"
    assert meta.supports_sandbox_id is True
    assert meta.supports_snapshot_name is True
    assert meta.install is not None
    assert meta.install.name == "langchain-instabox"


def test_missing_key_is_a_clear_error() -> None:
    provider = InstaboxProvider(resolve_env_var=lambda _name: None)
    with pytest.raises(ValueError, match="INSTABOX_API_KEY"):
        provider.get_or_create()


def test_create_uses_env_defaults() -> None:
    client = FakeClient()
    env = {"INSTABOX_TEMPLATE": "universal", "INSTABOX_DELETE_AFTER": "3600"}
    sandbox = _provider(client, env).get_or_create(name="dev", metadata={"k": "v"})
    assert isinstance(sandbox, InstaboxSandbox)
    assert sandbox.id == "m_new"
    kwargs = _create_kwargs(client)
    assert kwargs["name"] == "dev"
    assert kwargs["template"] == "universal"
    assert kwargs["delete_after_seconds"] == 3600
    assert kwargs["metadata"] == {"k": "v"}
    assert kwargs["snapshot_id"] is None


def test_explicit_args_beat_env() -> None:
    client = FakeClient()
    env = {"INSTABOX_TEMPLATE": "universal", "INSTABOX_DELETE_AFTER": "3600"}
    _provider(client, env).get_or_create(template="minimal", delete_after=120)
    kwargs = _create_kwargs(client)
    assert kwargs["template"] == "minimal"
    assert kwargs["delete_after_seconds"] == 120


def test_timeout_param_sets_the_default_command_timeout() -> None:
    client = FakeClient()
    assert _provider(client).get_or_create()._default_timeout == DEFAULT_TIMEOUT  # noqa: SLF001
    assert _provider(client).get_or_create(timeout="90")._default_timeout == 90  # noqa: SLF001


def test_bad_numbers_are_rejected() -> None:
    with pytest.raises(ValueError, match="delete_after"):
        _provider(FakeClient()).get_or_create(delete_after="soon")


def test_unknown_kwargs_are_rejected() -> None:
    with pytest.raises(TypeError, match="image"):
        _provider(FakeClient()).get_or_create(image="ubuntu")


def test_reattach_starts_the_sandbox() -> None:
    client = FakeClient()
    client.machines.running = False
    sandbox = _provider(client).get_or_create(sandbox_id="m_1")
    assert sandbox.id == "m_1"
    assert ("ensure_running", "m_1") in client.machines.calls
    assert not [c for c in client.machines.calls if c[0] == "create"]


def test_reattach_to_a_missing_sandbox() -> None:
    with pytest.raises(SandboxNotFoundError):
        _provider(FakeClient()).get_or_create(sandbox_id="m_gone")


def test_snapshot_with_sandbox_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="new sandbox"):
        _provider(FakeClient()).get_or_create(sandbox_id="m_1", snapshot="s")


def test_snapshot_by_id_and_by_name() -> None:
    client = FakeClient()
    client.snapshots.snaps = [
        SimpleNamespace(id="snap_1", name="base", status="ready"),
        SimpleNamespace(id="snap_2", name="wip", status="pending"),
    ]
    provider = _provider(client)
    provider.get_or_create(snapshot="snap_1")
    provider.get_or_create(snapshot="base")
    ids = [args[1]["snapshot_id"] for name, args in client.machines.calls if name == "create"]
    assert ids == ["snap_1", "snap_1"]
    with pytest.raises(ValueError, match="not ready"):
        provider.get_or_create(snapshot="wip")
    with pytest.raises(ValueError, match="No instabox snapshot"):
        provider.get_or_create(snapshot="nope")
    with pytest.raises(ValueError, match="either snapshot or template"):
        provider.get_or_create(snapshot="base", template="universal")


def test_duplicate_snapshot_names_need_an_id() -> None:
    client = FakeClient()
    client.snapshots.snaps = [
        SimpleNamespace(id="snap_1", name="base", status="ready"),
        SimpleNamespace(id="snap_2", name="base", status="ready"),
    ]
    with pytest.raises(ValueError, match="Several"):
        _provider(client).get_or_create(snapshot="base")


def test_delete_confirms_with_the_same_ref() -> None:
    client = FakeClient()
    _provider(client).delete(sandbox_id="m_1")
    assert ("delete", ("m_1", "m_1")) in client.machines.calls
    with pytest.raises(SandboxNotFoundError):
        _provider(client).delete(sandbox_id="m_gone")
