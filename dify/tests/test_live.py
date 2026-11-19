"""Live tests: every tool against the real instabox API. Needs INSTABOX_API_KEY."""

import hashlib
import http.server
import os
import threading
import time

import pytest
from conftest import invoke, live, unique_name
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from instabox import NotFoundError

from provider.instabox import InstaboxProvider
from tools.create_sandbox import CreateSandboxTool
from tools.delete_sandbox import DeleteSandboxTool
from tools.download_file import DownloadFileTool
from tools.run_code import RunCodeTool
from tools.run_command import RunCommandTool
from tools.upload_file import UploadFileTool

pytestmark = live


def wait_gone(client, box_id, seconds=60):
    """True once the sandbox is deleted or deleting."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if client.machines.get(box_id).state in ("deleting", "deleted"):
                return True
        except NotFoundError:
            return True
        time.sleep(1)
    return False


# Credentials


def test_credentials_good(creds):
    InstaboxProvider()._validate_credentials(creds)


@pytest.mark.parametrize(
    "key, code",
    [("ibk_wrong", "malformed_key"), ("ibk_" + "a" * 52, "invalid_key")],
)
def test_credentials_bad(key, code):
    with pytest.raises(ToolProviderCredentialValidationError) as e:
        InstaboxProvider()._validate_credentials({"api_key": key})
    message = str(e.value)
    assert "rejected the API key" in message and code in message
    assert key not in message


# Run Command


def test_run_command_output(sandbox, creds):
    r = invoke(RunCommandTool, {"command": "echo hello && echo oops >&2", "sandbox_id": sandbox.id}, creds)
    assert r.variables == {"stdout": "hello\n", "stderr": "oops\n", "exit_code": 0}
    assert r.json["sandbox_id"] == sandbox.id and r.json["one_off"] is False
    assert r.json["timed_out"] is False and r.json["truncated"] is False
    assert r.text == "hello\n[stderr]\noops\n[exit code 0]"


def test_run_command_non_zero_exit_is_a_result(sandbox, creds):
    r = invoke(RunCommandTool, {"command": "echo bad >&2; exit 3", "sandbox_id": sandbox.id}, creds)
    assert r.variables["exit_code"] == 3 and r.variables["stderr"] == "bad\n"
    assert r.text.endswith("[exit code 3]")


def test_run_command_stdin(sandbox, creds):
    r = invoke(RunCommandTool, {"command": "tr a-z A-Z", "stdin": "hello stdin\n", "sandbox_id": sandbox.id}, creds)
    assert r.variables["stdout"] == "HELLO STDIN\n"


def test_run_command_cwd(sandbox, creds):
    r = invoke(RunCommandTool, {"command": "pwd", "cwd": "/tmp", "sandbox_id": sandbox.id}, creds)
    assert r.variables["stdout"] == "/tmp\n"
    r = invoke(RunCommandTool, {"command": "pwd; whoami", "sandbox_id": sandbox.id}, creds)
    assert r.variables["stdout"] == "/data/home\nuser\n"


def test_run_command_timeout(sandbox, creds):
    started = time.monotonic()
    r = invoke(RunCommandTool, {"command": "sleep 30", "timeout": 2.0, "sandbox_id": sandbox.id}, creds)
    assert time.monotonic() - started < 20
    assert r.json["timed_out"] is True and r.variables["exit_code"] == 124
    assert "[timed out]" in r.text


def test_run_command_unknown_sandbox(creds):
    with pytest.raises(ValueError, match="not found"):
        invoke(RunCommandTool, {"command": "true", "sandbox_id": unique_name()}, creds)


def test_run_command_one_off_sandbox_is_deleted(client, creds):
    r = invoke(RunCommandTool, {"command": "hostname; . /etc/os-release; echo $ID $VERSION_ID"}, creds)
    assert r.variables["exit_code"] == 0, r.text
    assert r.variables["stdout"].splitlines()[1] == "debian 13"
    assert r.json["one_off"] is True and r.json["sandbox_id"].startswith("m_")
    assert wait_gone(client, r.json["sandbox_id"])


# Run Code

PROGRAMS = {
    "python": ("import sys\nprint(sum(range(10)))\nprint('to stderr', file=sys.stderr)\n", "45\n"),
    "javascript": ("const os = require('os')\nconsole.log(typeof os.cpus(), 6 * 7)\n", "object 42\n"),
    "typescript": ("const n: number = 41\nconsole.log(n + 1)\n", "42\n"),
    "bash": ('echo "$((6 * 7))"\n', "42\n"),
    "go": 'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println(6 * 7) }\n',
    "java": "class Main { public static void main(String[] a) { System.out.println(6 * 7); } }\n",
}


@pytest.mark.parametrize("language", list(PROGRAMS))
def test_run_code_language(sandbox, creds, language):
    program = PROGRAMS[language]
    code, expected = program if isinstance(program, tuple) else (program, "42\n")
    r = invoke(RunCodeTool, {"code": code, "language": language, "sandbox_id": sandbox.id}, creds)
    assert r.variables["stdout"] == expected, r.text
    assert r.variables["exit_code"] == 0
    assert r.json["language"] == language
    if language == "python":
        assert r.variables["stderr"] == "to stderr\n"


def test_run_code_javascript_module_syntax(sandbox, creds):
    code = "import { platform } from 'node:os'\nawait Promise.resolve()\nconsole.log(platform())\n"
    r = invoke(RunCodeTool, {"code": code, "language": "javascript", "sandbox_id": sandbox.id}, creds)
    assert r.variables["stdout"] == "linux\n", r.text


def test_run_code_errors_are_results(sandbox, creds):
    r = invoke(RunCodeTool, {"code": "1 / 0", "sandbox_id": sandbox.id}, creds)
    assert r.variables["exit_code"] == 1 and "ZeroDivisionError" in r.variables["stderr"]
    r = invoke(RunCodeTool, {"code": "raise SystemExit(5)", "sandbox_id": sandbox.id}, creds)
    assert r.variables["exit_code"] == 5


def test_run_code_leaves_no_temp_files(sandbox, creds):
    invoke(RunCodeTool, {"code": "print(1)", "sandbox_id": sandbox.id}, creds)
    r = invoke(RunCommandTool, {"command": "ls /tmp | grep -c dify-code- || true", "sandbox_id": sandbox.id}, creds)
    assert r.variables["stdout"] == "0\n"


def test_run_code_one_off_sandbox(client, creds):
    r = invoke(RunCodeTool, {"code": "import platform\nprint(platform.python_version())"}, creds)
    assert r.variables["stdout"].startswith("3."), r.text
    assert r.json["one_off"] is True
    assert wait_gone(client, r.json["sandbox_id"])


# Upload and download


class _Server:
    """Serves one file over HTTP, standing in for Dify's file URL."""

    def __init__(self, data: bytes):
        body = data

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/file"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def dify_file(url, filename):
    # The dict Dify sends for a file parameter; Tool.invoke turns it into a File.
    return {
        "dify_model_identity": "__dify__file__",
        "url": url,
        "type": "document",
        "filename": filename,
        "extension": os.path.splitext(filename)[1],
        "mime_type": "application/octet-stream",
        "size": None,
    }


def test_upload_then_download_round_trip(sandbox, creds):
    data = os.urandom(200_000) + b"\xff\xfe not utf-8"
    filename = f"{unique_name()}.bin"
    server = _Server(data)
    try:
        params = {"sandbox_id": sandbox.name, "file": dify_file(server.url, filename), "path": "dify-test/"}
        up = invoke(UploadFileTool, params, creds, convert=True)
    finally:
        server.close()
    expected_path = f"/data/home/dify-test/{filename}"
    assert up.json == {
        "sandbox_id": sandbox.id,
        "path": expected_path,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    assert up.variables == {"path": expected_path}

    for path in (expected_path, f"dify-test/{filename}"):
        down = invoke(DownloadFileTool, {"sandbox_id": sandbox.id, "path": path}, creds)
        assert len(down.blobs) == 1
        assert down.blobs[0].message.blob == data
        assert down.blobs[0].meta == {"mime_type": "application/octet-stream", "filename": filename}
        assert down.json["size"] == len(data)


def test_upload_default_and_absolute_paths(sandbox, creds):
    data = b"a,b\n1,2\n"
    server = _Server(data)
    try:
        r = invoke(UploadFileTool, {"sandbox_id": sandbox.id, "file": dify_file(server.url, "data.csv")}, creds, convert=True)
        assert r.json["path"] == "/data/home/data.csv"
        r = invoke(
            UploadFileTool,
            {"sandbox_id": sandbox.id, "file": dify_file(server.url, "data.csv"), "path": "/tmp/in/renamed.csv"},
            creds,
            convert=True,
        )
        assert r.json["path"] == "/tmp/in/renamed.csv"
    finally:
        server.close()
    down = invoke(DownloadFileTool, {"sandbox_id": sandbox.id, "path": "/tmp/in/renamed.csv"}, creds)
    assert down.blobs[0].message.blob == data
    assert down.blobs[0].meta["mime_type"] == "text/csv"


def test_download_errors(sandbox, creds):
    with pytest.raises(ValueError, match="No file at /tmp/nothing-here"):
        invoke(DownloadFileTool, {"sandbox_id": sandbox.id, "path": "/tmp/nothing-here"}, creds)
    with pytest.raises(ValueError, match="is not a file"):
        invoke(DownloadFileTool, {"sandbox_id": sandbox.id, "path": "/tmp"}, creds)


# Reuse


def test_reuse_paused_sandbox_by_name(client, sandbox, creds):
    invoke(RunCommandTool, {"command": "echo kept > reuse.txt", "sandbox_id": sandbox.id}, creds)
    client.machines.pause(sandbox.id)
    assert client.machines.get(sandbox.id).state == "suspended"
    r = invoke(RunCodeTool, {"code": "print(open('/data/home/reuse.txt').read().strip())", "sandbox_id": sandbox.name}, creds)
    assert r.variables["stdout"] == "kept\n", r.text
    assert r.json["sandbox_id"] == sandbox.id and r.json["one_off"] is False
    assert client.machines.get(sandbox.id).state == "running"


# Create and delete


def test_create_use_delete_sandbox(client, creds):
    name = unique_name()
    box_id = None
    try:
        r = invoke(CreateSandboxTool, {"name": name, "template": "minimal", "delete_after_seconds": 3600}, creds)
        box_id = r.variables["sandbox_id"]
        assert r.json["name"] == name and r.json["template"] == "minimal" and r.json["delete_at"]
        box = client.machines.get(box_id)
        assert box.state == "running" and box.metadata == {"created-by": "dify"}

        r = invoke(RunCommandTool, {"command": "echo in $HOSTNAME", "sandbox_id": name}, creds)
        assert r.variables["exit_code"] == 0 and r.json["sandbox_id"] == box_id

        r = invoke(DeleteSandboxTool, {"sandbox_id": name}, creds)
        assert r.json == {"deleted": True, "sandbox_id": box_id, "name": name}
        assert wait_gone(client, box_id, seconds=5)
    finally:
        if box_id and not wait_gone(client, box_id, seconds=1):
            client.machines.delete(box_id, confirm=box_id)


def test_delete_refuses_sandbox_not_made_by_plugin(client, sandbox, creds):
    with pytest.raises(ValueError, match="not created by this plugin"):
        invoke(DeleteSandboxTool, {"sandbox_id": sandbox.id}, creds)
    assert client.machines.get(sandbox.id).state not in ("deleting", "deleted")
