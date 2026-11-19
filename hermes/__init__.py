"""instabox terminal backend for Hermes Agent: select it with ``terminal.backend: instabox``. See README.md."""

from __future__ import annotations

from .provider import InstaboxProvider


def register(ctx) -> None:
    ctx.register_terminal_environment_provider(InstaboxProvider())
