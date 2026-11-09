# Valheim dashboard

See [the setup guide](../README.md) to install the dedicated server and observer,
configure the dashboard and start Docker Compose. Configuration is loaded from
the ignored root `.env`; branding is rendered from an explicit public whitelist.
No player identities are seeded. SQLite, telemetry and world data are created
locally and are ignored by Git.

Run regression tests from this directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

See [metric definitions](../docs/METRICS.md) and [third-party notices](THIRD_PARTY.md).
