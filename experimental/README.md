# experimental/

Prototype modules that are real code but not yet wired into a live code
path. Grep for any module here to decide whether to wire it up, evolve it,
or delete it.

## Modules

- **`interrupts.py`** — Redis-stream-based human-in-the-loop interrupt
  system. Producers: dashboard inject UI + Telegram /inject. Consumers:
  agent run loops should poll between steps (not mid-LLM-call). Moved here
  from the top-level `warroom/` directory (spelled without the underscore
  that the rest of the project uses) during the production-hardening pass.
  Nothing currently imports it.
