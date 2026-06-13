# Production Bug Report — 2026-06-13

## Summary

Production `https://sasi.asia/dashboard/` showed repeated `ERROR[RUNTIME_ERROR]: Claude transport error (attempt 3/3)` rows and no fresh signal alerts in Telegram.

## Root Cause

Two separate issues were active in production:

1. The VPS was still running an older `bot_set3.py` that used `TIER4_CLAUDE`.
2. Anthropic API requests were failing with `400 invalid_request_error` because the account credit balance was too low.

Because the old production path still depended on Claude for Tier 4 decisions, every qualifying signal degraded into `ERROR` instead of a usable bot review outcome.

## Evidence

- Production health still showed `bot_module=bot_set3`, but live rows were logging `gate_path=TIER4_CLAUDE`.
- Recent production rows included:
  - `2026-06-13 23:35 TH TIER4_CLAUDE ERROR`
  - `2026-06-13 23:25 TH TIER4_CLAUDE ERROR`
- Direct test from the running production bot container returned:

```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."
  }
}
```

## User Impact

- Dashboard showed repeated `ERROR` rows instead of reviewed signals.
- No fresh Telegram signal alerts were sent because there were no `APPROVED` or `WEAK APPROVAL` outcomes to notify.
- The previous production build also did not mirror signal alerts to `Telegram 2`.

## Fix Applied

### 1. Promote bot-review flow to production

- Synced the newer `bot_set3.py` to production.
- Production Tier 4 now uses `TIER4_BOT_REVIEW` instead of `TIER4_CLAUDE`.

### 2. Restore Telegram delivery redundancy

- Added optional secondary routing using:
  - `TELEGRAM_TOKEN2`
  - `TELEGRAM_CHAT2_ID`
- Updated cron environment export so the bot can use secondary Telegram values during scheduled runs.

## Verification

After deploy:

- Production generated:
  - `2026-06-13 23:40 TH TIER4_BOT_REVIEW NO TRADE`
- Manual Telegram test from production bot returned:
  - `Telegram primary: sent`
  - `Telegram secondary: sent`

## Current Status

- Production no longer depends on Claude for the current Tier 4 review path.
- Telegram primary and secondary delivery are both working.
- Historical `TIER4_CLAUDE ERROR` rows may still appear in the dashboard because they are old log entries, not new failures.

## Recommended Follow-up

1. Hide or relabel historical `TIER4_CLAUDE ERROR` rows in the dashboard to avoid confusion.
2. Keep the production bot on the bot-review path unless Anthropic billing is intentionally restored.
3. Maintain secondary Telegram routing for alert redundancy.
