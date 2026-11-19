"""The ``instabox`` terminal backend provider: classification, settings, health checks and environment creation."""

from __future__ import annotations

import importlib.util
import logging
import re
from typing import Any, Optional

from agent.terminal_env_provider import TerminalEnvironmentProvider

logger = logging.getLogger(__name__)

BACKEND_NAME = "instabox"
API_KEY_ENV = "INSTABOX_API_KEY"
API_URL_ENV = "INSTABOX_API_URL"
DEFAULT_API_URL = "https://api.instabox.sh"
DASHBOARD_URL = "https://dashboard.instabox.sh"
DEFAULT_TEMPLATE = "agent-browser"
DEFAULT_NAME_PREFIX = "hermes"
# The prompt-time ``uname`` probe's task id: declined, so the first reply never waits on a throwaway box.
PROBE_TASK_ID = "prompt-backend-probe"
# Bounds of ``delete_after_seconds`` the instabox API accepts.
_DELETE_AFTER_RANGE = (60, 86400)
_MAX_PREFIX_LEN = 30


def _terminal_config() -> dict:
    from hermes_cli.config import load_config_readonly

    terminal = load_config_readonly().get("terminal")
    return terminal if isinstance(terminal, dict) else {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(key: str, value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("instabox: terminal.%s=%r is not a whole number; ignored", key, value)
        return None


def settings() -> dict[str, Any]:
    """The ``terminal.instabox_*`` keys from config.yaml, normalized, with defaults."""
    try:
        terminal = _terminal_config()
    except Exception as exc:  # an unreadable config still gets the defaults
        logger.debug("instabox: could not read config.yaml: %s", exc)
        terminal = {}
    vcpus = _as_int("instabox_vcpus", terminal.get("instabox_vcpus"))
    mem_mib = _as_int("instabox_mem_mib", terminal.get("instabox_mem_mib"))
    if (vcpus is None) != (mem_mib is None):
        logger.warning(
            "instabox: set both terminal.instabox_vcpus and terminal.instabox_mem_mib, or neither; "
            "using the template's default size"
        )
        vcpus = mem_mib = None
    delete_after = _as_int("instabox_delete_after_seconds", terminal.get("instabox_delete_after_seconds"))
    low, high = _DELETE_AFTER_RANGE
    if delete_after is not None and not low <= delete_after <= high:
        logger.warning("instabox: terminal.instabox_delete_after_seconds must be %d-%d; ignored", low, high)
        delete_after = None
    prefix = re.sub(r"[^a-z0-9-]+", "-", str(terminal.get("instabox_name_prefix") or "").lower())
    prefix = prefix.strip("-")[:_MAX_PREFIX_LEN].strip("-") or DEFAULT_NAME_PREFIX
    return {
        "template": str(terminal.get("instabox_template") or "").strip() or DEFAULT_TEMPLATE,
        "vcpus": vcpus,
        "mem_mib": mem_mib,
        "name_prefix": prefix,
        "delete_after_seconds": delete_after,
    }


def _secret(name: str) -> Optional[str]:
    from agent.secret_scope import get_secret

    value = get_secret(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def sdk_installed() -> bool:
    try:
        return importlib.util.find_spec("instabox") is not None
    except (ImportError, ValueError):
        return False


def api_key_present() -> bool:
    try:
        return _secret(API_KEY_ENV) is not None
    except Exception:  # an unscoped read under multiplexing counts as missing
        return False


def _api_key() -> str:
    """This profile's key. Under a routed profile ``get_secret`` never falls back to another profile's."""
    key = _secret(API_KEY_ENV)
    if key:
        return key
    from agent.secret_scope import serves_routed_profile

    if serves_routed_profile():
        raise ValueError(
            f"{API_KEY_ENV} is not set for this profile: add it to this profile's .env (or its "
            "secret source). Another profile's instabox key is never used."
        )
    raise ValueError(f"instabox backend: {API_KEY_ENV} is not set. Create a key on API keys at {DASHBOARD_URL}.")


class InstaboxProvider(TerminalEnvironmentProvider):
    """instabox: one cloud sandbox per task, paused on exit and resumed by name when persistence is on."""

    # Commands run in the sandbox's own Linux filesystem, never on the host.
    is_remote = True
    is_container = True
    # Boxes are resumed by name: non-persistent sessions must never share one.
    session_isolated_when_nonpersistent = True
    # No local argv reaches a box with stdin and stdout open, so Bot Desktop stays off.
    supports_streams = False

    @property
    def name(self) -> str:
        return BACKEND_NAME

    @property
    def display_name(self) -> str:
        return "instabox"

    @property
    def description(self) -> str:
        return "Run commands in an instabox cloud sandbox (paused and resumed per task)."

    @property
    def env_description(self) -> str:
        return "an instabox cloud sandbox (Debian Linux)"

    @property
    def cache_path_base(self) -> Optional[str]:
        return "~/.hermes"

    @property
    def default_cwd(self) -> Optional[str]:
        return "~"

    @property
    def strip_env_keys(self) -> frozenset:
        return frozenset({API_KEY_ENV})

    def is_available(self) -> bool:
        # Offline: the SDK is importable and a key is configured.
        return sdk_installed() and api_key_present()

    def check_requirements(self, config: dict[str, Any]) -> bool:
        if not sdk_installed():
            logger.error(
                "instabox backend selected but the instabox-sdk package is not installed. "
                "Run `hermes plugins enable instabox` to let Hermes install it."
            )
            return False
        if not api_key_present():
            logger.error(
                "instabox backend selected but %s is not set. Create a key on API keys at %s "
                "and add it to your Hermes .env.",
                API_KEY_ENV,
                DASHBOARD_URL,
            )
            return False
        return True

    def probe(self) -> tuple[str, str]:
        try:
            if not sdk_installed():
                return ("unavailable", "The instabox-sdk Python package is not installed.")
            if not api_key_present():
                return ("needs_setup", f"Set {API_KEY_ENV} (create a key at {DASHBOARD_URL}).")
            return ("ready", "")
        except Exception as exc:  # probe must never raise
            return ("unavailable", str(exc))

    def setup_instructions(self) -> list[str]:
        cfg = settings()
        lines = [
            f"{API_KEY_ENV} is set."
            if api_key_present()
            else f"Set {API_KEY_ENV} in your Hermes .env (create a key on API keys at {DASHBOARD_URL}).",
            f"Template: {cfg['template']} (change it with `hermes config set terminal.instabox_template universal`).",
            "With terminal.container_persistent: true (the default) each task's box is paused on exit and resumed "
            "next time. With false every session gets its own box, deleted when the session ends.",
        ]
        if not sdk_installed():
            lines.insert(0, "The instabox-sdk package is missing: run `hermes plugins enable instabox` to install it.")
        return lines

    def doctor_checks(self) -> list[tuple[bool, str, str]]:
        sdk, key = sdk_installed(), api_key_present()
        return [
            (
                sdk,
                "instabox SDK",
                "(instabox-sdk installed)" if sdk else "(missing: run `hermes plugins enable instabox`)",
            ),
            (
                key,
                "instabox API key",
                f"({API_KEY_ENV} set)" if key else f"({API_KEY_ENV} not set, see {DASHBOARD_URL})",
            ),
        ]

    def create_environment(
        self,
        *,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        image: Optional[str] = None,
        container_config: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ):
        if task_id == PROBE_TASK_ID or kwargs.get("probe_only"):
            raise RuntimeError("instabox: the prompt-time backend probe does not get a box")
        from instabox import Client

        from .instabox_env import InstaboxEnvironment

        cc = container_config or {}
        cfg = settings()
        # Always explicit: without them the SDK reads os.environ, which may hold another profile's values.
        client = Client(api_key=_api_key(), base_url=_secret(API_URL_ENV) or DEFAULT_API_URL)
        try:
            return InstaboxEnvironment(
                client=client,
                cwd=cwd or "~",
                timeout=timeout,
                task_id=task_id or "default",
                persistent=_as_bool(cc.get("container_persistent"), default=True),
                # A batch run's per-task image override names a template, the closest instabox concept.
                template=(image or "").strip() or cfg["template"],
                vcpus=cfg["vcpus"],
                mem_mib=cfg["mem_mib"],
                name_prefix=cfg["name_prefix"],
                delete_after_seconds=cfg["delete_after_seconds"],
            )
        except BaseException:
            client.close()
            raise
