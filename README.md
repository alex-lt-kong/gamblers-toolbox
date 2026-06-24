# Gambler's Terminal

One FastAPI app: a landing page mounting each tool as a self-contained **module**
(`modules/<slug>/` exposing `MODULE`) — one port, shared auth, Swagger at `/docs`.

| Module | URL | What |
|---|---|---|
| P/E Monitor | `/pe-monitor` | Forward & TTM P/E watchlist. |
| Pyramiding Calculator | `/averaging-calc` | Shares to add to move a position's P/L% to a target. |
| AI Ratios | `/ai-ratios` | S&P 500 AI-exposure share. |

## Run

```bash
pip install -r requirements.txt
cp config.sample.toml config.toml
cp modules/pe_monitor/config.sample.toml modules/pe_monitor/config.toml
python -m core --config config.toml
```

`--config` is mandatory (or `GAMBLERS_TOOLBOX_CONFIG`). It holds only shared concerns
(`host`, `port`, `secret_key`, `auth_tokens`, `enable_schedulers`); modules keep their
own. Schedulers run in-process — run one worker (or `enable_schedulers = false` on
extra replicas).

**Auth** is off until `auth_tokens` is set (then a strong `secret_key` is required);
open `?token=<uuid>` once for a cookie. **Add a module**: drop a package under
`modules/` exposing `MODULE`.

## Tests

```bash
pip install -r requirements-dev.txt && python -m pytest
```
