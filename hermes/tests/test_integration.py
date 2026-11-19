"""Opt-in tests against the real instabox API: run `pytest -m integration` with INSTABOX_API_KEY set.

They create real boxes, billed to the key's account, named ``hermes-integ-...`` with a one-hour delete timer,
and delete every box they made when they finish."""

import contextlib
import hashlib
import time
import uuid

import pytest

from instabox_hermes_plugin import instabox_env
from instabox_hermes_plugin.provider import DEFAULT_API_URL

pytestmark = pytest.mark.integration
PREFIX = "hermes-integ"


@pytest.fixture
def real(real_key, monkeypatch):
    """``(factory, admin client)``: environments on the real API, every box they made deleted afterwards."""
    from instabox import Client

    key, url = real_key
    monkeypatch.setattr("tools.credential_files.get_credential_file_mounts", list)
    monkeypatch.setattr(instabox_env, "iter_sync_files", lambda base: [])
    admin = Client(api_key=key, base_url=url or DEFAULT_API_URL)
    made = []

    def factory(**kwargs):
        kwargs.setdefault("task_id", f"it-{uuid.uuid4().hex[:8]}")
        env = instabox_env.InstaboxEnvironment(
            client=Client(api_key=key, base_url=url or DEFAULT_API_URL),
            timeout=120,
            name_prefix=PREFIX,
            delete_after_seconds=3600,
            **kwargs,
        )
        made.append(env)
        return env

    yield factory, admin
    for env in made:
        env.cleanup()
    names = {env.machine_name for env in made}
    for machine in admin.machines.list():
        if machine.name.startswith(PREFIX) and machine.name in names:
            with contextlib.suppress(Exception):
                admin.machines.delete(machine.id, confirm=machine.id)
    admin.close()


def test_non_persistent_round_trip(real):
    factory, admin = real
    env = factory(persistent=False)
    name = env.machine_name
    assert env.cwd == "/data/home"
    result = env.execute("echo hello; echo oops >&2; exit 7")
    assert result["returncode"] == 7 and result["output"] == "hello\noops\n"
    assert env.execute("cat", stdin_data="from-stdin")["output"] == "from-stdin"
    env.execute("mkdir -p proj && cd proj")
    assert env.execute("pwd")["output"].strip() == "/data/home/proj"
    timed = env.execute("sleep 60", timeout=3)
    assert timed["returncode"] == 124
    time.sleep(2)
    assert env.execute("pgrep -f 'sleep 6[0]' || echo none")["output"].strip() == "none"
    env.cleanup()
    assert all(m.name != name for m in admin.machines.list())


def test_persistent_box_is_paused_and_resumed_by_name(real):
    factory, admin = real
    task_id = f"it-{uuid.uuid4().hex[:8]}"
    first = factory(persistent=True, task_id=task_id)
    box_id = first.machine_id
    first.execute("echo kept > ~/marker.txt")
    first.cleanup()
    assert admin.machines.get(box_id).state == "suspended"
    second = factory(persistent=True, task_id=task_id)
    assert second.machine_id == box_id
    assert second.execute("cat ~/marker.txt")["output"].strip() == "kept"


def test_interrupt_kills_the_remote_process(real):
    factory, _ = real
    env = factory(persistent=False)
    proc = env._run_bash("echo started > ~/int.txt; sleep 60; echo finished >> ~/int.txt")
    deadline = time.monotonic() + 30
    while env.execute("cat ~/int.txt 2>/dev/null")["output"].strip() != "started":
        assert time.monotonic() < deadline, "the command never started"
        time.sleep(0.5)
    started = time.monotonic()
    proc.kill()
    assert proc.wait(5) == 130 and time.monotonic() - started < 1
    time.sleep(3)
    assert env.execute("pgrep -f 'sleep 6[0]' || echo none")["output"].strip() == "none"
    assert env.execute("cat ~/int.txt")["output"].strip() == "started"


def test_large_output_and_file_fetch(real, tmp_path):
    factory, _ = real
    env = factory(persistent=False)
    result = env.execute("seq 1 100000")
    assert result["returncode"] == 0 and result["output"] == "".join(f"{i}\n" for i in range(1, 100001))
    env.execute("head -c 3000000 /dev/urandom > big.bin; sha256sum big.bin > big.sha")
    dest = tmp_path / "big.bin"
    env.fetch_file("/data/home/big.bin", dest, max_bytes=10_000_000)
    expected = env.execute("cut -d' ' -f1 big.sha")["output"].strip()
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == expected


def test_box_deleted_outside_hermes_is_replaced(real):
    factory, admin = real
    env = factory(persistent=False)
    old = env.machine_id
    admin.machines.delete(old, confirm=old)
    first = env.execute("echo hi")
    second = first if first["returncode"] == 0 else env.execute("echo hi")
    assert second["output"].strip() == "hi" and env.machine_id != old
