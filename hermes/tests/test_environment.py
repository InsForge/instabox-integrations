"""The environment against the fake client: box lifecycle, the real Hermes command path, sync and cancel."""

import re
import time

import pytest
from tools.environments.base import EnvironmentConnectionError, FileFetchError

from instabox_hermes_plugin import instabox_env
from instabox_hermes_plugin.instabox_env import MAX_EXEC_SECONDS, box_name

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def test_box_name_is_valid_and_stable():
    assert box_name("hermes", "default") == "hermes-default"
    assert box_name("hermes", "default") == box_name("hermes", "default")
    odd = box_name("hermes", "Session_42/Desktop chat")
    assert NAME_RE.match(odd) and odd.startswith("hermes-session-42-desktop-chat-")
    assert box_name("hermes", "a_b") != box_name("hermes", "a-b")  # changed ids get a hash
    long = box_name("hermes-integ", "x" * 200)
    assert NAME_RE.match(long) and len(long) <= 63
    unique = box_name("hermes", "t1", unique=True)
    assert NAME_RE.match(unique) and unique != box_name("hermes", "t1", unique=True)


def test_non_persistent_round_trip(make_env, cloud):
    env = make_env(persistent=False, template="universal", delete_after_seconds=3600)
    box = cloud.created[0]
    assert box.name.startswith("hermes-unit-") and box.metadata == {"hermes_task_id": "unit"}
    assert cloud.create_kwargs[0]["template"] == "universal"
    assert cloud.create_kwargs[0]["delete_after_seconds"] == 3600
    assert env.cwd == box.home and env._remote_home == box.home

    result = env.execute("echo hello; echo oops >&2; exit 7")
    assert result["returncode"] == 7
    assert result["output"].split() == ["hello", "oops"]
    assert env.execute("cat", stdin_data="from-stdin")["output"] == "from-stdin"
    env.execute("mkdir -p proj && cd proj")
    assert env.execute("pwd")["output"].strip() == f"{box.home}/proj"
    env.execute("export HERMES_UNIT_VAR=kept")
    assert env.execute("echo $HERMES_UNIT_VAR")["output"].strip() == "kept"

    env.cleanup()
    assert ("delete", box.name) in cloud.calls and box.id not in cloud.boxes
    env.cleanup()  # idempotent
    with pytest.raises(EnvironmentConnectionError):
        env.execute("echo late")


def test_persistent_box_is_paused_and_resumed_by_name(make_env, cloud):
    first = make_env(persistent=True, task_id="task-1")
    box = cloud.created[0]
    assert box.name == "hermes-task-1"
    first.execute("echo kept > ~/marker.txt")
    first.cleanup()
    assert box.state == "suspended" and ("pause", box.name) in cloud.calls

    second = make_env(persistent=True, task_id="task-1")
    assert second.machine_id == box.id and len(cloud.created) == 1
    assert ("ensure_running", box.name) in cloud.calls
    assert second.execute("cat ~/marker.txt")["output"].strip() == "kept"
    second.cleanup()
    assert not any(action == "delete" for action, _ in cloud.calls)


def test_size_and_prefix_reach_create(make_env, cloud):
    make_env(persistent=True, task_id="t", vcpus=2, mem_mib=4096, name_prefix="hermes-integ")
    assert cloud.created[0].name == "hermes-integ-t"
    assert cloud.create_kwargs[0]["vcpus"] == 2 and cloud.create_kwargs[0]["mem_mib"] == 4096


def test_box_in_error_state_is_reported_not_replaced(make_env, cloud):
    cloud.add_box("hermes-broken", state="error")
    with pytest.raises(RuntimeError, match="error state"):
        make_env(persistent=True, task_id="broken")
    assert cloud.created == []


def test_name_race_attaches_the_box_another_process_created(make_env, cloud, monkeypatch):
    other = cloud.add_box("hermes-race", state="suspended")
    machines_get = instabox_env.InstaboxEnvironment._find
    calls = {"n": 0}

    def find_misses_first(self, name):
        calls["n"] += 1
        return None if calls["n"] == 1 else machines_get(self, name)

    monkeypatch.setattr(instabox_env.InstaboxEnvironment, "_find", find_misses_first)
    env = make_env(persistent=True, task_id="race")
    assert env.machine_id == other.id and cloud.created == []


def test_timeout_kills_the_command_process_group(make_env, cloud, tmp_path):
    env = make_env(persistent=False)
    flag = tmp_path / "finished"
    started = time.monotonic()
    result = env.execute(f"sleep 5; touch {flag}", timeout=1)
    assert result["returncode"] == 124 and "timed out" in result["output"]
    assert time.monotonic() - started < 4
    time.sleep(5)
    assert not flag.exists()  # the kill reached the remote process group


def test_interrupt_returns_at_once_and_stops_the_command(make_env, tmp_path):
    env = make_env(persistent=False)
    flag = tmp_path / "late"
    proc = env._run_bash(f"sleep 4; touch {flag}")
    time.sleep(0.5)
    started = time.monotonic()
    proc.kill()
    assert proc.wait(2) == 130 and time.monotonic() - started < 1
    time.sleep(5)
    assert not flag.exists()


def test_paused_box_is_resumed_once(make_env, cloud):
    env = make_env(persistent=True, task_id="idle")
    cloud.created[0].state = "suspended"  # instabox's idle pause
    assert env.execute("echo back")["output"].strip() == "back"
    assert cloud.calls.count(("ensure_running", "hermes-idle")) == 1


def test_box_deleted_outside_hermes_is_replaced(make_env, cloud):
    env = make_env(persistent=False)
    old = cloud.created[0]
    del cloud.boxes[old.id]
    first = env.execute("echo hi")
    assert first["returncode"] == 1 and "no longer exists" in first["output"]
    second = env.execute("echo hi")
    assert second["output"].strip() == "hi" and second.get("environment_recreated") is True
    assert env.machine_id != old.id


def test_truncated_output_is_flagged(make_env, cloud):
    env = make_env(persistent=False)
    cloud.max_output = 64
    result = env.execute("seq 1 1000")
    assert "first 1 MiB" in result["output"]


def test_server_limit_is_explained(make_env, cloud, monkeypatch):
    env = make_env(persistent=False)
    monkeypatch.setattr(instabox_env, "MAX_EXEC_SECONDS", 2)
    result = env.execute("sleep 6", timeout=20)
    assert result["returncode"] == 124 and "background=true" in result["output"]
    assert MAX_EXEC_SECONDS == 600


def test_large_and_binary_stdin_round_trip(make_env):
    env = make_env(persistent=False)
    text = "line\n" * 300000
    assert env.execute("wc -c", stdin_data=text)["output"].strip() == str(len(text))


def test_hermes_files_sync_up_and_back(make_env, cloud, monkeypatch, tmp_path):
    host = tmp_path / "skills" / "demo" / "SKILL.md"
    host.parent.mkdir(parents=True)
    host.write_text("original\n")
    monkeypatch.setattr(instabox_env, "iter_sync_files", lambda base: [(str(host), f"{base}/skills/demo/SKILL.md")])
    env = make_env(persistent=False)
    assert env.execute("cat ~/.hermes/skills/demo/SKILL.md")["output"] == "original\n"
    env.execute("echo changed-in-box > ~/.hermes/skills/demo/SKILL.md")
    env.cleanup()
    assert host.read_text() == "changed-in-box\n"


def test_fetch_file_uses_the_file_api(make_env, cloud, tmp_path):
    env = make_env(persistent=False)
    env.execute("head -c 3000000 /dev/zero > big.bin; echo hi > small.txt; mkdir adir")
    dest = tmp_path / "out"
    env.fetch_file("big.bin", dest, max_bytes=4_000_000)
    assert dest.stat().st_size == 3_000_000  # past the 1 MiB exec output limit
    with pytest.raises(FileFetchError, match="delivery limit"):
        env.fetch_file("big.bin", dest, max_bytes=1_000_000)
    with pytest.raises(FileFetchError):
        env.fetch_file("missing.txt", dest, max_bytes=100)
    with pytest.raises(FileFetchError):
        env.fetch_file(f"{env.cwd}/adir", dest, max_bytes=100)


def test_failed_release_keeps_the_box_for_a_retry(make_env, cloud, monkeypatch, caplog):
    env = make_env(persistent=False)
    box = cloud.created[0]

    def refuse(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(env._client.machines, "delete", refuse)
    env.cleanup()
    assert "delete of box" in caplog.text and env.machine_id == box.id
