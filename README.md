# stupidex

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e .            # Editable mode: changes take effect immediately.
```

## Usage

### TUI (terminal)

```bash
stupidex
```

### Web UI (agent)

```bash
stupidex-web
```

Then open `http://127.0.0.1:5000` in your browser.

## Security defaults

- Web authentication uses an `HttpOnly` cookie; bearer tokens remain supported for API clients.
- Provider, model and API key settings are stored per user. API keys are encrypted at rest.
- GitHub OAuth tokens are encrypted at rest and are used only by the server when downloading repository archives.
- Agent shell execution is disabled by default. Enable it only inside an isolated container with
  `STUPIDEX_ENABLE_SHELL=1`; optionally restrict executables with `STUPIDEX_SHELL_COMMANDS`.
- Production deployments must persist `STUPIDEX_DATA_DIR` (the Docker image uses `/data`).

## GitHub private repositories

Create a GitHub OAuth App and configure its callback URL as:

```text
https://your-domain.example/api/integrations/github/callback
```

Then set these environment variables:

```bash
GITHUB_CLIENT_ID=your_oauth_app_client_id
GITHUB_CLIENT_SECRET=your_oauth_app_client_secret
GITHUB_REDIRECT_URI=https://your-domain.example/api/integrations/github/callback
FRONTEND_URL=https://your-domain.example
```

The integration requests GitHub's `repo` scope so the connected user can clone
and update private repositories they are allowed to access. Public GitHub and
GitLab repositories continue to work without connecting an account.

### Standalone executable (no Python required)

```bash
pyinstaller stupidex.spec
# -> dist/Stupidex.exe
```

Double-click `dist/Stupidex.exe` — it starts the server and opens the browser automatically.

## Development

The project uses the `src` layout:

```
pyproject.toml
src/
  stupidex/
    main.py         # TUI entry point
    web.py          # Web server (Flask) + SSE streaming
    launcher.py     # Desktop launcher (server + browser)
    static/         # Web UI (HTML, CSS, JS)
    llm/
      handle_input.py   # LLM streaming + tool-calling loop
      message.py        # Message dataclass + render
      tools.py          # File ops, shell, git tools for the agent
```


# TODO - In priority order
- Thinking collapse
- Commands
- Sessions with resuming
- Model selector
- Provider selector
- Implement tool calls, there already is some specs defined for tool calls and responses in message.py
- MCP (Configurable in a settings.json)
- Subagents (Configurable)
- Not re-render the full history on each update

# Needs fix
- Input messages are not queued and can have multiple concurrent connections
