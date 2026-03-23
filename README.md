# tapchain

`tapchain` is a hop-by-hop HTTP capture tool for multi-client LLM routing.

It is meant for setups like:

- `client -> new API -> cliproxy -> Claude endpoint`
- `client -> new API -> sub2api -> Claude endpoint`

The main point is that each hop is a separate proxy stage, so you can log:

- client request into `new API`
- `new API` request into `cliproxy` or `sub2api`
- `cliproxy` or `sub2api` request into the final Claude endpoint

Each stage gets the same `X-Trace-Id`, so the full path stays correlated.

## What it captures

- Request headers
- Request body
- Upstream URL
- Response status
- Response headers
- Response body

Sensitive headers are redacted in logs.

## Files

- [`profiles.example.json`](./profiles.example.json) contains sample chains for `claude code`, `opencode`, and `geminicli`.
- [`tapchain/cli.py`](./tapchain/cli.py) contains the CLI.
- [`tapchain/proxy.py`](./tapchain/proxy.py) contains the reverse proxy.

## Quick start

Install the tool:

```bash
pip install -e .
```

Run the full chain for one profile:

```bash
python -m tapchain run --config profiles.example.json --profile claude-code/cliproxy/us1
```

Print the client env for a profile:

```bash
python -m tapchain env --config profiles.example.json --profile claude-code/cliproxy/us1 --client claude-code
```

Run a client with the right env vars injected:

```bash
python -m tapchain exec --config profiles.example.json --profile claude-code/cliproxy/us1 --client claude-code -- claude
```

Switch the active profile in the config:

```bash
python -m tapchain switch --config profiles.example.json claude-code/sub2api/us1
```

## Direct hop logging

If you need logs directly between `new API` and `cliproxy` or `sub2api`, run those as separate stages.

Example chain:

```text
client -> 127.0.0.1:4010 -> 127.0.0.1:4011 -> 127.0.0.1:4012 -> Claude
```

That gives you direct logs for:

- `client-newapi`
- `newapi-cliproxy`
- `cliproxy-claude`

For the `sub2api` route, use the equivalent chain with the `newapi-sub2api` stage.

## Log layout

- `logs/events.jsonl` for structured events
- `logs/raw/<trace_id>/` for raw request and response bodies

## Notes

- The proxy is HTTP-level, so it is good for capturing API payloads and streaming responses.
- If you want inside-process logging in a real `new API`, `cliproxy`, or `sub2api` service, add the same `X-Trace-Id` to their own middleware logs.
- The client env variable names are templates. If your `claude code`, `opencode`, or `geminicli` build uses different names, change them once in `profiles.example.json` and keep the wrapper flow unchanged.
