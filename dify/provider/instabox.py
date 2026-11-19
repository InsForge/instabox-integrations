from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from instabox import InstaboxError

from instabox_sandbox import friendly_error, make_client


class InstaboxProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        # One cheap authenticated read: list at most one sandbox.
        try:
            with make_client(credentials) as client:
                client.machines.list_page(limit=1)
        except ValueError as e:
            raise ToolProviderCredentialValidationError(str(e)) from None
        except InstaboxError as e:
            raise ToolProviderCredentialValidationError(str(friendly_error(e))) from None
