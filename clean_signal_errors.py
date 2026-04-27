#!/usr/bin/env python3
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import config_set2 as cfg
except ImportError:
    import config as cfg


def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_old_key_error(row):
    if row.get("verdict") != "ERROR":
        return False
    msg = str(row.get("error_msg") or row.get("reject_reason") or "").lower()
    return "anthropic_api_key not configured" in msg


def main():
    rows = load_json(cfg.LOG_FILE)
    before = len(rows)
    cleaned = [r for r in rows if not is_old_key_error(r)]
    removed = before - len(cleaned)
    if removed:
        save_json(cfg.LOG_FILE, cleaned)
    print(f"Removed {removed} old ANTHROPIC_API_KEY error rows from {cfg.LOG_FILE}")


if __name__ == "__main__":
    main()
