"""Test bootstrap: import the plugin directory as the package ``instabox_hermes_plugin``, plus a fake instabox client.

Hermes core (``agent``, ``tools``, ``hermes_cli``) must be importable: run the tests with a hermes-agent
checkout's interpreter. The fake client needs no network: a box is a temporary directory and exec runs real
local ``bash`` processes, so Hermes's whole command path (snapshot, cwd tracking, stdin, timeouts, kills) runs.
"""

from __future__ import annotations

import importlib.util
import itertools
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from instabox import ConflictError, ExecResult, NotFoundError

PACKAGE = "instabox_hermes_plugin"
_ROOT = Path(__file__).resolve().parents[1]

# ``python -m pytest`` from the plugin dir puts it on sys.path, where its modules could shadow core's.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _ROOT]


def _load_plugin_package():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_load_plugin_package()

# Captured before any test runs: unit tests never see real credentials, integration tests ask for them.
_REAL_KEY = os.environ.get("INSTABOX_API_KEY", "")
_REAL_URL = os.environ.get("INSTABOX_API_URL", "")


@pytest.fixture(autouse=True)
def _hermes_home(tmp_path, monkeypatch):
    """Every test gets its own profile home and no real instabox credentials."""
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for var in ("INSTABOX_API_KEY", "INSTABOX_API_URL"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def real_key():
    """The real API key for integration tests; skips the test when none is configured."""
    if not _REAL_KEY:
        pytest.skip("INSTABOX_API_KEY is not set")
    return _REAL_KEY, _REAL_URL or None


class _RepoRootIsNotATestPackage:
    """Collect the plugin root as a plain directory: its ``__init__.py`` has relative imports."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == _ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("instabox-hermes-repo-root"):
        config.pluginmanager.register(_RepoRootIsNotATestPackage(), "instabox-hermes-repo-root")


# --------------------------------------------------------------------------- fake instabox client


class FakeStream:
    def __init__(self, data: bytes):
        self.size = len(data)
        self._chunks = iter([data[i : i + 4096] for i in range(0, len(data), 4096)] or [])

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeCloud:
    """The account: boxes by id. ``calls`` records every lifecycle call as ``(action, name)``."""

    def __init__(self, root: Path):
        self.root = root
        self.boxes: dict[str, SimpleNamespace] = {}
        self.calls: list[tuple[str, str]] = []
        self.created: list[SimpleNamespace] = []
        self.create_kwargs: list[dict] = []
        self.exec_count = 0
        self.max_output = 1 << 20
        self.max_exec_seconds = 600
        self._ids = itertools.count(1)

    def box(self, ref: str) -> SimpleNamespace:
        for box in self.boxes.values():
            if ref in (box.id, box.name):
                return box
        raise NotFoundError("machine not found", code="not_found", status=404)

    def add_box(self, name: str, state: str = "running", **extra) -> SimpleNamespace:
        box_id = f"m_fake{next(self._ids)}"
        home = self.root / box_id / "home"
        home.mkdir(parents=True)
        box = SimpleNamespace(
            id=box_id, name=name, state=state, metadata={}, last_error=None, home=str(home), kwargs={}, **extra
        )
        self.boxes[box_id] = box
        return box

    def client(self, api_key=None, base_url=None):
        return FakeClient(self, api_key, base_url)


class FakeMachines:
    def __init__(self, cloud: FakeCloud):
        self.cloud = cloud

    def create(self, name, **kwargs):
        if any(b.name == name for b in self.cloud.boxes.values()):
            raise ConflictError(f'a machine named "{name}" already exists', code="operation_conflict", status=409)
        box = self.cloud.add_box(name)
        box.metadata = dict(kwargs.get("metadata") or {})
        box.kwargs = kwargs
        self.cloud.calls.append(("create", name))
        self.cloud.created.append(box)
        self.cloud.create_kwargs.append(kwargs)
        return box

    def get(self, machine):
        return self.cloud.box(machine)

    def ensure_running(self, machine, **_):
        box = self.cloud.box(machine)
        self.cloud.calls.append(("ensure_running", box.name))
        box.state = "running"
        return box

    def pause(self, machine, **_):
        box = self.cloud.box(machine)
        self.cloud.calls.append(("pause", box.name))
        box.state = "suspended"

    def delete(self, machine, *, confirm=None, **_):
        box = self.cloud.box(machine)
        if confirm not in (box.id, box.name):
            raise ConflictError("confirm the delete", code="delete_confirmation_required", status=409)
        self.cloud.calls.append(("delete", box.name))
        del self.cloud.boxes[box.id]

    def exec(self, machine, command, *, cwd=None, env=None, stdin=None, timeout=60):
        box = self.cloud.box(machine)
        if box.state != "running":
            raise ConflictError(f"machine is {box.state}; start it first", code="machine_not_running", status=409)
        self.cloud.exec_count += 1
        argv = ["bash", "-lc", command] if isinstance(command, str) else list(command)
        data = stdin.encode() if isinstance(stdin, str) else stdin
        proc = subprocess.Popen(
            argv,
            cwd=box.home,
            env={**os.environ, "HOME": box.home},
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        limit = min(timeout, self.cloud.max_exec_seconds)
        timed_out = False
        try:
            out, err = proc.communicate(data, timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        code = 124 if timed_out else (proc.returncode if proc.returncode >= 0 else 128 - proc.returncode)
        cap = self.cloud.max_output
        truncated = len(out) > cap or len(err) > cap
        return ExecResult(
            stdout=out[:cap].decode("utf-8", "replace"),
            stderr=err[:cap].decode("utf-8", "replace"),
            exit_code=code,
            timed_out=timed_out,
            truncated=truncated,
            signal="",
        )


class FakeFiles:
    def __init__(self, cloud: FakeCloud):
        self.cloud = cloud

    def write(self, machine, path, data, **_):
        self.cloud.box(machine)
        payload = data.read() if hasattr(data, "read") else (data.encode() if isinstance(data, str) else data)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(payload)

    def read_stream(self, machine, path, **_):
        self.cloud.box(machine)
        p = Path(path)
        if p.is_dir():
            raise ConflictError("not a file", code="not_a_file", status=409)
        if not p.exists():
            raise NotFoundError("no such file", code="not_found", status=404)
        return FakeStream(p.read_bytes())


class FakeClient:
    def __init__(self, cloud: FakeCloud, api_key=None, base_url=None):
        self.api_key, self.base_url = api_key, base_url
        self.machines = FakeMachines(cloud)
        self.files = FakeFiles(cloud)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    """A fake instabox account, installed as ``instabox.Client``; no Hermes files are synced by default."""
    import instabox

    from instabox_hermes_plugin import instabox_env

    root = tmp_path / "cloud"
    root.mkdir()
    fake = FakeCloud(root)
    monkeypatch.setattr(instabox, "Client", fake.client)
    monkeypatch.setattr(instabox_env, "iter_sync_files", lambda base: [])
    yield fake
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def make_env(cloud):
    """Build ``InstaboxEnvironment`` objects on the fake account and clean them up after the test."""
    from instabox_hermes_plugin.instabox_env import InstaboxEnvironment

    made = []

    def factory(**kwargs):
        kwargs.setdefault("timeout", 30)
        kwargs.setdefault("task_id", "unit")
        env = InstaboxEnvironment(client=cloud.client("test-key"), **kwargs)
        made.append(env)
        return env

    yield factory
    for env in made:
        env.cleanup()
