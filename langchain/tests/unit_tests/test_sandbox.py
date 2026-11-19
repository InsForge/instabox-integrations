from __future__ import annotations

from instabox import ConflictError, PermissionDeniedError

from langchain_instabox import InstaboxSandbox
from langchain_instabox.sandbox import MAX_EXEC_TIMEOUT
from tests.unit_tests.fakes import FakeClient, FakeExec


def _sandbox(client: FakeClient, **kwargs: int) -> InstaboxSandbox:
    return InstaboxSandbox(client=client, machine="m_1", **kwargs)  # type: ignore[arg-type]


def test_id_is_the_machine_id() -> None:
    assert _sandbox(FakeClient()).id == "m_1"


def test_execute_combines_stdout_and_stderr() -> None:
    client = FakeClient()
    client.machines.results = [FakeExec(stdout="out\n", stderr="err\n", exit_code=3)]
    r = _sandbox(client).execute("x")
    assert r.output == "out\n\nerr\n"
    assert r.exit_code == 3
    assert r.truncated is False


def test_execute_stderr_only_and_truncated() -> None:
    client = FakeClient()
    client.machines.results = [FakeExec(stderr="err", truncated=True)]
    r = _sandbox(client).execute("x")
    assert r.output == "err"
    assert r.truncated is True


def test_execute_caps_timeout_at_600() -> None:
    client = FakeClient()
    sandbox = _sandbox(client)
    sandbox.execute("a")  # default 1800
    sandbox.execute("b", timeout=0)  # no limit elsewhere
    sandbox.execute("c", timeout=30)
    timeouts = [args[2] for name, args in client.machines.calls if name == "exec"]
    assert timeouts == [MAX_EXEC_TIMEOUT, MAX_EXEC_TIMEOUT, 30]


def test_execute_notes_a_timeout() -> None:
    client = FakeClient()
    client.machines.results = [FakeExec(stdout="partial", exit_code=124, timed_out=True)]
    r = _sandbox(client).execute("sleep 999", timeout=5)
    assert r.exit_code == 124
    assert r.output == "partial\n[command timed out after 5 seconds]"


def test_execute_resumes_a_paused_sandbox_once() -> None:
    client = FakeClient()
    client.machines.running = False
    client.machines.results = [FakeExec(stdout="ok")]
    r = _sandbox(client).execute("echo ok")
    assert r.output == "ok"
    assert [name for name, _ in client.machines.calls] == ["exec", "ensure_running", "exec"]


def test_execute_other_conflicts_propagate() -> None:
    client = FakeClient()

    def busy(*_args: object, **_kwargs: object) -> None:
        raise ConflictError("busy", code="operation_conflict", status=409)

    client.machines.exec = busy  # type: ignore[method-assign]
    try:
        _sandbox(client).execute("x")
    except ConflictError as e:
        assert e.code == "operation_conflict"
    else:
        raise AssertionError("expected ConflictError")


def test_upload_and_download_roundtrip() -> None:
    client = FakeClient()
    sandbox = _sandbox(client)
    up = sandbox.upload_files([("/data/home/a.txt", b"hi")])
    assert [r.error for r in up] == [None]
    down = sandbox.download_files(["/data/home/a.txt"])
    assert down[0].content == b"hi"
    assert down[0].error is None


def test_relative_paths_are_invalid() -> None:
    sandbox = _sandbox(FakeClient())
    assert sandbox.upload_files([("a.txt", b"x")])[0].error == "invalid_path"
    assert sandbox.download_files(["a.txt"])[0].error == "invalid_path"


def test_file_errors_map_to_deepagents_codes() -> None:
    client = FakeClient()
    client.files.errors = {
        "/dir": ConflictError("is a directory", code="not_a_file", status=409),
        "/locked": PermissionDeniedError("denied", code="permission_denied", status=403),
    }
    sandbox = _sandbox(client)
    assert sandbox.download_files(["/missing"])[0].error == "file_not_found"
    assert sandbox.download_files(["/dir"])[0].error == "is_directory"
    assert sandbox.download_files(["/locked"])[0].error == "permission_denied"
    assert sandbox.upload_files([("/dir", b"x")])[0].error == "is_directory"
    assert sandbox.upload_files([("/locked", b"x")])[0].error == "permission_denied"
