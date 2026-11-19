"""In-memory stand-ins for the parts of `instabox.Client` the package uses."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from instabox import ConflictError, NotFoundError


@dataclass
class FakeExec:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    truncated: bool = False


@dataclass
class FakeMachines:
    running: bool = True
    known: set[str] = field(default_factory=lambda: {"m_1"})
    results: list[FakeExec] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def exec(self, machine: str, command: str, *, timeout: int = 60) -> FakeExec:
        self.calls.append(("exec", (machine, command, timeout)))
        if not self.running:
            raise ConflictError("not running", code="machine_not_running", status=409)
        return self.results.pop(0) if self.results else FakeExec()

    def ensure_running(self, machine: str) -> SimpleNamespace:
        self.calls.append(("ensure_running", machine))
        if machine not in self.known:
            raise NotFoundError("no such machine", code="not_found", status=404)
        self.running = True
        return SimpleNamespace(id=machine)

    def create(self, name: str | None = None, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("create", (name, kwargs)))
        return SimpleNamespace(id="m_new")

    def delete(self, machine: str, *, confirm: str | None = None) -> None:
        self.calls.append(("delete", (machine, confirm)))
        if machine not in self.known:
            raise NotFoundError("no such machine", code="not_found", status=404)


@dataclass
class FakeFiles:
    store: dict[str, bytes] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)

    def write(self, machine: str, path: str, data: bytes) -> None:  # noqa: ARG002
        if path in self.errors:
            raise self.errors[path]
        self.store[path] = data

    def read_bytes(self, machine: str, path: str) -> bytes:  # noqa: ARG002
        if path in self.errors:
            raise self.errors[path]
        if path not in self.store:
            raise NotFoundError(f"{path} does not exist", code="not_found", status=404)
        return self.store[path]


@dataclass
class FakeSnapshots:
    snaps: list[SimpleNamespace] = field(default_factory=list)

    def get(self, ref: str) -> SimpleNamespace:
        for s in self.snaps:
            if s.id == ref:
                return s
        raise NotFoundError("no such snapshot", code="not_found", status=404)

    def list(self) -> list[SimpleNamespace]:
        return list(self.snaps)


@dataclass
class FakeClient:
    machines: FakeMachines = field(default_factory=FakeMachines)
    files: FakeFiles = field(default_factory=FakeFiles)
    snapshots: FakeSnapshots = field(default_factory=FakeSnapshots)
