# Codex Agent

Implement an agent object with:

- `name: str`
- `select_move(game, state, player, legal_moves) -> move`

You can also implement a subprocess bot (any language) using the JSONL protocol
in `docs/protocol.md` and run it via:

```bash
ai-arena play tictactoe --p0 subprocess:python3 -u bot.py --p1 random
```

