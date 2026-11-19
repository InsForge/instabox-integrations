"""The `instabox` provider through Deep Agents Code's own factory, on live sandboxes."""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("deepagents_code")

from deepagents_code.integrations.sandbox_factory import create_sandbox  # noqa: E402
from deepagents_code.integrations.sandbox_registry import SandboxRegistry  # noqa: E402
from instabox import Client, NotFoundError  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTABOX_API_KEY"), reason="INSTABOX_API_KEY is not set"
)


def _gone(client: Client, machine_id: str) -> bool:
    try:
        machine = client.machines.get(machine_id)
    except NotFoundError:
        return True
    return machine.state in {"deleting", "deleted"}


def test_registry_discovers_the_entry_point() -> None:
    registry = SandboxRegistry.load()
    assert "instabox" in registry.available_providers()
    meta = registry.get_metadata("instabox")
    assert meta is not None
    assert meta.working_dir == "/data/home"
    assert meta.supports_snapshot_name is True


def test_dcode_creates_uses_and_deletes_a_sandbox() -> None:
    name = f"integ-langchain-dcode-{uuid.uuid4().hex[:6]}"
    params = {"name": name, "delete_after": 3600}
    with create_sandbox("instabox", params=params) as backend:
        machine_id = backend.id
        result = backend.execute("pwd && echo hello")
        assert result.exit_code == 0
        assert result.output.splitlines() == ["/data/home", "hello"]
        assert backend.write("/data/home/notes/a.txt", "one\n").error is None
        assert backend.read("/data/home/notes/a.txt").error is None
    assert _gone(Client(), machine_id)


def test_dcode_reattaches_to_a_paused_sandbox_and_leaves_it() -> None:
    client = Client()
    machine = client.machines.create(
        f"integ-langchain-keep-{uuid.uuid4().hex[:6]}", delete_after_seconds=3600
    )
    try:
        client.files.write(machine.id, "/data/home/kept.txt", "still here\n")
        client.machines.pause(machine.id)
        with create_sandbox("instabox", sandbox_id=machine.name) as backend:
            result = backend.execute("cat /data/home/kept.txt")
            assert result.output.strip() == "still here"
        # dcode did not create it, so it must not delete it.
        assert client.machines.get(machine.id).state == "running"
    finally:
        client.machines.delete(machine.id, confirm=machine.id)
