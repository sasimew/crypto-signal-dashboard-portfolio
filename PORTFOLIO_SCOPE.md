# Portfolio Scope

This repository is intended for GitHub portfolio use.

## Safe To Publish

- `api_server.py`
- `bot.py`
- `config.py`
- `dashboard.html`
- `recheck.py`
- `docker-compose.yml`
- `nginx.conf`
- `nginx.bootstrap.conf`
- `enable_https.sh` after review
- `README.md`

## Keep Private

- `.env`
- `env`
- `.htpasswd`
- `data/`
- `DEPLOY_CHECKLIST.md`
- `setup_vps.sh`
- `telegram_guide.docx`
- any token, password, server IP, or private deployment detail

## Before Every GitHub Push

1. Review `git status`
2. Check staged files one by one
3. Search for secrets:
   - `ANTHROPIC_API_KEY`
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `ghp_`
   - `.htpasswd`
4. Confirm `data/` is not staged
5. Confirm no personal-only or production-only notes are staged

## Suggested Public Positioning

Use this project as a portfolio piece for:

- crypto product operations
- AI-assisted decision systems
- Flask + HTML dashboard engineering
- VPS deployment and reliability debugging
- signal quality analytics and recheck pipelines
