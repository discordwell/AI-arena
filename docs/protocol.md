# Protocol

This repo is intentionally lightweight. The only hard constraints are:

- 2 players
- alternating turns
- deterministic game rules (given current state and chosen move)

## Python Game Interface

See `src/ai_arena/game.py`.

Required methods:

- `initial_state() -> JSONValue`
- `legal_moves(state, player) -> list[JSONValue]`
- `apply_move(state, player, move) -> JSONValue`
- `terminal(state) -> Terminal`
- `render(state) -> str`

`JSONValue` means the state/move must be JSON-serializable (dict/list/str/int/etc).

## Python Agent Interface

Agents are objects with:

- `name: str`
- `select_move(game, state, player, legal_moves) -> JSONValue`

## Subprocess JSONL Agent Protocol

To use a non-Python agent (or to wrap a model harness), run an executable and
exchange one JSON object per line over stdin/stdout.

Engine -> bot:

```json
{
  "type": "turn",
  "game": "tictactoe",
  "player": 0,
  "state": {"board": [0,0,0,0,0,0,0,0,0]},
  "legal_moves": [0,1,2,3,4,5,6,7,8],
  "ts_ms": 1730000000000
}
```

Bot -> engine (success):

```json
{"type": "move", "move": 4}
```

Bot -> engine (failure):

```json
{"type": "error", "error": "api_call_failed: TimeoutError: ..."}
```

Notes:

- Bots may print other JSON message types; the engine ignores unknown `type`s.
- If the bot sends `{"type": "error"}`, the engine treats it as an agent error and the bot forfeits.
- If the bot times out, crashes, or returns an illegal move, it also forfeits.
- Bots should **not** silently fall back to a default move on failure — emit an error instead.

