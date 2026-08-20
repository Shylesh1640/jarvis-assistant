# External Connectors

Let the assistant reach an external service (issue tracker, notes app,
home-automation hub, ...) through a **connector abstraction**.

## How it works

* `Connector` is a `Protocol` in `src/jarvis/connectors/base.py` with
  `health_check` and `execute(input)`.
* `CONNECTORS` maps a config-file `type` to an implementation class.
* `ConnectorConfig` rows are read from `CONNECTORS_CONFIG_PATH`:

```json
{
  "connectors": [
    {
      "id": "github_issues",
      "name": "GitHub Issues",
      "description": "Create/list GitHub issues",
      "type": "github",
      "config": { "repo": "org/repo" },
      "enabled": true
    }
  ]
}
```

The `config` dict is passed to the implementation and **may contain
credentials** — it is stripped from every response.

## Settings

```
CONNECTORS_ENABLED=true
CONNECTORS_CONFIG_PATH=./config/connectors.json
```

Until then, connector routes/tools return a structured
`503 connector_not_configured` response and never call out.

## API

| Method | Path | Requires `?confirm=1` |
|---|---|---|
| GET | `/connectors` | no |
| GET | `/connectors/{id}` | no |
| POST | `/connectors/{id}/execute` | yes |

`GET /connectors` returns sanitised metadata (`id`, `name`, `description`,
`type`, `enabled`) — never the raw `config`.

## Agent tools

* `list_connectors` — **low risk** (auto-run)
* `run_connector` — **high risk**, approval-gated

## Safety

* Connectors are **off by default**.
* `execute` is a write: it requires `?confirm=1` at the API and approval at
  the tool layer — never auto-executed.
* Responses never include credentials; nothing about what a connector did is
  logged beyond its id.