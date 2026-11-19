"""Through Hermes core: registration makes ``terminal.backend: instabox`` resolve, core reads the flags, and core's
environment factory builds a working environment."""

import pytest
from agent import terminal_env_registry as reg

import instabox_hermes_plugin

NAME = "instabox"


class _Ctx:
    def register_terminal_environment_provider(self, provider):
        reg.register_provider(provider)


@pytest.fixture
def registered():
    previous = reg.snapshot_registration(NAME)
    instabox_hermes_plugin.register(_Ctx())
    current = reg.get_provider(NAME)
    yield current
    reg.restore_registration(NAME, current, previous)


def test_backend_resolves_and_core_reads_its_flags(registered):
    assert registered is not None and registered.name == NAME
    assert NAME in reg.plugin_backend_names()
    assert reg.provider_flag(NAME, "is_remote") is True
    assert reg.provider_flag(NAME, "is_container") is True
    assert reg.provider_flag(NAME, "session_isolated_when_nonpersistent") is True
    assert reg.provider_flag(NAME, "default_cwd") == "~"
    from tools.approval import _should_skip_container_guards

    assert _should_skip_container_guards(NAME) is True


def test_api_key_joins_the_global_strip_list(registered):
    assert "INSTABOX_API_KEY" in reg.plugin_strip_env_keys()


def test_core_factory_builds_a_working_environment(registered, cloud, monkeypatch):
    from tools.terminal_tool_backends import _create_environment

    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test_not_real")
    env = _create_environment(
        NAME, image="", cwd="~", timeout=30, task_id="core-factory", container_config={"container_persistent": False}
    )
    try:
        box = cloud.created[0]
        assert env.cwd == box.home and env._hermes_sandbox_home == box.home
        result = env.execute("echo through-core; pwd")
        assert result["returncode"] == 0 and result["output"].split() == ["through-core", box.home]
    finally:
        env.cleanup()
    assert ("delete", box.name) in cloud.calls
