"""Offline checks of the manifest, the tool definitions and parameter handling."""

from pathlib import Path

import pytest
import yaml
from conftest import ROOT
from dify_plugin.core.entities.plugin.setup import PluginConfiguration
from dify_plugin.entities.tool import ToolProviderConfiguration
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from instabox_sandbox import int_param, timeout_param
from provider.instabox import InstaboxProvider
from tools.create_sandbox import CreateSandboxTool
from tools.run_code import RUNNERS, RunCodeTool, runner_command
from tools.run_command import RunCommandTool


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.chdir(ROOT)  # tool paths in the provider are relative to the plugin root
    return ToolProviderConfiguration(**load("provider/instabox.yaml"))


def test_manifest_is_valid():
    manifest = load("manifest.yaml")
    config = PluginConfiguration(**manifest)
    assert config.plugins.tools == ["provider/instabox.yaml"]
    assert (ROOT / "_assets" / manifest["icon"]).is_file()
    assert (ROOT / "_assets" / manifest["icon_dark"]).is_file()
    assert (ROOT / manifest["privacy"]).is_file()
    assert manifest["repo"].startswith("https://github.com/")


def test_provider_and_tools_load(provider):
    names = [t.identity.name for t in provider.tools]
    assert names == ["run_command", "run_code", "upload_file", "download_file", "create_sandbox", "delete_sandbox"]
    assert (ROOT / provider.extra.python.source).is_file()
    for tool in provider.tools:
        assert (ROOT / tool.extra.python.source).is_file()
        assert tool.identity.author == "instabox"


def test_run_code_options_match_runners(provider):
    tool = next(t for t in provider.tools if t.identity.name == "run_code")
    language = next(p for p in tool.parameters if p.name == "language")
    assert [o.value for o in language.options] == list(RUNNERS)


def test_text_has_no_em_dashes_or_semicolons(provider):
    texts = {"README.md": (ROOT / "README.md").read_text(), "PRIVACY.md": (ROOT / "PRIVACY.md").read_text()}
    for tool in provider.tools:
        texts[tool.identity.name] = " ".join(
            [tool.description.human.en_us, tool.description.llm]
            + [f"{p.human_description.en_us} {p.llm_description or ''}" for p in tool.parameters]
        )
    for name, text in texts.items():
        assert "—" not in text and "–" not in text, name
        assert ";" not in text, name


def test_empty_key_never_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_from_the_environment")
    with pytest.raises(ToolProviderCredentialValidationError, match="API key is required"):
        InstaboxProvider()._validate_credentials({"api_key": "  "})


@pytest.mark.parametrize("value", [0, 601, 1.5, "abc", -1])
def test_timeout_out_of_range(value):
    with pytest.raises(ValueError, match="timeout"):
        timeout_param({"timeout": value})


def test_number_params():
    assert timeout_param({}) == 120
    assert timeout_param({"timeout": 600.0}) == 600
    assert timeout_param({"timeout": "30"}) == 30
    assert int_param({"n": ""}, "n", 7, 0, 10) == 7


def test_bad_input_fails_before_any_request():
    creds = {"api_key": "ibk_never_used"}
    with pytest.raises(ValueError, match="command is required"):
        list(RunCommandTool.from_credentials(creds)._invoke({"command": "  "}))
    with pytest.raises(ValueError, match="language must be one of"):
        list(RunCodeTool.from_credentials(creds)._invoke({"code": "puts 1", "language": "ruby"}))
    with pytest.raises(ValueError, match="delete_after_seconds must be 0 or at least 60"):
        list(CreateSandboxTool.from_credentials(creds)._invoke({"delete_after_seconds": 30}))


@pytest.mark.parametrize("language", list(RUNNERS))
def test_runner_command_keeps_extension(language):
    ext, runner = RUNNERS[language]
    command = runner_command(language)
    assert f"XXXXXXXX.{ext})" in command and f'{runner} "$f" </dev/null' in command


def test_package_leaves_out_tests():
    ignored = (ROOT / ".difyignore").read_text().splitlines()
    assert "tests/" in ignored and ".env" in ignored
    assert not list(Path(ROOT).glob("*.difypkg"))
