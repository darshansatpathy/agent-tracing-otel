# 4. Prompt and completion text is off by default

Status: accepted

## Context

Prompts contain customer data. Tracing backends are rarely held to the same retention and access rules as the primary data store, and a trace is easy to share.

## Decision

- `call.content(...)` records nothing unless `configure(capture_content=True)`; when on, a redaction function runs first and text is truncated to 4,000 characters.
- The Collector's `attributes/drop_content` processor deletes `gen_ai.input.messages`, `gen_ai.output.messages` and `gen_ai.system_instructions` from every span, so one misconfigured service cannot leak text to the backends. Remove it deliberately, per environment, if you need content in a development stack.

## Consequences

Debugging a bad answer needs the content from somewhere else (the application's own store, with its own controls) or a development environment with capture on.
