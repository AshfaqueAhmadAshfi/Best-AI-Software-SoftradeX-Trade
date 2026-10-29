# Best-AI-Software-SoftradeX-Trade

Exness / MT5 trading dashboard: chart, market watch dials, multi-venue order flow,
news and bots — one FastAPI process serving the API, the WebSocket stream and the UI.

## Run on this PC (Windows, real MT5)

Double-click `start.bat` (or `python backend/app.py`), then open <http://127.0.0.1:8777/>.
Requires a running MetaTrader 5 terminal for live account/trading data; without it the
dashboard still works on its public market feed.

## Put it on a domain (upload → live)

```bash
cp .env.example .env      # DOMAIN=yourdomain.com
docker compose up -d --build
```

Step-by-step (DNS, VPS, HTTPS, updates, MT5 caveat): **[DEPLOY.md](DEPLOY.md)**

## cPanel / shared hosting (no MT5, API data)

cPanel runs Python apps through Passenger (WSGI, no WebSocket) and has no MT5
terminal, so the app switches to **hosted mode**: prices come from the public
market APIs and the browser polls HTTP instead of streaming. Upload
`passenger_wsgi.py`, `backend/`, `web/` and `requirements.txt` into the app root
created by *Setup Python App*, start it with `passenger_wsgi.py`, then RESTART.

Step-by-step: **[DEPLOY-CPANEL.md](DEPLOY-CPANEL.md)**
