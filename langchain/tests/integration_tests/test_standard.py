"""Deep Agents' standard sandbox tests, run on a live instabox sandbox."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from instabox import Client
from langchain_tests.integration_tests import SandboxIntegrationTests

from langchain_instabox import InstaboxSandbox

if TYPE_CHECKING:
    from collections.abc import Iterator

    from deepagents.backends.protocol import SandboxBackendProtocol

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTABOX_API_KEY"), reason="INSTABOX_API_KEY is not set"
)


# deepagents 0.7.40's BaseSandbox overwrites on write and globs to absolute paths (its own
# test_local_sandbox_operations.py says so), while langchain-tests 1.1.9 expects the old behavior.
_STALE_SUITE = pytest.mark.xfail(
    reason="langchain-tests 1.1.9 predates deepagents 0.7.40's BaseSandbox write and glob",
    strict=True,
)
# The test calls `python`: the default instabox template ships `python3` only.
_NO_PYTHON_ALIAS = pytest.mark.xfail(
    reason="the default instabox template has python3 but no `python` command",
    strict=True,
)


class TestInstaboxSandboxStandard(SandboxIntegrationTests):
    test_write_existing_file_fails = _STALE_SUITE(
        SandboxIntegrationTests.test_write_existing_file_fails
    )
    test_glob = _STALE_SUITE(SandboxIntegrationTests.test_glob)
    test_glob_basic_pattern = _STALE_SUITE(SandboxIntegrationTests.test_glob_basic_pattern)
    test_glob_with_directories = _STALE_SUITE(SandboxIntegrationTests.test_glob_with_directories)
    test_glob_hidden_files_explicitly = _STALE_SUITE(
        SandboxIntegrationTests.test_glob_hidden_files_explicitly
    )
    test_glob_with_character_class = _STALE_SUITE(
        SandboxIntegrationTests.test_glob_with_character_class
    )
    test_glob_with_question_mark = _STALE_SUITE(
        SandboxIntegrationTests.test_glob_with_question_mark
    )
    test_execute_large_stdout_payload = _NO_PYTHON_ALIAS(
        SandboxIntegrationTests.test_execute_large_stdout_payload
    )
    test_aexecute_large_stdout_payload = _NO_PYTHON_ALIAS(
        SandboxIntegrationTests.test_aexecute_large_stdout_payload
    )

    @pytest.fixture(scope="class")
    def sandbox(self) -> Iterator[SandboxBackendProtocol]:
        client = Client()
        # delete_after cleans up even if this process dies.
        machine = client.machines.create(
            f"integ-langchain-{uuid.uuid4().hex[:8]}", delete_after_seconds=3600
        )
        try:
            yield InstaboxSandbox(client=client, machine=machine)
        finally:
            client.machines.delete(machine.id, confirm=machine.id)
