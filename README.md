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