import os
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dify_plugin  # noqa: E402,F401  (patches with gevent first, as the plugin runtime does)
from instabox import Client  # noqa: E402

API_KEY = os.environ.get("INSTABOX_API_KEY", "")
# Sandboxes the tests create themselves start with this.
TEST_PREFIX = "integ-dify-"

live = pytest.mark.skipif(not API_KEY, reason="set INSTABOX_API_KEY to run the live tests")


def unique_name() -> str:
    return TEST_PREFIX + secrets.token_hex(4)


@pytest.fixture(scope="session")
def creds():
    return {"api_key": API_KEY}


@pytest.fixture(scope="session")
def client():
    with Client(api_key=API_KEY) as c:
        yield c


@pytest.fixture(scope="session")
def sandbox(client):
    """One sandbox for the tests that reuse a sandbox, made without the plugin's metadata."""
    box = client.machines.create(unique_name(), delete_after_seconds=3600)
    try:
        yield box
    finally:
        client.machines.delete(box.id, confirm=box.id)


def invoke(tool_cls, params, creds, *, convert=False):
    """Call a tool the way the runtime does and sort its messages by type."""
    tool = tool_cls.from_credentials(creds)
    messages = list(tool.invoke(params) if convert else tool._invoke(params))
    by_type = {}
    for m in messages:
        by_type.setdefault(m.type.value, []).append(m.message)
    variables = {v.variable_name: v.variable_value for v in by_type.get("variable", [])}
    jsons = [j.json_object for j in by_type.get("json", [])]
    return SimpleNamespace(
        text="\n".join(t.text for t in by_type.get("text", [])),
        json=jsons[0] if jsons else None,
        variables=variables,
        blobs=[m for m in messages if m.type.value == "blob"],
        messages=messages,
    )
