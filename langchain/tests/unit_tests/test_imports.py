from importlib.metadata import entry_points

import langchain_instabox


def test_public_api() -> None:
    assert langchain_instabox.__all__ == ["InstaboxSandbox", "__version__"]


def test_entry_point_is_registered() -> None:
    eps = entry_points(group="deepagents_code.sandbox_providers")
    [ep] = [ep for ep in eps if ep.name == "instabox"]
    assert ep.value == "langchain_instabox.provider:InstaboxProvider"
