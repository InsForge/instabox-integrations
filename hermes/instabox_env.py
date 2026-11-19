"""instabox execution environment: each Hermes command is one exec call in an instabox sandbox ("box")."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import posixpath
import re
import shlex
import tarfile
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from instabox import ConflictError, NotFoundError
from tools.environments.base import BaseEnvironment, EnvironmentConnectionError, FileFetchError
from tools.environments.file_sync import FileSyncManager, iter_sync_files, quoted_rm_command

logger = logging.getLogger(__name__)

DEFAULT_HOME = "/data/home"
# The instabox API ends a foreground exec after this many seconds.
MAX_EXEC_SECONDS = 600
# The server-side limit trails Hermes's timeout so Hermes's own kill fires first.
_EXEC_GRACE_S = 10
_KILL_EXEC_TIMEOUT_S = 30
_MAX_NAME_LEN = 63
_MAX_TASK_SLUG_LEN = 32
_HASH_LEN = 8
_MAX_METADATA_VALUE = 512
# Records the command's process group id (exec starts it as a group leader) and merges stderr into stdout.
_RUNNER = 'f=$1; shift; printf %s "$$" > "$f"; exec 2>&1; "$@"; rc=$?; rm -f -- "$f"; exit $rc'
# Ends the process group a pidfile names: TERM, then KILL a second later.
_KILL = (
    'f=$1; for _ in $(seq 1 30); do [ -s "$f" ] && break; sleep 0.1; done; pid=$(cat "$f" 2>/dev/null); '
    'rm -f -- "$f"; [ -n "$pid" ] || exit 0; kill -TERM -- "-$pid" 2>/dev/null; sleep 1; '
    'kill -KILL -- "-$pid" 2>/dev/null; true'
)
_TRUNCATED_NOTE = "\n[instabox kept only the first 1 MiB of this command's output]"
_CAPPED_NOTE = f"\n[instabox ends a foreground command after {MAX_EXEC_SECONDS}s: run longer work with background=true]"


def _slug(value: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", value.lower())).strip("-")


def box_name(prefix: str, task_id: str, *, unique: bool = False) -> str:
    """``<prefix>-<task slug>``, at most 63 of ``[a-z0-9-]``: altered ids get a hash, ``unique`` a random suffix."""
    prefix = _slug(prefix) or "hermes"
    slug = _slug(task_id) or "default"
    digest = "-" + hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:_HASH_LEN]
    rand = "-" + uuid.uuid4().hex[:8] if unique else ""
    hashed = slug != task_id or len(slug) > _MAX_TASK_SLUG_LEN
    room = _MAX_NAME_LEN - len(prefix) - 1 - len(rand) - (len(digest) if hashed else 0)
    if len(slug) > room:
        hashed = True
        room = _MAX_NAME_LEN - len(prefix) - 1 - len(rand) - len(digest)
    slug = slug[:room].rstrip("-") or "t"
    return f"{prefix}-{slug}{digest if hashed else ''}{rand}"


class _ExecHandle:
    """``ProcessHandle`` for one blocking exec call, whose output goes into a pipe that Hermes drains."""

    def __init__(self, run: Callable[[], tuple[str, int]], cancel: Callable[[], None]):
        read_fd, self._write_fd = os.pipe()
        self._stdout = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        self._cancel = cancel
        self._lock = threading.Lock()
        self._finished = False
        self._returncode: Optional[int] = None
        self._done = threading.Event()
        threading.Thread(target=self._work, args=(run,), name="instabox-exec", daemon=True).start()

    def _work(self, run: Callable[[], tuple[str, int]]) -> None:
        try:
            output, code = run()
        except Exception as exc:  # the handle must always finish
            logger.debug("instabox: command worker failed", exc_info=True)
            output, code = f"[instabox: {exc}]", 1
        self.finish(output, code)

    def finish(self, output: str, code: int, *, blocking: bool = True) -> None:
        """Write ``output``, close the pipe and record ``code``. The first call wins."""
        if not self._lock.acquire(blocking=blocking):
            return  # the worker is writing its output and finishes the handle itself
        try:
            if self._finished:
                return
            self._finished = True
            view = memoryview(output.encode("utf-8", "replace"))
            try:
                while view:
                    view = view[os.write(self._write_fd, view) :]
            except OSError:  # Hermes closed its end
                pass
            with contextlib.suppress(OSError):
                os.close(self._write_fd)
            self._returncode = code
            self._done.set()
        finally:
            self._lock.release()

    @property
    def stdout(self):
        return self._stdout

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def poll(self) -> Optional[int]:
        return self._returncode if self._done.is_set() else None

    def kill(self) -> None:
        # Returns at once: Hermes's interrupt and timeout paths must not wait on the network.
        with contextlib.suppress(Exception):
            self._cancel()
        self.finish("", 130, blocking=False)

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        self._done.wait(timeout)
        return self._returncode


class InstaboxEnvironment(BaseEnvironment):
    """Hermes commands in an instabox box. Merged stdout and stderr come back when each command exits."""

    _stdin_mode = "payload"
    # A timed-out probe kills only its own process group, never the box.
    _passwordless_sudo_probe_supported = True

    def __init__(
        self,
        *,
        client: Any,
        cwd: str = "~",
        timeout: int = 60,
        task_id: str = "default",
        persistent: bool = True,
        template: Optional[str] = None,
        vcpus: Optional[int] = None,
        mem_mib: Optional[int] = None,
        name_prefix: str = "hermes",
        delete_after_seconds: Optional[int] = None,
    ):
        super().__init__(cwd=cwd, timeout=timeout)
        self._client = client
        self._requested_cwd = cwd
        self._persistent = bool(persistent)
        # Hermes tears down non-persistent environments after every turn unless they are session-scoped.
        self._session_scoped = not self._persistent
        self._task_id = task_id
        self._template = template
        self._size = {"vcpus": vcpus, "mem_mib": mem_mib} if vcpus and mem_mib else {}
        self._name_prefix = name_prefix
        self._delete_after = delete_after_seconds
        self._lock = threading.Lock()
        self._machine_id: Optional[str] = None
        self._machine_name: Optional[str] = None
        self._created = False
        # Set by cleanup(): a later call comes from a stale reference and gets no box.
        self._closed = False
        self._remote_home = DEFAULT_HOME
        self._sync_manager: Optional[FileSyncManager] = None
        try:
            self._set_up()
        except BaseException:
            self.cleanup()
            raise

    # --- Box lifecycle ---
    @property
    def machine_id(self) -> Optional[str]:
        return self._machine_id

    @property
    def machine_name(self) -> Optional[str]:
        return self._machine_name

    def _set_up(self) -> None:
        """Attach a box, sync files and take the shell snapshot."""
        self._attach()
        self._sync_manager.sync(force=True)
        self._snapshot_ready = False
        self.init_session()

    def _attach(self) -> None:
        self._created = False
        if self._persistent:
            name = box_name(self._name_prefix, self._task_id)
            machine = self._find(name) or self._create(name)
        else:
            machine = self._create(box_name(self._name_prefix, self._task_id, unique=True))
        self._machine_id, self._machine_name = machine.id, machine.name
        self._remote_home = self._detect_home()
        # ``~`` (Hermes's default for this backend) is the box user's home; an explicit path is kept.
        if self._requested_cwd in {"~", ""}:
            self.cwd = self._remote_home
        elif self._requested_cwd.startswith("~/"):
            self.cwd = f"{self._remote_home.rstrip('/')}/{self._requested_cwd[2:]}"
        else:
            self.cwd = self._requested_cwd
        base = f"{self._remote_home.rstrip('/')}/.hermes"
        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(base),
            upload_fn=self._upload,
            delete_fn=self._delete,
            bulk_upload_fn=self._bulk_upload,
            bulk_download_fn=self._bulk_download,
        )

    def _find(self, name: str) -> Any:
        """This task's box by name, started or resumed, or None when there is none."""
        try:
            machine = self._client.machines.get(name)
        except NotFoundError:
            return None
        if machine.state in {"deleting", "deleted"}:
            return None
        if machine.state == "error":
            raise RuntimeError(
                f"instabox: box {name} is in the error state ({machine.last_error or 'no detail'}). "
                f"Delete it (`instabox rm {name}`) and Hermes creates a new one."
            )
        machine = self._client.machines.ensure_running(machine.id)
        logger.info("instabox: resumed box %s (%s) for task %s", name, machine.id, self._task_id)
        return machine

    def _create(self, name: str) -> Any:
        kwargs: dict[str, Any] = {"metadata": {"hermes_task_id": self._task_id[:_MAX_METADATA_VALUE]}, **self._size}
        if self._template:
            kwargs["template"] = self._template
        if self._delete_after:
            kwargs["delete_after_seconds"] = self._delete_after
        try:
            machine = self._client.machines.create(name, **kwargs)
        except ConflictError as exc:
            # Another Hermes process created this task's box between our lookup and create.
            found = self._find(name) if self._persistent and "already exists" in str(exc) else None
            if found is None:
                raise
            return found
        self._created = True
        logger.info("instabox: created box %s (%s) for task %s", name, machine.id, self._task_id)
        return machine

    def _detect_home(self) -> str:
        try:
            home = self._exec(self._machine_id, ["bash", "-c", 'printf %s "$HOME"'], timeout=30).stdout.strip()
        except Exception as exc:
            logger.debug("instabox: home detection failed: %s", exc)
            return DEFAULT_HOME
        return home if home.startswith("/") else DEFAULT_HOME

    def _exec(self, machine_id: Optional[str], argv: list[str], **kwargs: Any) -> Any:
        """``machines.exec``. A box paused behind Hermes's back (idle pause, dashboard) is resumed once."""
        if machine_id is None:
            raise EnvironmentConnectionError("instabox: no box is attached")
        try:
            return self._client.machines.exec(machine_id, argv, **kwargs)
        except ConflictError as exc:
            if exc.code != "machine_not_running" or self._closed:
                raise
        logger.info("instabox: box %s is not running; starting it", machine_id)
        self._client.machines.ensure_running(machine_id)
        return self._client.machines.exec(machine_id, argv, **kwargs)

    def _forget(self, machine_id: str) -> None:
        """Drop a box the API reports as gone, so the next command attaches a new one."""
        if self._machine_id == machine_id:
            self._machine_id = None
            logger.warning("instabox: box %s no longer exists; the next command gets a new one", machine_id)

    def _before_execute(self) -> None:
        with self._lock:
            if self._closed:
                raise EnvironmentConnectionError(
                    "instabox: this environment was cleaned up and has no box any more.",
                    retry_hint="Retry the command: Hermes starts a new instabox environment for it.",
                )
            if self._machine_id is None:
                # An earlier command found the box deleted: attach anew and tell the model.
                self._set_up()
                if self._created:
                    self._mark_recreated()
        self._sync_manager.sync()

    # --- Helpers (internal, never model-authored) ---
    def _sh(self, script: str, label: str) -> str:
        result = self._exec(self._machine_id, ["bash", "-c", script], timeout=300)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"instabox {label} failed (exit {result.exit_code}): {detail}")
        return result.stdout

    # --- File sync transport ---
    def _upload(self, host_path: str, remote_path: str) -> None:
        with open(host_path, "rb") as f:
            self._client.files.write(self._machine_id, remote_path, f)

    def _bulk_upload(self, files: list[tuple[str, str]]) -> None:
        """Upload ``files`` as one tar archive and unpack it in the box at their absolute paths."""
        fd, archive = tempfile.mkstemp(prefix="hermes-instabox-upload-", suffix=".tar")
        os.close(fd)
        remote_tar = f"/tmp/.hermes_upload.{uuid.uuid4().hex}.tar"  # no-tmp: ok, box path
        try:
            with tarfile.open(archive, "w", dereference=True) as tar:
                for host, remote in files:
                    tar.add(host, arcname=remote.lstrip("/"), recursive=False)
            with open(archive, "rb") as f:
                self._client.files.write(self._machine_id, remote_tar, f)
            quoted = shlex.quote(remote_tar)
            self._sh(f"tar xf {quoted} --no-same-owner -C /; rc=$?; rm -f {quoted}; exit $rc", "upload")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(archive)

    def _delete(self, remote_paths: list[str]) -> None:
        if remote_paths:
            self._sh(quoted_rm_command(remote_paths), "delete")

    def _bulk_download(self, dest: Path) -> None:
        """Write the box's ``~/.hermes`` to ``dest`` as a tar archive of paths relative to ``/``."""
        hermes_dir = f"{self._remote_home.rstrip('/')}/.hermes"
        remote_tar = f"/tmp/.hermes_sync.{os.getpid()}.{uuid.uuid4().hex[:8]}.tar"  # no-tmp: ok, box path
        quoted = shlex.quote(remote_tar)
        try:
            # --exclude: live sockets cannot be archived and must not fail the download.
            self._sh(
                f"mkdir -p {shlex.quote(hermes_dir)} && tar cf {quoted} --exclude='*.sock' "
                f"-C / {shlex.quote(hermes_dir.lstrip('/'))}",
                "download",
            )
            with self._client.files.read_stream(self._machine_id, remote_tar) as stream, open(dest, "wb") as out:
                for chunk in stream:
                    out.write(chunk)
        finally:
            with contextlib.suppress(Exception):
                self._client.machines.exec(self._machine_id, ["rm", "-f", "--", remote_tar], timeout=30)

    # --- File extraction (box -> host) ---
    def fetch_file(self, remote_path: str, local_dest: Path, *, max_bytes: int) -> None:
        """Copy a regular file out of the box over the file API, with no 1 MiB exec output limit."""
        machine_id = self._machine_id
        if machine_id is None:
            raise FileFetchError(f"could not read {remote_path!r}: no box is attached")
        path = remote_path if remote_path.startswith("/") else posixpath.join(self.cwd, remote_path)
        try:
            stream = self._client.files.read_stream(machine_id, path)
        except (NotFoundError, ConflictError) as exc:
            raise FileFetchError(
                f"could not read {remote_path!r} in the sandbox (missing, not a regular file, or unreadable)"
            ) from exc
        limit = f"{remote_path!r} exceeds the {max_bytes // (1024 * 1024)} MB delivery limit"
        with stream:
            if stream.size is not None and stream.size > max_bytes:
                raise FileFetchError(limit)
            data = bytearray()
            for chunk in stream:
                data += chunk
                if len(data) > max_bytes:
                    raise FileFetchError(limit)
        Path(local_dest).write_bytes(bytes(data))

    # --- Command execution ---
    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None):
        machine_id = self._machine_id
        pidfile = f"/tmp/.hermes-pid-{uuid.uuid4().hex}"  # no-tmp: ok, box path
        inner = ["bash", "-l", "-c", cmd_string] if login else ["bash", "-c", cmd_string]
        argv = ["bash", "-c", _RUNNER, "hermes", pidfile, *inner]
        lock = threading.Lock()
        state = {"cancelled": False, "dispatched": False}

        def cancel() -> None:
            with lock:
                state["cancelled"] = True
                dispatched = state["dispatched"]
            if dispatched:
                threading.Thread(
                    target=self._kill_group, args=(machine_id, pidfile), name="instabox-kill", daemon=True
                ).start()

        def run() -> tuple[str, int]:
            if machine_id is None or self._closed:
                return "[instabox: no box is attached; retry the command]", 1
            # Empty stdin means no stdin. Bytes keep surrogate-escaped binary exact.
            stdin = stdin_data.encode("utf-8", "surrogateescape") if stdin_data else None
            limit = min(max(int(timeout or 0), 1) + _EXEC_GRACE_S, MAX_EXEC_SECONDS)
            with lock:
                if state["cancelled"]:
                    return "", 130
                state["dispatched"] = True
            try:
                result = self._exec(machine_id, argv, stdin=stdin, timeout=limit)
            except NotFoundError:
                self._forget(machine_id)
                return (
                    "[instabox: the box no longer exists, so the command did not run. The next command "
                    "runs in a new box and files from the old one are gone.]",
                    1,
                )
            output = (result.stdout or "") + (result.stderr or "")
            if result.truncated:
                output += _TRUNCATED_NOTE
            if result.timed_out and limit == MAX_EXEC_SECONDS and not state["cancelled"]:
                output += _CAPPED_NOTE
            return output, result.exit_code

        return _ExecHandle(run, cancel)

    def _kill_group(self, machine_id: Optional[str], pidfile: str) -> None:
        """Stop a cancelled command's process group. Never resumes a paused box."""
        if machine_id is None:
            return
        try:
            self._client.machines.exec(
                machine_id, ["bash", "-c", _KILL, "hermes", pidfile], timeout=_KILL_EXEC_TIMEOUT_S
            )
        except Exception as exc:
            logger.debug("instabox: could not stop a cancelled command: %s", exc)

    # --- Teardown ---
    def cleanup(self):
        lock = getattr(self, "_lock", None)
        if lock is None:  # __init__ failed before the lock existed
            return
        with lock:
            self._closed = True
            machine_id = self._machine_id
            if machine_id is not None:
                if self._sync_manager is not None:
                    try:
                        self._sync_manager.sync_back()
                    except Exception as exc:
                        logger.warning("instabox: sync_back failed: %s", exc)
                if self._release(machine_id):
                    self._machine_id = None
            with contextlib.suppress(Exception):
                self._client.close()

    def _release(self, machine_id: str) -> bool:
        """Pause (persistent) or delete (non-persistent) this environment's own box. True when done or gone."""
        action = "pause" if self._persistent else "delete"
        try:
            if self._persistent:
                self._client.machines.pause(machine_id)
            else:
                self._client.machines.delete(machine_id, confirm=machine_id)
        except NotFoundError:
            logger.info("instabox: box %s was already gone", machine_id)
            return True
        except Exception as exc:
            if self._persistent and self._paused_already(machine_id):
                return True
            # instabox's idle pause still stops the box after 30 idle minutes.
            logger.warning("instabox: %s of box %s failed: %s", action, machine_id, exc)
            return False
        logger.info("instabox: %s box %s", "paused" if self._persistent else "deleted", machine_id)
        return True

    def _paused_already(self, machine_id: str) -> bool:
        try:
            return self._client.machines.get(machine_id).state in {"suspended", "stopped"}
        except Exception:
            return False


__all__ = ["InstaboxEnvironment", "box_name"]
