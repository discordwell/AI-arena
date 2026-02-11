# Codex Agent

`CodexAgent` is wired to a subprocess JSONL bot:

- wrapper class: `codex/agent/agent.py:CodexAgent`
- bot process: `codex/agent/codex_subprocess_bot.py`

The bot calls `codex exec` per turn with defaults:

- model: `gpt-5.3-codex`
- reasoning effort: `xhigh`

## Run vs Codex

```bash
ai-arena play tictactoe --p0 human --p1 codex/agent/agent.py:CodexAgent
```

Or run the JSONL bot directly:

```bash
ai-arena play tictactoe \
  --p0 human \
  --p1 "subprocess:python3 -u codex/agent/codex_subprocess_bot.py"
```

## Overrides

- `CODEX_ARENA_MODEL` (default: `gpt-5.3-codex`)
- `CODEX_ARENA_REASONING_EFFORT` (default: `xhigh`)
- `CODEX_ARENA_CODEX_BIN` (default: `codex`)
- `CODEX_ARENA_WORKDIR` (default: current working directory)
- `CODEX_ARENA_TIMEOUT_S` (default: `3500`)

For `CodexAgent` wrapper only:

- `CODEX_ARENA_COMMAND`: full subprocess command override
