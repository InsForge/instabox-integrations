# Privacy Policy for the instabox Dify Plugin

The plugin connects Dify to the instabox API (`api.instabox.sh`) to create and use instabox sandboxes. It sends data only to instabox. The plugin does not collect personal data for itself, and it keeps nothing after a call ends.

## Data the plugin sends to instabox

- **API key**: Dify stores it in the plugin credentials. The plugin sends it to instabox to authenticate each request. It is never sent into a sandbox and never appears in tool output or error messages.
- **Commands and code**: the commands, programs and standard input that you or your agent run. They run inside your sandbox.
- **Files**: files you upload go into the sandbox.
- **Sandbox settings**: the sandbox ID or name, and the name, template and delete time you choose when you create a sandbox.

## Data the plugin returns to Dify

Sandbox IDs and names, command output (stdout, stderr and exit code) and the files you download.

## Storage and logging

The plugin stores nothing and writes no logs of its own. Files and programs stay in a sandbox until the sandbox is deleted. A sandbox that a tool creates for one call is deleted when the call ends. instabox processes account and sandbox data under its own [privacy policy](https://instabox.sh/privacy).

## Third parties

The plugin talks only to instabox. Code that runs inside a sandbox can reach the internet. What that code sends is up to the code.

## Contact

support@instabox.sh
