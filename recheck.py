#!/usr/bin/env python3
"""
Signal Recheck v4 — SET1v4
- รันทุก 00:00 TH (17:00 UTC via cron)
- เช็คทุก signal ที่ conf >= 50
- BUG-07 FIX: ลบ duplicate code block ออก (เดิมมี 2 version ต่อกัน)
- แก้ให้เก็บ history 7 วัน (RECHECK_LOG limit=500 → 7-day window)
- ใช้ราคาปัจจุบันตรวจ TP/SL (realtime check)
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfg

TZ_THAI = timezone(timedelta(hours=7))
def now_thai(): return datetime.now(TZ_THAI)
def log(m): print(f"[{now_thai().strftime('%Y-%m-%d %H:%M:%S')} TH] {m}", flush=True)

def get_price(sym):
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={sym}"
        with urllib.request.urlopen(url, timeout=8) as r:
            return float(json.loads(r.read().decode())["price"])
    except: return None

def get_price_at_recheck_time(sym, target_date):
    """
    ราคาใกล้เวลา recheck ของวัน target_date ที่ 00:00 TH ของวันถัดไป
    ใช้ candle 5m ฝั่ง spot เพื่อ backfill ย้อนหลังให้ได้ผล deterministic
    """
    try:
        recheck_dt_th = datetime(target_date.year, target_date.month, target_date.day,
                                 0, 0, 0, tzinfo=TZ_THAI) + timedelta(days=1)
        start_utc = recheck_dt_th.astimezone(timezone.utc)
        start_ms = int(start_utc.timestamp() * 1000)
        url = (
            f"https://api.binance.com/api/v3/klines?symbol={sym}"
            f"&interval=5m&startTime={start_ms}&limit=1"
        )
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
            if isinstance(data, list) and len(data):
                return float(data[0][4])
    except Exception as e:
        log(f"Historical price error {sym} {target_date}: {e}")
    return get_price(sym)

def load_json(p):
    try:
        if os.path.exists(p):
            with open(p) as f: return json.load(f)
    except: pass
    return []

def save_json(p, d):
    with open(p, "w") as f: json.dump(d, f, indent=2, ensure_ascii=False)

def safe_float(v):
    try: return float(v) if v else 0.0
    except: return 0.0

def check(sig, now_price):
    """
    ตรวจสอบ outcome ของ signal
    WIN TP1/TP2/TP3 / LOSS / SOFT SL / PENDING
    """
    d   = sig.get("direction", "Long")
    e   = safe_float(sig.get("entry"))
    hsl = safe_float(sig.get("hsl"))
    ssl = safe_float(sig.get("ssl"))
    tp1 = safe_float(sig.get("tp1"))
    tp2 = safe_float(sig.get("tp2"))
    tp3 = safe_float(sig.get("tp3"))

    if not e or not now_price:
        return "UNKNOWN", 0.0, "—"

    pnl = (now_price - e) / e * 100 if d == "Long" else (e - now_price) / e * 100

    if d == "Long":
        if tp3 and now_price >= tp3:   outcome = "WIN TP3 ✅"
        elif tp2 and now_price >= tp2: outcome = "WIN TP2 ✅"
        elif tp1 and now_price >= tp1: outcome = "WIN TP1 ✅"
        elif hsl and now_price <= hsl: outcome = "LOSS ❌"
        elif ssl and now_price <= ssl: outcome = "SOFT SL ⚠️"
        else:                          outcome = "PENDING"
    else:  # Short
        if tp3 and now_price <= tp3:   outcome = "WIN TP3 ✅"
        elif tp2 and now_price <= tp2: outcome = "WIN TP2 ✅"
        elif tp1 and now_price <= tp1: outcome = "WIN TP1 ✅"
        elif hsl and now_price >= hsl: outcome = "LOSS ❌"
        elif ssl and now_price >= ssl: outcome = "SOFT SL ⚠️"
        else:                          outcome = "PENDING"

    level = "—"
    if "TP3" in outcome:  level = f"TP3 ${tp3:,.2f}"
    elif "TP2" in outcome: level = f"TP2 ${tp2:,.2f}"
    elif "TP1" in outcome: level = f"TP1 ${tp1:,.2f}"
    elif "LOSS" in outcome: level = f"HSL ${hsl:,.2f}"
    elif "SOFT" in outcome: level = f"SSL ${ssl:,.2f}"

    return outcome, round(pnl, 2), level

def fmt(v):
    if not v: return "—"
    try: return f"${float(v):,.2f}"
    except: return "—"

def send_tg(msg):
    if not cfg.TELEGRAM_TOKEN or "YOUR" in cfg.TELEGRAM_TOKEN: return
    params = urllib.parse.urlencode({
        "chat_id": cfg.TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    })
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage?{params}",
            timeout=10
        )
    except Exception as e:
        log(f"Telegram error: {e}")

def parse_date_arg(name):
    prefix = f"--{name}="
    for arg in sys.argv[1:]:
        if arg.startswith(prefix):
            return datetime.strptime(arg[len(prefix):], "%Y-%m-%d").date()
    return None

def has_flag(name):
    return f"--{name}" in sys.argv[1:]

def process_date(logs, rechk, target_date, send_summary=True):
    existing_pairs = {
        (r.get("signal_id"), r.get("recheck_date"))
        for r in rechk
        if r.get("signal_id") and r.get("recheck_date")
    }

    day_start = datetime(target_date.year, target_date.month, target_date.day,
                         0, 0, 0, tzinfo=TZ_THAI)
    day_end   = day_start + timedelta(days=1)

    to_check = []
    for s in logs:
        conf = safe_float(s.get("conf"))
        if conf < 50:
            continue
        pair = (s.get("id"), target_date.isoformat())
        if pair in existing_pairs:
            continue
        try:
            t = datetime.fromisoformat(s.get("time","").replace("Z","+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            t_thai = t.astimezone(TZ_THAI)
            if day_start <= t_thai < day_end:
                to_check.append(s)
        except:
            continue

    log(f"Recheck วัน {target_date} (TH) — พบ {len(to_check)} signals (conf >= 50)")

    if not to_check:
        if send_summary:
            msg = (f"📊 *Recheck — {target_date.strftime('%d %b %Y')}*\n\n"
                   f"ไม่มี signal ที่ต้อง recheck\n"
                   f"_(เฉพาะ conf >= 50 | 00:00-23:59 TH)_\n\n"
                   f"_⚡ sasi.asia/dashboard · SET1v4_")
            send_tg(msg)
        return []

    results = []
    for sig in to_check:
        sym = sig["symbol"]
        now = get_price_at_recheck_time(sym, target_date)
        if not now:
            log(f"  ⚠️ ดึงราคา {sym} ไม่ได้")
            continue

        outcome, pnl, level = check(sig, now)
        log(f"  {sig.get('id','?')}: {sig.get('direction')} @ {fmt(sig.get('entry'))} "
            f"→ ref={fmt(now)} | {outcome} {pnl:+.2f}% | hit={level}")

        r = {
            "signal_id":     sig.get("id"),
            "symbol":        sym,
            "direction":     sig.get("direction"),
            "verdict":       sig.get("verdict"),
            "conf":          sig.get("conf"),
            "pre_conf":      sig.get("pre_conf"),
            "gate_path":     sig.get("gate_path", "—"),
            "strategy":      sig.get("strategy_used","—"),
            "regime":        sig.get("regime","—"),
            "entry":         sig.get("entry"),
            "ssl":           sig.get("ssl"),
            "hsl":           sig.get("hsl"),
            "tp1":           sig.get("tp1"),
            "tp2":           sig.get("tp2"),
            "tp3":           sig.get("tp3"),
            "current_price": now,
            "outcome":       outcome,
            "pnl_pct":       pnl,
            "level_hit":     level,
            "recheck_date":  target_date.isoformat(),
            "time":          now_thai().isoformat(),
        }
        results.append(r)
        rechk.insert(0, r)

        # latest recheck summary on signal_log for dashboard quick view
        for s in logs:
            if s.get("id") == sig.get("id"):
                s["recheck"] = {
                    "outcome":   outcome,
                    "pnl_pct":   pnl,
                    "level_hit": level,
                    "price":     now,
                    "time":      r["time"],
                    "recheck_date": target_date.isoformat(),
                }
                break

    if send_summary:
        wins    = [r for r in results if "WIN"     in r["outcome"]]
        losses  = [r for r in results if "LOSS"    in r["outcome"]]
        softsl  = [r for r in results if "SOFT"    in r["outcome"]]
        pending = [r for r in results if "PENDING" in r["outcome"]]
        wr = round(len(wins)/(len(wins)+len(losses))*100) if (wins or losses) else None

        tp1_hits = sum(1 for r in wins if "TP1" in r["outcome"])
        tp2_hits = sum(1 for r in wins if "TP2" in r["outcome"])
        tp3_hits = sum(1 for r in wins if "TP3" in r["outcome"])

        avg_win  = round(sum(r["pnl_pct"] for r in wins)  /len(wins),  2) if wins   else 0
        avg_loss = round(sum(r["pnl_pct"] for r in losses)/len(losses), 2) if losses else 0

        date_s = target_date.strftime("%d %b %Y")
        msg  = f"📊 *Signal Recheck — {date_s}*\n"
        msg += f"_(conf >= 50 | 00:00-23:59 TH)_\n\n"
        msg += f"📈 WIN: *{len(wins)}* | 📉 LOSS: *{len(losses)}* | ⚠️ SoftSL: {len(softsl)} | ⏳ Pending: {len(pending)}\n"
        if wr is not None:
            msg += f"🎯 *Win Rate: {wr}%*\n\n"

        if wins:
            msg += f"✅ *Winners* (avg +{avg_win:.2f}%):\n"
            for r in wins[:5]:
                sym = r['symbol'].replace('USDT','')
                msg += f"  {sym} {r['direction']} {r['pnl_pct']:+.2f}% ({r['outcome'].split()[1]})\n"
            if tp1_hits or tp2_hits or tp3_hits:
                msg += f"  _TP hits: TP1×{tp1_hits} TP2×{tp2_hits} TP3×{tp3_hits}_\n"

        if losses:
            msg += f"\n❌ *Losers* (avg {avg_loss:.2f}%):\n"
            for r in losses[:5]:
                sym = r['symbol'].replace('USDT','')
                msg += f"  {sym} {r['direction']} {r['pnl_pct']:+.2f}%\n"

        if softsl:
            msg += f"\n⚠️ *Soft SL hit:*\n"
            for r in softsl[:3]:
                sym = r['symbol'].replace('USDT','')
                msg += f"  {sym} {r['direction']} {r['pnl_pct']:+.2f}%\n"

        msg += f"\n_⚡ sasi.asia/dashboard · SET1v4_"
        send_tg(msg)

    return results

def main():
    log("=" * 50)
    log("🔍 Signal Recheck v4 — SET1v4")

    logs  = load_json(cfg.LOG_FILE)
    rechk = load_json(cfg.RECHECK_LOG)
    today_th = now_thai().date()
    from_date = parse_date_arg("from")
    to_date = parse_date_arg("to") or today_th
    is_backfill = has_flag("backfill") or bool(from_date)

    if is_backfill:
        start_date = from_date or today_th
        if start_date > to_date:
            start_date, to_date = to_date, start_date
        log(f"📚 Backfill mode — {start_date} ถึง {to_date}")
        d = start_date
        total = 0
        while d <= to_date:
            total += len(process_date(logs, rechk, d, send_summary=False))
            d += timedelta(days=1)
        save_json(cfg.LOG_FILE, logs)
        save_json(cfg.RECHECK_LOG, rechk[:2000])
        log(f"✅ Backfill done — added {total} recheck rows")
        return

    yesterday_th = today_th - timedelta(days=1)
    results = process_date(logs, rechk, yesterday_th, send_summary=True)
    save_json(cfg.LOG_FILE, logs)
    save_json(cfg.RECHECK_LOG, rechk[:2000])
    log(f"✅ Done — rows:{len(results)}")

if __name__ == "__main__":
    main()
