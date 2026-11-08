# Security policy

Report a vulnerability privately through [GitHub security advisories](https://github.com/darshansatpathy/agent-tracing-otel/security/advisories/new). Please do not open a public issue. You can expect a first reply within five working days.

## In scope

- Prompt or completion text reaching spans when content capture is off, or passing through the Collector configuration shipped here.
- Cost figures that can be made wrong by a provider response (for example, negative or missing usage).

## Notes

Langfuse credentials are read from environment variables by the Collector, never written to files in this repository.

## Supported versions

The latest release receives fixes.
