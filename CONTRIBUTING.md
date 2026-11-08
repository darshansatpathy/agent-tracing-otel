# Contributing

```bash
python -m venv .venv && . .venv/bin/activate
make install
make check
```

The rule and dashboard tests use `promtool` when it is on the PATH; CI installs it.

## Names are an interface

Span attributes and metric names are read by the Prometheus rules, the dashboards, Langfuse and other people's queries. If you rename one, change the rules, `deploy/grafana/build.py` and the README table in the same pull request, rerun `make dashboards`, and note it in the changelog.

## Prices

`prices.yaml` holds example prices. Add a new row with `effective_from` when a price changes; never edit an old row, or past traces will be recomputed differently.

## Commits

Small, focused commits with messages in the imperative. Update `CHANGELOG.md` for anything a user would notice.
