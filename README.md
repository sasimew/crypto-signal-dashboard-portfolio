# Crypto Signal Dashboard Portfolio V2

A production-style crypto signal dashboard and bot system. The portfolio version now reflects the V2 dashboard workflow, including an updated indicator configuration layer without exposing private trading parameters.

The project is focused on:

- rule-based signal generation
- AI validation for medium/high-confidence setups
- Telegram alert delivery
- signal logging and recheck analytics
- Docker-based deployment behind Nginx

This repository is prepared as a **portfolio-safe version** of the project. Sensitive runtime files, local credentials, and production-only deployment materials are intentionally excluded.

## What This Project Does

The system analyzes crypto market data, scores potential setups, routes them through confidence gates, and records the results for dashboard review.

Core flow:

1. Fetch market data from Binance
2. Compute indicators and market regime
3. Score the setup
4. Route the setup through signal gates
5. Optionally call Claude for higher-confidence validation
6. Save signal logs
7. Send Telegram alerts when notification rules are met
8. Recheck logged signals later to measure outcome and quality

## Main Components

- `bot.py`
  Main signal engine, cron-run workflow, Telegram message building, and Claude call path.

- `api_server.py`
  Flask API for the dashboard, signal log access, recheck data, and manual analysis endpoints.

- `dashboard.html`
  Frontend dashboard for signal review, AI recheck, stats, and manual analysis.

- `recheck.py`
  Backfill and recheck engine used to evaluate historical logged signals.

- `config.py`
  Centralized thresholds, indicator settings, and signal logic configuration.

- `docker-compose.yml`
  Local and VPS runtime orchestration.

## Signal Logic Summary

The dashboard and bot revolve around a confidence-gated workflow:

- `< 25`
  Filtered out and not logged

- `25-34`
  Weak signal, logged without Claude

- `35-49`
  Bot-level weak or no-trade path

- `>= 50`
  Must have entry / TP / SL recorded and is eligible for recheck

- `>= 50` with stronger paths
  May call Claude for validation depending on gate logic

- `>= 70`
  High-confidence path, often used for stronger alert and Telegram behavior depending on verdict and route

## V2 Dashboard Update

V2 adds a cleaner indicator configuration structure and a more disciplined dashboard review flow while keeping the public repository portfolio-safe. The update highlights product maturity around signal filtering, confidence thresholds, Telegram notification rules, and post-signal quality checks without publishing the exact proprietary tuning details.

## Recheck Logic

Historical signal quality is tracked through a recheck flow:

- high-confidence logged signals with `conf >= 70` are prioritized for scheduled recheck
- qualified rejected signals retain entry / TP / SL levels for audit and later review
- recheck evaluates whether TP1 or stop levels were touched during the review window, not only the final reference price
- dashboard summary groups results into win / loss / pending / unknown

The current implementation has gone through several rounds of bug fixing around:

- missing TP/SL on rejected high-confidence rows
- duplicate signal IDs
- stale recheck rows
- reversed stop-loss direction
- cron env propagation

## Public Portfolio Scope

Included:

- dashboard UI logic
- API and bot logic
- config structure
- recheck architecture
- Docker / Nginx application structure

Excluded:

- API keys
- Telegram tokens
- `.htpasswd`
- runtime `data/`
- private deployment notes
- personal landing-page assets not needed for code review

## Running Locally

This public portfolio version assumes secrets are supplied via environment variables and are **not committed**.

Example environment variables:

- `ANTHROPIC_API_KEY`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Portfolio Notes

This repo is meant to showcase:

- product thinking around dashboard workflows
- system logic and rule design
- debugging and operations maturity
- AI-assisted validation layered on top of deterministic rules

Contact: `sasi.esports@gmail.com`

For a fuller internal reference, pair this repo with a separate private operations repository or internal notes.
