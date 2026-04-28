#!/usr/bin/env python3
"""
Export Log — ดึง signal_log.json และ recheck_log.json ออกมาพร้อม summary
รันบน VPS แล้วส่งไฟล์ให้ Claude / GPT วิเคราะห์

Usage:
  python3 export_log.py
  python3 export_log.py --days 7
  python3 export_log.py --summary
"""

import csv
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import config_set2 as cfg
except ImportError:
    import config as cfg


# ── ARGS ──────────────────────────────────────────────────────
args = sys.argv[1:]
days = 7
summary_only = False

for i, a in enumerate(args):
    if a == "--days" and i + 1 < len(args):
        try:
            days = int(args[i + 1])
        except ValueError:
            print("[WARN] --days ต้องเป็นตัวเลข, ใช้ค่า default = 7")
            days = 7
    if a == "--summary":
        summary_only = True


# ── LOAD ──────────────────────────────────────────────────────
def load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] โหลดไฟล์ไม่สำเร็จ: {path} -> {e}")
    return []


signal_log = load_json(cfg.LOG_FILE)
recheck_log = load_json(cfg.RECHECK_LOG)


# ── FILTER BY DATE ────────────────────────────────────────────
cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


signal_log = [
    s for s in signal_log
    if parse_iso(s.get("time")) and parse_iso(s.get("time")) >= cutoff_dt
]
recheck_log = [
    r for r in recheck_log
    if parse_iso(r.get("time")) and parse_iso(r.get("time")) >= cutoff_dt
]


# ── STATS ─────────────────────────────────────────────────────
total = len(signal_log)
filtered = sum(1 for s in signal_log if s.get("verdict") == "FILTERED")
ai_called = total - filtered
approved = sum(1 for s in signal_log if s.get("verdict") in ("APPROVED", "WEAK APPROVAL"))
rejected = sum(1 for s in signal_log if s.get("verdict") in ("REJECTED", "NO TRADE"))
errors = sum(1 for s in signal_log if s.get("verdict") == "ERROR")

wins = [r for r in recheck_log if "WIN" in str(r.get("outcome", "")).upper()]
losses = [r for r in recheck_log if "LOSS" in str(r.get("outcome", "")).upper()]
wr = round(len(wins) / (len(wins) + len(losses)) * 100, 1) if (wins or losses) else None


# ── INDICATOR SET PICKER ──────────────────────────────────────
IND_KEYS = [
    "EMA_SHORT", "EMA_MID", "EMA_LONG",
    "RSI_OVERSOLD", "RSI_OVERBOUGHT",
    "EMA_CROSS_BUFFER", "MIN_RR",
    "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL",
    "BB_PERIOD", "BB_STD", "VOLUME_AVG",
]

def ind_score(d):
    if not isinstance(d, dict):
        return -1
    return sum(1 for k in IND_KEYS if d.get(k) not in (None, "", "None"))

candidate_sets = [
    s.get("indicator_set")
    for s in reversed(signal_log)
    if isinstance(s.get("indicator_set"), dict)
]

ind_set = max(candidate_sets, key=ind_score, default={})

def pick_ind_value(key, default="—"):
    v = ind_set.get(key) if isinstance(ind_set, dict) else None
    if v not in (None, "", "None"):
        return v
    v = getattr(cfg, key, None)
    return v if v not in (None, "", "None") else default


# ── PRINT SUMMARY ─────────────────────────────────────────────
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

print("=" * 60)
print("CRYPTO SIGNAL LOG EXPORT")
print(f"Generated: {now_str}")
print(f"Period:    Last {days} days")
print("=" * 60)

if ind_set or any(hasattr(cfg, k) for k in IND_KEYS):
    print("\n[INDICATOR SET]")
    print(f"  Name:           {ind_set.get('name') if isinstance(ind_set, dict) else '—'}")
    print(f"  Configured:     {ind_set.get('configured') if isinstance(ind_set, dict) else '—'}")
    print(f"  Review after:   {ind_set.get('review_after') if isinstance(ind_set, dict) else '—'}")
    print(f"  EMA:            {pick_ind_value('EMA_SHORT')}/{pick_ind_value('EMA_MID')}/{pick_ind_value('EMA_LONG')}")
    print(f"  RSI zone:       {pick_ind_value('RSI_OVERSOLD')} / {pick_ind_value('RSI_OVERBOUGHT')}")
    print(f"  EMA buffer:     {pick_ind_value('EMA_CROSS_BUFFER')}")
    print(f"  Min R:R:        {pick_ind_value('MIN_RR')}")
    print(f"  MACD:           {pick_ind_value('MACD_FAST')}/{pick_ind_value('MACD_SLOW')}/{pick_ind_value('MACD_SIGNAL')}")
    print(f"  BB:             {pick_ind_value('BB_PERIOD')} period, {pick_ind_value('BB_STD')}SD")
    print(f"  Volume avg:     {pick_ind_value('VOLUME_AVG')} candles")

print("\n[SIGNAL SUMMARY]")
print(f"  Total scans:     {total}")
print(f"  Filtered (skip): {filtered}  ({round(filtered / total * 100, 1) if total else 0}%)")
print(f"  AI called:       {ai_called}")
print(f"  Approved:        {approved}")
print(f"  Rejected:        {rejected}")
print(f"  Errors:          {errors}")

print("\n[RECHECK ACCURACY]")
print(f"  Total rechecked: {len(recheck_log)}")
print(f"  Wins:            {len(wins)}")
print(f"  Losses:          {len(losses)}")
print(f"  Win Rate:        {wr}%" if wr is not None else "  Win Rate:        — (not enough data)")

print("\n[PER SYMBOL]")
for sym in ["BTCUSDT", "ETHUSDT", "BNBUSDT"]:
    sl = [s for s in signal_log if s.get("symbol") == sym]
    ap = [s for s in sl if s.get("verdict") in ("APPROVED", "WEAK APPROVAL")]
    fi = [s for s in sl if s.get("verdict") == "FILTERED"]
    rc = [r for r in recheck_log if r.get("symbol") == sym]
    w = [r for r in rc if "WIN" in str(r.get("outcome", "")).upper()]
    l = [r for r in rc if "LOSS" in str(r.get("outcome", "")).upper()]
    wr2 = round(len(w) / (len(w) + len(l)) * 100, 1) if (w or l) else None

    if wr2 is not None:
        print(f"  {sym}: {len(sl)} scans | {len(ap)} approved | {len(fi)} filtered | WR: {wr2}%")
    else:
        print(f"  {sym}: {len(sl)} scans | {len(ap)} approved | {len(fi)} filtered | WR: —")

print("=" * 60)

if summary_only:
    print("\n[--summary mode: ไม่ export full log]")
    sys.exit(0)


# ── EXPORT FILES ──────────────────────────────────────────────
base_dir = cfg.BASE_DIR if hasattr(cfg, "BASE_DIR") else os.path.dirname(os.path.abspath(__file__))
export_dir = os.path.join(base_dir, "export")
os.makedirs(export_dir, exist_ok=True)

date_tag = datetime.now().strftime("%Y%m%d_%H%M")


# 1) signal_log.json
out_json = os.path.join(export_dir, f"signal_log_{date_tag}.json")
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(signal_log, f, indent=2, ensure_ascii=False)


# 2) signal_log.csv
out_csv = os.path.join(export_dir, f"signal_log_{date_tag}.csv")
csv_rows = []

for s in signal_log:
    ind = s.get("indicators") or {}
    iset = s.get("indicator_set") or {}
    rc = s.get("recheck") or {}

    csv_rows.append({
        "id": s.get("id"),
        "time": str(s.get("time", ""))[:16].replace("T", " "),
        "symbol": s.get("symbol"),
        "direction": s.get("direction"),
        "price": s.get("price"),
        "change_pct": s.get("change"),

        "filter_passed": s.get("filter_passed"),
        "filter_reason": s.get("filter_reason"),

        "verdict": s.get("verdict"),
        "conf": s.get("conf"),
        "main_score_note": s.get("main_score_note"),
        "reason": s.get("reason"),
        "entry": s.get("entry"),
        "ssl": s.get("ssl"),
        "hsl": s.get("hsl"),
        "tp1": s.get("tp1"),
        "tp2": s.get("tp2"),
        "tp3": s.get("tp3"),

        "rsi_15": ind.get("rsi_15"),
        "rsi_1h": ind.get("rsi_1h"),
        "macd_hist": ind.get("macd_hist"),
        "bb_pct": ind.get("bb_pct"),
        "vol_ratio": ind.get("vol_ratio"),
        "ema_short_15": ind.get("ema_short_15"),
        "ema_mid_15": ind.get("ema_mid_15"),
        "swing_high": ind.get("swing_high_15m"),
        "swing_low": ind.get("swing_low_15m"),

        # SET1v4 fields — BUG FIX: เพิ่ม gate/conf fields ที่ขาดหายไป
        "pre_conf":      s.get("pre_conf"),
        "ai_conf":       s.get("ai_conf"),
        "gate_path":     s.get("gate_path"),
        "claude_called": s.get("claude_called"),
        "reason_code":   s.get("reason_code"),
        "reject_reason": s.get("reject_reason"),
        "strategy_used": s.get("strategy_used"),
        "regime":        s.get("regime"),
        "bot_version":   s.get("bot_version"),

        "recheck_outcome": rc.get("outcome"),
        "recheck_pnl":     rc.get("pnl_pct"),
        "recheck_level":   rc.get("level_hit"),
        "recheck_main_window": rc.get("main_window"),
        "recheck_benchmark_price": rc.get("benchmark_price"),
        "recheck_label": rc.get("recheck_label"),
        "recheck_evaluation_type": rc.get("evaluation_type"),
        "recheck_would_outcome": rc.get("would_outcome"),

        "ind_set_name": iset.get("name") if isinstance(iset, dict) else iset,
        "ema_config": f"{iset.get('EMA_SHORT')}/{iset.get('EMA_MID')}/{iset.get('EMA_LONG')}" if isinstance(iset, dict) else "",
        "rsi_zone": f"{iset.get('RSI_OVERSOLD')}/{iset.get('RSI_OVERBOUGHT')}" if isinstance(iset, dict) else "",
    })

if csv_rows:
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
        writer.writeheader()
        writer.writerows(csv_rows)


# 3) recheck_log.json
out_rc = os.path.join(export_dir, f"recheck_log_{date_tag}.json")
with open(out_rc, "w", encoding="utf-8") as f:
    json.dump(recheck_log, f, indent=2, ensure_ascii=False)


# 4) latest copies
latest_json = os.path.join(export_dir, "signal_log_latest.json")
latest_csv = os.path.join(export_dir, "signal_log_latest.csv")
latest_rc = os.path.join(export_dir, "recheck_log_latest.json")

shutil.copyfile(out_json, latest_json)
if csv_rows:
    shutil.copyfile(out_csv, latest_csv)
shutil.copyfile(out_rc, latest_rc)


print("\n[EXPORT COMPLETE]")
print(f"  📄 {out_json}")
print(f"  📊 {out_csv}  ← เปิดด้วย Excel ได้เลย")
print(f"  📄 {out_rc}")
print(f"  📄 {latest_json}")
print(f"  📊 {latest_csv}" if csv_rows else "  📊 signal_log_latest.csv — no rows")
print(f"  📄 {latest_rc}")

print("\nวิธี download จาก VPS:")
print(f"  scp USER@YOUR_SERVER:{export_dir}/*latest* ~/Desktop/")
print("=" * 60)
