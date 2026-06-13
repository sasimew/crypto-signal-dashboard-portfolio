# SASI.ASIA Platform Stack

This repository contains the production web stack behind `sasi.asia`.

## What This Project Includes

- Public SASI.ASIA portfolio site in [`home/`](/Users/sasi/Desktop/crypto_bot/home)
- Market Intelligence Radar news proxy in [`api_server.py`](/Users/sasi/Desktop/crypto_bot/api_server.py)
- `SET3v1` bot runtime in [`bot_set3.py`](/Users/sasi/Desktop/crypto_bot/bot_set3.py)
- Docker-based runtime for the API, bot worker, certbot, and shared nginx
- Shared nginx routing for:
  - `https://sasi.asia/`
  - `https://sasi.asia/insights.html`
  - `https://sasi.asia/api/news`
  - private dashboard and internal API routes

## Portfolio Scope

The portfolio site is a strategic advisor website for Sasion Nanthaphiriyakit, covering:

- Home / positioning
- Advisory services
- Case studies
- Insights
- Contact

The `Market Intelligence Radar` section on `insights.html` is powered by a backend proxy and in-memory caching so API keys are never exposed to the browser.

## Deployment Notes

- Public website files are mounted from `home/`
- Backend service runs through Docker on port `3000`
- API defaults to loading `bot_set3` first and reports the active bot/config modules at `/health`
- nginx routes `/api/news` to the backend and serves the public site from the same domain
- Secrets must stay in `.env` on the server only and must never be committed
