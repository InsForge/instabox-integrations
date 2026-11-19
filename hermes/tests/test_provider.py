"""The provider: the flags core reads, cheap offline availability, settings and what create_environment passes on."""

import socket

import pytest

from instabox_hermes_plugin import provider as provider_mod
from instabox_hermes_plugin.provider import InstaboxProvider, settings


def _write_config(home, text):
    (home / "config.yaml").write_text(text, encoding="utf-8")


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_classification():
    p = InstaboxProvider()
    assert p.name == "instabox" and p.display_name == "instabox"
    assert p.is_remote and p.is_container
    assert p.skip_container_guards  # a cloud sandbox, like Daytona and Modal
    assert p.session_isolated_when_nonpersistent
    assert not p.supports_streams
    assert p.default_cwd == "~"
    assert p.cache_path_base == "~/.hermes"
    assert p.strip_env_keys == frozenset({"INSTABOX_API_KEY"})


def test_name_is_a_valid_non_reserved_backend_name():
    from agent import terminal_env_registry as reg

    name = InstaboxProvider().name
    assert name not in reg.BUILTIN_BACKEND_NAMES
    assert all(c.isalnum() or c == "_" for c in name) and name == name.lower()


def test_is_available_needs_the_sdk_and_a_key_and_no_network(monkeypatch, no_network):
    p = InstaboxProvider()
    assert not p.is_available() and p.probe()[0] == "needs_setup"
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test_not_real")
    assert p.is_available() and p.probe() == ("ready", "")
    monkeypatch.setattr(provider_mod.importlib.util, "find_spec", lambda name: None)
    assert not p.is_available() and p.probe()[0] == "unavailable"


def test_blank_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("INSTABOX_API_KEY", "   ")
    assert not InstaboxProvider().is_available()


def test_check_requirements_logs_actionable_errors(monkeypatch, caplog):
    p = InstaboxProvider()
    assert not p.check_requirements({})
    assert "INSTABOX_API_KEY is not set" in caplog.text
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test_not_real")
    assert p.check_requirements({})


def test_doctor_and_setup_never_print_the_key(monkeypatch):
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_secret_value")
    p = InstaboxProvider()
    text = repr(p.doctor_checks()) + "\n".join(p.setup_instructions())
    assert "ibk_secret_value" not in text and all(ok for ok, _, _ in p.doctor_checks())


def test_settings_defaults(_hermes_home):
    assert settings() == {
        "template": "agent-browser",
        "vcpus": None,
        "mem_mib": None,
        "name_prefix": "hermes",
        "delete_after_seconds": None,
    }


def test_settings_from_config(_hermes_home):
    _write_config(
        _hermes_home,
        "terminal:\n  instabox_template: universal\n  instabox_vcpus: 2\n"
        "  instabox_mem_mib: 4096\n  instabox_name_prefix: My_Laptop!\n"
        "  instabox_delete_after_seconds: 3600\n",
    )
    assert settings() == {
        "template": "universal",
        "vcpus": 2,
        "mem_mib": 4096,
        "name_prefix": "my-laptop",
        "delete_after_seconds": 3600,
    }


def test_bad_settings_fall_back_with_warnings(_hermes_home, caplog):
    _write_config(
        _hermes_home, "terminal:\n  instabox_vcpus: 2\n  instabox_delete_after_seconds: 5\n  instabox_mem_mib: lots\n"
    )
    cfg = settings()
    assert cfg["vcpus"] is None and cfg["mem_mib"] is None and cfg["delete_after_seconds"] is None
    assert "both terminal.instabox_vcpus" in caplog.text and "60-86400" in caplog.text


def test_prompt_probe_gets_no_box(monkeypatch, cloud):
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test_not_real")
    with pytest.raises(RuntimeError, match="probe"):
        InstaboxProvider().create_environment(cwd="~", timeout=10, task_id="prompt-backend-probe")
    assert cloud.created == []


def test_create_environment_without_a_key_fails_clearly(cloud):
    with pytest.raises(ValueError, match="INSTABOX_API_KEY is not set"):
        InstaboxProvider().create_environment(cwd="~", timeout=10, task_id="t")
    assert cloud.created == []


def test_routed_profile_never_borrows_another_key(monkeypatch, cloud):
    import agent.secret_scope as scope

    monkeypatch.setattr(scope, "serves_routed_profile", lambda: True)
    with pytest.raises(ValueError, match="this profile"):
        InstaboxProvider().create_environment(cwd="~", timeout=10, task_id="t")


def test_create_environment_passes_settings(monkeypatch, cloud, _hermes_home):
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test_not_real")
    _write_config(_hermes_home, "terminal:\n  instabox_template: minimal\n")
    env = InstaboxProvider().create_environment(
        cwd="~", timeout=10, task_id="t1", container_config={"container_persistent": False, "container_cpu": 8}
    )
    try:
        assert env._client.api_key == "ibk_test_not_real" and env._client.base_url == "https://api.instabox.sh"
        assert cloud.create_kwargs[0]["template"] == "minimal"
        assert "vcpus" not in cloud.create_kwargs[0]  # container_cpu is not mapped
        assert cloud.created[0].name.startswith("hermes-t1-")
    finally:
        env.cleanup()
    assert env._client.closed


def test_image_override_names_the_template(monkeypatch, cloud):
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test_not_real")
    env = InstaboxProvider().create_environment(cwd="~", timeout=10, task_id="t2", image="universal")
    try:
        assert cloud.create_kwargs[0]["template"] == "universal" and cloud.created[0].name == "hermes-t2"
    finally:
        env.cleanup()
