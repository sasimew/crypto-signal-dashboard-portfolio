#!/usr/bin/env python3
"""
Signal Recheck Set2V1
- รันทุก 00:00 TH (17:00 UTC via cron)
- เช็คทุก signal ที่ conf >= 70
- ใช้ 4h หลัง signal เป็น benchmark หลัก
- ตัดสิน WIN/LOSS จาก P/L ณ benchmark หลักเท่านั้น
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import config_set2 as cfg
    import bot_set2 as bot_module
except ImportError:
    import config as cfg
    import bot as bot_module

TZ_THAI = timezone(timedelta(hours=7))
def now_thai(): return datetime.now(TZ_THAI)
def log(m): print(f"[{now_thai().strftime('%Y-%m-%d %H:%M:%S')} TH] {m}", flush=True)
BOT_VERSION = getattr(cfg, "BOT_VERSION", "SET2v2")
RECHECK_VERSION = "Signal Recheck Set2V1"
MAIN_WINDOW = "4h"

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

def get_intraday_klines(sym, start_th, end_th, interval="5m"):
    """
    ดึง candles ตั้งแต่หลัง signal จนถึงสิ้นวัน recheck เพื่อดูว่า TP/SL เคย hit หรือไม่
    ใช้ลำดับ candle เพื่อเลี่ยงปัญหา daily high/low ที่ไม่รู้ว่า TP หรือ SL มาก่อน
    """
    try:
        start_ms = int(start_th.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end_th.astimezone(timezone.utc).timestamp() * 1000)
        url = (
            f"https://api.binance.com/api/v3/klines?symbol={sym}"
            f"&interval={interval}&startTime={start_ms}&endTime={end_ms}&limit=1000"
        )
        with urllib.request.urlopen(url, timeout=12) as r:
            data = json.loads(r.read().decode())
            return data if isinstance(data, list) else []
    except Exception as e:
        log(f"Intraday kline error {sym}: {e}")
        return []

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

def check(sig, now_price, candles=None):
    """
    ตรวจสอบ outcome:
      1) ใช้ P/L ณราคาปิดของ window เป็นตัวตัดสิน WIN/LOSS เท่านั้น
      2) high/low ใน window ใช้เป็น context ว่าเคยแตะ TP/SL หรือไม่ แต่ไม่ override outcome
    ถ้าไม่มี entry หรือไม่มีราคาที่ใช้ recheck จะเป็น UNKNOWN
    """
    d = sig.get("direction", "Long")
    e = safe_float(sig.get("entry"))

    if not e or not now_price:
        return "UNKNOWN", 0.0, "—", None, None

    tp1 = safe_float(sig.get("tp1"))
    tp2 = safe_float(sig.get("tp2"))
    tp3 = safe_float(sig.get("tp3"))
    ssl = safe_float(sig.get("ssl"))
    high = None
    low = None
    touch = None

    if candles:
        highs = [safe_float(c[2]) for c in candles if len(c) > 3]
        lows = [safe_float(c[3]) for c in candles if len(c) > 3]
        highs = [x for x in highs if x]
        lows = [x for x in lows if x]
        high = max(highs) if highs else None
        low = min(lows) if lows else None

        for c in candles:
            if len(c) <= 3:
                continue
            c_high = safe_float(c[2])
            c_low = safe_float(c[3])
            if d == "Long":
                hit_tp3 = tp3 and c_high >= tp3
                hit_tp2 = tp2 and c_high >= tp2
                hit_tp1 = tp1 and c_high >= tp1
                hit_sl = ssl and c_low <= ssl
                if hit_tp3:
                    touch = "Touched TP3"
                elif hit_tp2 and touch not in ("Touched TP3",):
                    touch = "Touched TP2"
                elif hit_tp1 and touch not in ("Touched TP3", "Touched TP2"):
                    touch = "Touched TP1"
                elif hit_sl and not touch:
                    touch = "Touched Soft SL"
            elif d == "Short":
                hit_tp3 = tp3 and c_low <= tp3
                hit_tp2 = tp2 and c_low <= tp2
                hit_tp1 = tp1 and c_low <= tp1
                hit_sl = ssl and c_high >= ssl
                if hit_tp3:
                    touch = "Touched TP3"
                elif hit_tp2 and touch not in ("Touched TP3",):
                    touch = "Touched TP2"
                elif hit_tp1 and touch not in ("Touched TP3", "Touched TP2"):
                    touch = "Touched TP1"
                elif hit_sl and not touch:
                    touch = "Touched Soft SL"

    pnl = (now_price - e) / e * 100 if d == "Long" else (e - now_price) / e * 100
    pnl = round(pnl, 2)

    if pnl > 0:
        outcome = "WIN ✅"
        level = "P/L > 0"
    elif pnl < 0:
        outcome = "LOSS ❌"
        level = "P/L < 0"
    else:
        outcome = "0"
        level = "P/L = 0"

    if touch:
        level = f"{level}; {touch}"

    return outcome, pnl, level, high, low

def filter_candles_until(candles, end_th):
    end_ms = int(end_th.astimezone(timezone.utc).timestamp() * 1000)
    return [c for c in (candles or []) if c and int(c[0]) < end_ms]

def candle_close(candles, fallback):
    try:
        if candles:
            return safe_float(candles[-1][4]) or fallback
    except Exception:
        pass
    return fallback

def check_windows(sig, candles, fallback_price, sig_time_th, day_end):
    windows = {}
    for label, hours in (("1h", 1), ("2h", 2), ("4h", 4)):
        end_th = min(sig_time_th + timedelta(hours=hours), day_end)
        subset = filter_candles_until(candles, end_th)
        ref_price = candle_close(subset, fallback_price)
        outcome, pnl, level, high, low = check(sig, ref_price, subset)
        windows[label] = {
            "hours": hours,
            "end_time_thai": end_th.isoformat(),
            "price": ref_price,
            "outcome": outcome,
            "pnl_pct": pnl,
            "level_hit": level,
            "high": high,
            "low": low,
        }
    return windows

def classify_recheck_outcome(sig, outcome, level):
    """
    REJECTED rows are validation rows, not trades.
    If a rejected setup would have reached TP, mark it as a missed opportunity;
    otherwise mark the rejection as valid so dashboard win rate is not polluted.
    """
    if sig.get("verdict") != "REJECTED":
        return outcome, "trade_result", None
    if "WIN" in str(outcome):
        return f"MISSED_TP ⚠️ {level}", "rejected_validation", outcome
    if "UNKNOWN" in str(outcome):
        return "UNKNOWN", "rejected_validation", outcome
    if str(outcome).strip() == "0":
        return "REJECT_NEUTRAL", "rejected_validation", outcome
    return "VALID_REJECT ✅", "rejected_validation", outcome

def fmt(v):
    if not v: return "—"
    try: return f"${float(v):,.2f}"
    except: return "—"

def migrate_missing_levels(logs):
    """
    One-time / idempotent migration:
    เติม TP/SL/Entry ให้ log เก่าที่ conf >= 70 และมี direction/price
    แบบ aggressive เพื่อให้ recheck คำนวณได้ แม้แถวเก่าจะไม่มี suggested_* เดิม
    """
    changed = 0
    for s in logs:
        conf = safe_float(s.get("conf"))
        direction = s.get("direction")
        price = safe_float(s.get("price"))
        verdict = str(s.get("verdict", ""))
        if verdict == "ERROR" or conf < 70 or direction not in ("Long", "Short") or not price:
            continue

        fallback = bot_module.calc_fallback_levels(price, direction)
        entry = s.get("entry") or s.get("suggested_entry") or str(round(price, 2))
        before = {
            "entry": s.get("entry"),
            "ssl": s.get("ssl"),
            "hsl": s.get("hsl"),
            "tp1": s.get("tp1"),
            "tp2": s.get("tp2"),
            "tp3": s.get("tp3"),
            "suggested_entry": s.get("suggested_entry"),
            "suggested_ssl": s.get("suggested_ssl"),
            "suggested_hsl": s.get("suggested_hsl"),
            "suggested_tp1": s.get("suggested_tp1"),
            "suggested_tp2": s.get("suggested_tp2"),
            "suggested_tp3": s.get("suggested_tp3"),
        }
        normalized = bot_module.normalize_trade_levels(entry, direction, {
            "entry": entry,
            "ssl": s.get("ssl") or s.get("suggested_ssl") or fallback["ssl"],
            "hsl": s.get("hsl") or s.get("suggested_hsl") or fallback["hsl"],
            "tp1": s.get("tp1") or s.get("suggested_tp1") or fallback["tp1"],
            "tp2": s.get("tp2") or s.get("suggested_tp2") or fallback["tp2"],
            "tp3": s.get("tp3") or s.get("suggested_tp3") or fallback["tp3"],
            "suggested_entry": s.get("suggested_entry") or entry,
            "suggested_ssl": s.get("suggested_ssl"),
            "suggested_hsl": s.get("suggested_hsl"),
            "suggested_tp1": s.get("suggested_tp1"),
            "suggested_tp2": s.get("suggested_tp2"),
            "suggested_tp3": s.get("suggested_tp3"),
        })

        s["entry"] = normalized["entry"]
        s["ssl"] = normalized["ssl"]
        s["hsl"] = normalized["hsl"]
        s["tp1"] = normalized["tp1"]
        s["tp2"] = normalized["tp2"]
        s["tp3"] = normalized["tp3"]
        s["suggested_entry"] = normalized["suggested_entry"]
        s["suggested_ssl"] = normalized["suggested_ssl"]
        s["suggested_hsl"] = normalized["suggested_hsl"]
        s["suggested_tp1"] = normalized["suggested_tp1"]
        s["suggested_tp2"] = normalized["suggested_tp2"]
        s["suggested_tp3"] = normalized["suggested_tp3"]
        after = {
            "entry": s.get("entry"),
            "ssl": s.get("ssl"),
            "hsl": s.get("hsl"),
            "tp1": s.get("tp1"),
            "tp2": s.get("tp2"),
            "tp3": s.get("tp3"),
            "suggested_entry": s.get("suggested_entry"),
            "suggested_ssl": s.get("suggested_ssl"),
            "suggested_hsl": s.get("suggested_hsl"),
            "suggested_tp1": s.get("suggested_tp1"),
            "suggested_tp2": s.get("suggested_tp2"),
            "suggested_tp3": s.get("suggested_tp3"),
        }
        if after != before:
            changed += 1

    if changed:
        log(f"🛠 Migrated TP/SL for {changed} rows (conf>=70)")
    return changed

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

def process_date(logs, rechk, target_date, send_summary=True, replace_existing=False):
    if replace_existing:
        rechk[:] = [
            r for r in rechk
            if r.get("recheck_date") != target_date.isoformat()
        ]

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
        if conf < 70 or s.get("direction") not in ("Long", "Short"):
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

    log(f"Recheck วัน {target_date} (TH) — พบ {len(to_check)} signals (conf >= 70)")

    if not to_check:
        if send_summary:
            msg = (f"📊 *Recheck — {target_date.strftime('%d %b %Y')}*\n\n"
                   f"ไม่มี signal ที่ต้อง recheck\n"
                   f"_(เฉพาะ conf >= 70 | 00:00-23:59 TH)_\n\n"
                   f"_⚡ sasi.asia/dashboard · {BOT_VERSION}_")
            send_tg(msg)
        return []

    results = []
    for sig in to_check:
        sym = sig["symbol"]
        now = get_price_at_recheck_time(sym, target_date)
        if not now:
            log(f"  ⚠️ ดึงราคา {sym} ไม่ได้")
            continue

        try:
            sig_time = datetime.fromisoformat(sig.get("time", "").replace("Z", "+00:00"))
            if sig_time.tzinfo is None:
                sig_time = sig_time.replace(tzinfo=timezone.utc)
            sig_time_th = sig_time.astimezone(TZ_THAI)
        except Exception:
            sig_time_th = day_start
        candles = get_intraday_klines(sym, max(sig_time_th, day_start), day_end)
        outcome_day, pnl_day, level_day, day_high, day_low = check(sig, now, candles)
        windows = check_windows(sig, candles, now, max(sig_time_th, day_start), day_end)
        main = windows.get(MAIN_WINDOW) or {
            "outcome": outcome_day, "pnl_pct": pnl_day, "level_hit": level_day,
            "price": now, "high": day_high, "low": day_low,
        }
        raw_outcome = main["outcome"]
        pnl = main["pnl_pct"]
        level = main["level_hit"]
        outcome, evaluation_type, would_outcome = classify_recheck_outcome(sig, raw_outcome, level)
        main_label = MAIN_WINDOW
        log(f"  {sig.get('id','?')}: {sig.get('direction')} @ {fmt(sig.get('entry'))} "
            f"→ {main_label}={fmt(main.get('price'))} | H{main_label}={fmt(main.get('high'))} L{main_label}={fmt(main.get('low'))} "
            f"| Day H={fmt(day_high)} L={fmt(day_low)} | {outcome} {pnl:+.2f}% | hit={level}")

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
            "main_window": MAIN_WINDOW,
            "evaluation_type": evaluation_type,
            "would_outcome": would_outcome,
            "windows":       windows,
            "day_high_after_signal": day_high,
            "day_low_after_signal":  day_low,
            "day_outcome":   outcome_day,
            "day_pnl_pct":   pnl_day,
            "day_level_hit": level_day,
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
                    "main_window": MAIN_WINDOW,
                    "evaluation_type": evaluation_type,
                    "would_outcome": would_outcome,
                    "windows":    windows,
                    "day_high_after_signal": day_high,
                    "day_low_after_signal":  day_low,
                    "day_outcome": outcome_day,
                    "day_pnl_pct": pnl_day,
                    "day_level_hit": level_day,
                    "time":      r["time"],
                    "recheck_date": target_date.isoformat(),
                }
                break

    if send_summary:
        trade_results = [r for r in results if r.get("evaluation_type") != "rejected_validation"]
        rejected_eval = [r for r in results if r.get("evaluation_type") == "rejected_validation"]
        wins    = [r for r in trade_results if "WIN"     in r["outcome"]]
        losses  = [r for r in trade_results if "LOSS"    in r["outcome"]]
        softsl  = [r for r in results if "SOFT"    in r["outcome"]]
        pending = [r for r in results if "PENDING" in r["outcome"]]
        valid_rejects = [r for r in rejected_eval if "VALID_REJECT" in r["outcome"]]
        missed_tp = [r for r in rejected_eval if "MISSED_TP" in r["outcome"]]
        wr = round(len(wins)/(len(wins)+len(losses))*100) if (wins or losses) else None

        tp1_hits = sum(1 for r in wins if "TP1" in r["outcome"])
        tp2_hits = sum(1 for r in wins if "TP2" in r["outcome"])
        tp3_hits = sum(1 for r in wins if "TP3" in r["outcome"])

        avg_win  = round(sum(r["pnl_pct"] for r in wins)  /len(wins),  2) if wins   else 0
        avg_loss = round(sum(r["pnl_pct"] for r in losses)/len(losses), 2) if losses else 0

        date_s = target_date.strftime("%d %b %Y")
        msg  = f"📊 *{RECHECK_VERSION} — {date_s}*\n"
        msg += f"_(เฉพาะ conf >= 70 | main benchmark: {MAIN_WINDOW} หลัง signal)_\n\n"
        msg += f"📈 WIN: *{len(wins)}* | 📉 LOSS: *{len(losses)}* | ⚠️ SoftSL: {len(softsl)} | ⏳ Pending: {len(pending)}\n"
        if rejected_eval:
            msg += f"🧪 Reject validation: valid {len(valid_rejects)} | missed TP {len(missed_tp)}\n"
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

        msg += f"\n_⚡ sasi.asia/dashboard · {BOT_VERSION}_"
        send_tg(msg)

    return results

def main():
    log("=" * 50)
    log(f"🔍 {RECHECK_VERSION} — {BOT_VERSION}")

    logs  = load_json(cfg.LOG_FILE)
    rechk = load_json(cfg.RECHECK_LOG)
    migrated = migrate_missing_levels(logs)
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
            total += len(process_date(logs, rechk, d, send_summary=False, replace_existing=True))
            d += timedelta(days=1)
        save_json(cfg.LOG_FILE, logs)
        save_json(cfg.RECHECK_LOG, rechk[:2000])
        log(f"✅ Backfill done — migrated {migrated} rows, added {total} recheck rows")
        return

    yesterday_th = today_th - timedelta(days=1)
    results = process_date(logs, rechk, yesterday_th, send_summary=True)
    save_json(cfg.LOG_FILE, logs)
    save_json(cfg.RECHECK_LOG, rechk[:2000])
    log(f"✅ Done — migrated {migrated} rows | rows:{len(results)}")

if __name__ == "__main__":
    main()
