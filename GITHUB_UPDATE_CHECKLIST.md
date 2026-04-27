# GitHub Update Checklist

Use this checklist every time code, dashboard files, bot logic, docs, deployment files, or release notes are updated. The default rule is: if the update matters, commit it and push it to GitHub.

## Project Paths

- Working bot folder:

```bash
/Users/sasi/Desktop/crypto_bot
```

- GitHub publishing repo:

```bash
/Users/sasi/Documents/Playground
```

- GitHub remote:

```bash
https://github.com/sasimew/crypto-signal-dashboard-portfolio
```

Important: `/Users/sasi/Desktop/crypto_bot` is the active working folder but is not the Git repo. AI should copy public-safe files from the bot folder into `/Users/sasi/Documents/Playground`, then commit and push from `/Users/sasi/Documents/Playground`.

## Public-Safe Copy Flow

Copy only reviewed public-safe files from the working bot folder to the GitHub publishing repo:

```bash
cp /Users/sasi/Desktop/crypto_bot/api_server.py /Users/sasi/Documents/Playground/api_server.py
cp /Users/sasi/Desktop/crypto_bot/recheck.py /Users/sasi/Documents/Playground/recheck.py
cp /Users/sasi/Desktop/crypto_bot/dashboard.html /Users/sasi/Documents/Playground/dashboard.html
cp /Users/sasi/Desktop/crypto_bot/docker-compose.yml /Users/sasi/Documents/Playground/docker-compose.yml
cp /Users/sasi/Desktop/crypto_bot/Dockerfile /Users/sasi/Documents/Playground/Dockerfile
cp /Users/sasi/Desktop/crypto_bot/export_log.py /Users/sasi/Documents/Playground/export_log.py
cp /Users/sasi/Desktop/crypto_bot/clean_signal_errors.py /Users/sasi/Documents/Playground/clean_signal_errors.py
cp /Users/sasi/Desktop/crypto_bot/bot_set2.py /Users/sasi/Documents/Playground/bot_set2.py
cp /Users/sasi/Desktop/crypto_bot/config_set2.py /Users/sasi/Documents/Playground/config_set2.py
cp /Users/sasi/Desktop/crypto_bot/bot_set2.py /Users/sasi/Documents/Playground/bot.py
cp /Users/sasi/Desktop/crypto_bot/config_set2.py /Users/sasi/Documents/Playground/config.py
```

Do not copy these files or folders to GitHub:

- `.env`
- `env`
- `.htpasswd`
- `data/`
- `export/`
- `DEPLOY_CHECKLIST.md`
- `setup_vps.sh`
- private server IPs, credentials, tokens, or personal deployment notes

## 1. Before Editing

- Confirm the target folder is the correct project.
- Check the current worktree:

```bash
git status -sb
```

- Review existing uncommitted changes before editing.
- Do not overwrite user changes or generated production logs.
- Never commit secrets, tokens, passwords, server IPs, `.env`, `.htpasswd`, runtime `data/`, or private deployment notes.

## 2. After Editing

- Review changed files:

```bash
git status --short
git diff
```

- Run the smallest useful validation for the change.
- For Python bot changes:

```bash
PYTHONPYCACHEPREFIX=/tmp/crypto_bot_pycache python3 -m py_compile *.py
```

- For Docker or deploy changes, validate config on a machine with Docker:

```bash
docker compose config
```

## 3. Secret Safety Check

Run a secret-oriented search before staging:

```bash
rg -n "ghp_|sk-|ANTHROPIC_API_KEY=|TELEGRAM_TOKEN=|TELEGRAM_CHAT_ID=|TG_BOT_TOKEN=|password\\s*=|token\\s*=|api_key\\s*="
```

Allowed matches should be placeholders or documentation only. Real keys must be removed before commit.

## 4. Stage Only Intended Files

- Stage explicit files only:

```bash
git add README.md bot_set2.py config_set2.py
```

- Do not use `git add .` unless every new and modified file has been reviewed.
- Recheck staged files:

```bash
git diff --cached --name-only
git diff --cached
```

## 5. Commit Best Practice

Use a clear commit message:

```bash
git commit -m "type: short description"
```

Recommended types:

- `feat:` for new functionality
- `fix:` for bug fixes
- `docs:` for documentation
- `config:` for settings or deployment config
- `test:` for tests or validation tooling
- `chore:` for maintenance

Good examples:

```bash
git commit -m "fix: filter low-confidence rejected telegram alerts"
git commit -m "docs: add dashboard v2 portfolio notes"
git commit -m "config: use env file in cron container"
```

## 6. Push Every Update

Push after every completed commit from the GitHub publishing repo:

```bash
cd /Users/sasi/Documents/Playground
git push origin main
```

If a tag or release is needed:

```bash
git tag -a v2.0.0 -m "Release v2.0.0"
git push origin v2.0.0
```

## 7. GitHub Release Checklist

Create a GitHub Release when the update is user-facing, deployable, or marks a stable version.

Release title example:

```text
Crypto Signal Dashboard V2
```

Release notes should summarize what changed without exposing private logic:

```text
Dashboard V2 update with improved indicator configuration, safer notification rules, and updated recheck documentation. Proprietary trading parameters are intentionally excluded.
```

## 8. Final Verification

After pushing:

```bash
git status -sb
git log --oneline -3
```

Confirm:

- local branch is clean except intentionally untracked files
- commit is visible on GitHub
- release tag is visible if one was created
- no keys or production runtime files were included

## Standing Rule

From now on, every completed code or documentation update should follow this flow:

1. edit
2. validate
3. copy public-safe files to `/Users/sasi/Documents/Playground`
4. secret scan
5. stage intended files only
6. commit with a clear message
7. push to GitHub from `/Users/sasi/Documents/Playground`
8. publish a release when the update is deployable or versioned
