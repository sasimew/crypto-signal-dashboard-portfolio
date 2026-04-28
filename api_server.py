#!/usr/bin/env python3
"""
Crypto Signal — Flask API Server v3
- Binance futures data cache (funding/LS ratio/OI/volume)
- /api/binance endpoint สำหรับ dashboard Binance tab
- Bug fix: tf_15m error handling
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

TZ_THAI = timezone(timedelta(hours=7))
def now_thai(): return datetime.now(TZ_THAI)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import config_set2 as cfg
except ImportError:
    import config as cfg

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    import bot_set2 as bot_module
except ImportError:
    import bot as bot_module

app = Flask(__name__, static_folder=cfg.BASE_DIR)
CORS(app)

# ─── BINANCE FUTURES CACHE ────────────────────────────────────
# cache เก็บ {symbol: {data, fetched_at, ttl}}
_bnf_cache = {}

BNF_BASE = "https://fapi.binance.com/fapi/v1"
BNF_BASE2 = "https://fapi.binance.com/futures/data"

CACHE_TTL = {
    "funding":   300,  # 5 min
    "ls_ratio":   60,  # 1 min
    "oi":         30,  # 30 sec
    "taker_vol":  60,  # 1 min
    "prices":      5,  # 5 sec
}

def bn_get(url, timeout=10):
    """GET Binance API — ไม่ต้องใส่ header"""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url), timeout=timeout
        ) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[BN] fetch error: {url[:60]} → {e}")
        return None

def cache_get(key):
    """ดึงจาก cache ถ้ายัง fresh"""
    c = _bnf_cache.get(key)
    if not c: return None
    age = time.time() - c["fetched_at"]
    if age > c["ttl"]: return None
    return c["data"]

def cache_set(key, data, ttl):
    _bnf_cache[key] = {"data": data, "fetched_at": time.time(), "ttl": ttl}

def fetch_binance_futures(symbol="BTCUSDT"):
    """
    ดึง Binance Futures data ทั้งหมดพร้อมกัน
    Cache แต่ละ type แยกกัน
    """
    sym = symbol.upper()
    result = {"symbol": sym, "fetched_at": datetime.now(timezone.utc).isoformat()}

    # ── Funding Rate ──────────────────────────────────────────
    fkey = f"funding_{sym}"
    funding = cache_get(fkey)
    if not funding:
        d = bn_get(f"{BNF_BASE}/premiumIndex?symbol={sym}")
        if d and "lastFundingRate" in d:
            funding = {
                "rate":      round(float(d["lastFundingRate"]) * 100, 4),
                "next_time": int(d.get("nextFundingTime", 0)),
                "mark_price": float(d.get("markPrice", 0)),
                "index_price": float(d.get("indexPrice", 0)),
            }
            cache_set(fkey, funding, CACHE_TTL["funding"])
    result["funding"] = funding

    # ── Long/Short Ratio ──────────────────────────────────────
    lskey = f"ls_{sym}"
    ls = cache_get(lskey)
    if not ls:
        d = bn_get(f"{BNF_BASE2}/globalLongShortAccountRatio?symbol={sym}&period=5m&limit=3")
        if d and isinstance(d, list) and len(d):
            latest = d[-1]
            prev   = d[0] if len(d) > 1 else latest
            ls = {
                "long_ratio":  round(float(latest.get("longAccount", 0.5)) * 100, 2),
                "short_ratio": round(float(latest.get("shortAccount", 0.5)) * 100, 2),
                "ls_ratio":    round(float(latest.get("longShortRatio", 1.0)), 3),
                "prev_ls":     round(float(prev.get("longShortRatio", 1.0)), 3),
            }
            cache_set(lskey, ls, CACHE_TTL["ls_ratio"])
    result["ls_ratio"] = ls

    # ── Open Interest ──────────────────────────────────────────
    oikey = f"oi_{sym}"
    oi = cache_get(oikey)
    if not oi:
        d = bn_get(f"{BNF_BASE}/openInterest?symbol={sym}")
        dh = bn_get(f"{BNF_BASE2}/openInterestHist?symbol={sym}&period=5m&limit=2")
        if d and "openInterest" in d:
            oi_now  = float(d["openInterest"])
            oi_prev = float(dh[0]["sumOpenInterest"]) if (dh and isinstance(dh, list) and len(dh)) else oi_now
            oi_chg  = round((oi_now - oi_prev) / oi_prev * 100, 3) if oi_prev else 0
            oi = {
                "value":   round(oi_now, 2),
                "change":  oi_chg,
                "rising":  oi_chg > 0,
            }
            cache_set(oikey, oi, CACHE_TTL["oi"])
    result["oi"] = oi

    # ── Taker Buy/Sell Volume ─────────────────────────────────
    tvkey = f"tv_{sym}"
    tv = cache_get(tvkey)
    if not tv:
        d = bn_get(f"{BNF_BASE2}/takerlongshortRatio?symbol={sym}&period=5m&limit=3")
        if d and isinstance(d, list) and len(d):
            latest = d[-1]
            buy_vol  = float(latest.get("buyVol", 0))
            sell_vol = float(latest.get("sellVol", 0))
            total    = buy_vol + sell_vol
            tv = {
                "buy_vol":    round(buy_vol, 2),
                "sell_vol":   round(sell_vol, 2),
                "buy_ratio":  round(buy_vol / total * 100, 1) if total else 50,
                "sell_ratio": round(sell_vol / total * 100, 1) if total else 50,
                "ls_vol_ratio": round(float(latest.get("buySellRatio", 1.0)), 3),
            }
            cache_set(tvkey, tv, CACHE_TTL["taker_vol"])
    result["taker_vol"] = tv

    # ── Interpretation ─────────────────────────────────────────
    result["interpretation"] = interpret_futures(funding, ls, oi, tv)
    return result

def interpret_futures(funding, ls, oi, tv):
    """วิเคราะห์ market sentiment จาก futures data"""
    signals = []
    sentiment = "NEUTRAL"

    if not funding and not ls: return {"sentiment": "NO DATA", "signals": []}

    # Funding
    if funding:
        rate = funding["rate"]
        if rate > 0.05:
            signals.append(f"🔴 Funding สูง ({rate:+.4f}%) — crowded longs / squeeze risk")
        elif rate > 0.01:
            signals.append(f"⚠️ Funding ปานกลาง ({rate:+.4f}%) — longs dominant")
        elif rate < -0.01:
            signals.append(f"🟢 Funding ลบ ({rate:+.4f}%) — shorts dominant / potential long squeeze")
        else:
            signals.append(f"✅ Funding ปกติ ({rate:+.4f}%)")

    # Long/Short Ratio
    if ls:
        lr = ls["ls_ratio"]
        if lr > 1.5:
            signals.append(f"🔴 Long ครอง {ls['long_ratio']:.1f}% — crowded longs")
        elif lr < 0.7:
            signals.append(f"🟢 Short ครอง {ls['short_ratio']:.1f}% — potential short squeeze")
        else:
            signals.append(f"⚖️ L/S ratio สมดุล ({lr:.2f})")

    # OI
    if oi:
        chg = oi["change"]
        if abs(chg) > 0.5:
            signals.append(f"{'📈' if oi['rising'] else '📉'} OI {'เพิ่ม' if oi['rising'] else 'ลด'} {chg:+.2f}%")

    # Taker Volume
    if tv:
        br = tv["buy_ratio"]
        if br > 55:
            signals.append(f"🟢 Taker Buy {br:.0f}% — aggressive buying")
        elif br < 45:
            signals.append(f"🔴 Taker Sell {100-br:.0f}% — aggressive selling")

    # Overall sentiment
    bull = sum(1 for s in signals if s.startswith("🟢"))
    bear = sum(1 for s in signals if s.startswith("🔴"))
    if bull >= 2: sentiment = "BULLISH"
    elif bear >= 2: sentiment = "BEARISH"
    elif bull > bear: sentiment = "SLIGHTLY BULLISH"
    elif bear > bull: sentiment = "SLIGHTLY BEARISH"

    return {"sentiment": sentiment, "signals": signals}

# ─── HELPERS ──────────────────────────────────────────────
def load_json(path):
    try:
        if os.path.exists(path):
            with open(path) as f: return json.load(f)
    except: pass
    return []

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data[:2000], f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Save error: {e}")

def _signal_score(sig):
    vals = []
    for key in ("conf", "pre_conf"):
        try:
            vals.append(int(sig.get(key) or 0))
        except (TypeError, ValueError):
            vals.append(0)
    return max(vals) if vals else 0

def _is_display_signal(sig):
    gate = str(sig.get("gate_path", ""))
    direction = sig.get("direction")

    if gate in ("VOLATILE_BLOCK", "TIER3_NO_DIRECTION", "T4_NO_DIRECTION",
                "TIER4_NO_DIRECTION", "MANUAL_T3_NO_DIRECTION"):
        return False
    if direction not in ("Long", "Short"):
        return False
    return _signal_score(sig) >= cfg.FILTER_MIN_CONF

def get_display_signal_logs(include_audit=False):
    logs = load_json(cfg.LOG_FILE)
    display_logs = logs[:] if include_audit else [s for s in logs if _is_display_signal(s)]
    display_logs.sort(key=lambda s: s.get("time", ""), reverse=True)
    return logs, display_logs

def send_telegram_msg(msg):
    token   = cfg.TELEGRAM_TOKEN
    chat_id = cfg.TELEGRAM_CHAT_ID
    if not token or "YOUR" in token: return False
    params = urllib.parse.urlencode({
        "chat_id": chat_id, "text": msg, "parse_mode": "Markdown"
    })
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage?{params}"
            ), timeout=10
        ) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except: return False

# ─── ROUTES ───────────────────────────────────────────────
@app.route("/")
def root():
    return jsonify({"status": "ok", "version": getattr(cfg, "BOT_VERSION", "unknown"), "dashboard": "/dashboard"})

@app.route("/dashboard")
@app.route("/dashboard/")
def dashboard():
    return send_from_directory(cfg.BASE_DIR, "dashboard.html")

@app.route("/health")
def health():
    cron_paths = [
        cfg.LOG_FILE,
        os.path.join(os.path.dirname(cfg.LOG_FILE), "bot.log"),
    ]
    latest_mtime = None
    for p in cron_paths:
        try:
            mt = os.path.getmtime(p)
            latest_mtime = mt if latest_mtime is None else max(latest_mtime, mt)
        except OSError:
            continue

    cron_last_seen = None
    cron_running = False
    if latest_mtime:
        cron_last_seen = datetime.fromtimestamp(latest_mtime, TZ_THAI).strftime('%Y-%m-%d %H:%M:%S TH')
        cron_running = (time.time() - latest_mtime) <= 15 * 60

    return jsonify({
        "status": "ok",
        "time": now_thai().strftime('%Y-%m-%d %H:%M:%S TH'),
        "version": getattr(cfg, "BOT_VERSION", "unknown"),
        "cron_running": cron_running,
        "cron_last_seen": cron_last_seen,
    })

@app.route("/api/prices")
def api_prices():
    """Live prices BTC/ETH/BNB — ดึงจาก Binance spot"""
    prices = []
    for sym in cfg.SYMBOLS:
        try:
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}"
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode())
                prices.append({
                    "symbol": sym,
                    "price":  float(d["lastPrice"]),
                    "change": float(d["priceChangePercent"]),
                    "high":   float(d["highPrice"]),
                    "low":    float(d["lowPrice"]),
                    "volume": float(d["quoteVolume"]),
                })
        except: pass
    return jsonify({"ok": True, "data": prices})

@app.route("/api/binance")
def api_binance():
    """
    Binance Futures intelligence — cached backend
    ไม่ให้ browser ยิง Binance โดยตรง (rate limit safe)
    ?symbol=BTCUSDT
    """
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    if symbol not in [s.upper() for s in cfg.SYMBOLS]:
        symbol = "BTCUSDT"
    try:
        data = fetch_binance_futures(symbol)
        if not any([data.get("funding"), data.get("ls_ratio"), data.get("oi"), data.get("taker_vol")]):
            return jsonify({
                "ok": False,
                "error": "Binance futures data unavailable",
                "symbol": symbol,
                "data": data,
            }), 503
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/signals")
def api_signals():
    include_audit = request.args.get("include_audit") == "1"
    raw_logs, display_logs = get_display_signal_logs(include_audit=include_audit)
    filtered = display_logs
    limit   = min(int(request.args.get("limit", 400)), 500)
    verdict = request.args.get("verdict", "all").upper()
    symbol  = request.args.get("symbol", "all").upper()
    strategy= request.args.get("strategy", "all").upper()
    regime  = request.args.get("regime", "all").upper()

    if verdict  != "ALL": filtered = [s for s in filtered if s.get("verdict")==verdict]
    if symbol   != "ALL": filtered = [s for s in filtered if s.get("symbol","").startswith(symbol)]
    if strategy != "ALL": filtered = [s for s in filtered if s.get("strategy_used")==strategy]
    if regime   != "ALL": filtered = [s for s in filtered if s.get("regime")==regime]

    return jsonify({
        "ok": True,
        "total": len(display_logs),
        "raw_total": len(raw_logs),
        "filtered": len(filtered),
        "data": filtered[:limit]
    })

@app.route("/api/stats")
def api_stats():
    raw_logs, display_logs = get_display_signal_logs()
    total      = len(raw_logs)
    n_filtered = sum(1 for s in raw_logs if s.get("verdict")=="FILTERED")
    n_volatile = sum(1 for s in raw_logs if s.get("regime")=="VOLATILE")
    claude_used= sum(1 for s in raw_logs if s.get("claude_called"))
    approved   = sum(1 for s in raw_logs if s.get("verdict") in ("APPROVED","WEAK APPROVAL"))
    rejected   = sum(1 for s in raw_logs if s.get("verdict") in ("REJECTED","NO TRADE"))

    # By strategy
    trend_sigs  = [s for s in raw_logs if s.get("strategy_used")=="TREND_FOLLOW"]
    rebound_sigs= [s for s in raw_logs if s.get("strategy_used")=="REBOUND"]

    # Win rate
    rechecked = [s for s in raw_logs if s.get("recheck")]
    wins      = [s for s in rechecked if "WIN"  in (s["recheck"].get("outcome",""))]
    losses    = [s for s in rechecked if "LOSS" in (s["recheck"].get("outcome",""))]
    win_rate  = round(len(wins)/(len(wins)+len(losses))*100) if (wins or losses) else None

    # Regime breakdown
    regime_counts = {}
    for r in ["TRENDING","RANGING","VOLATILE","MIXED"]:
        regime_counts[r] = sum(1 for s in raw_logs if s.get("regime")==r)

    # Cost estimate (Haiku)
    est_cost = (claude_used * 400/1e6 * 0.80) + (claude_used * 300/1e6 * 4.0)

    # BUG-10 FIX: ใช้ TH timezone ไม่ใช่ UTC (เวลาไทย +7)
    today      = datetime.now(TZ_THAI).date()
    today_sigs = [s for s in raw_logs if s.get("time","")[:10]==str(today)]
    today_approved = sum(1 for s in today_sigs if s.get("verdict") in ("APPROVED", "WEAK APPROVAL"))

    by_symbol = {}
    for sym in cfg.SYMBOLS:
        sl = [s for s in raw_logs if s.get("symbol")==sym]
        by_symbol[sym] = {
            "total":    len(sl),
            "approved": sum(1 for s in sl if s.get("verdict") in ("APPROVED","WEAK APPROVAL")),
            "filtered": sum(1 for s in sl if s.get("verdict")=="FILTERED"),
            "trending": sum(1 for s in sl if s.get("strategy_used")=="TREND_FOLLOW"),
            "rebound":  sum(1 for s in sl if s.get("strategy_used")=="REBOUND"),
        }

    gate_counts = {
        "t1": sum(1 for s in raw_logs if str(s.get("gate_path","")).startswith("TIER1")),
        "t2": sum(1 for s in raw_logs if str(s.get("gate_path","")).startswith("TIER2")),
        "t3": sum(1 for s in raw_logs if str(s.get("gate_path","")).startswith("TIER3")),
        "t4": sum(1 for s in raw_logs if str(s.get("gate_path","")).startswith("TIER4") or str(s.get("gate_path","")).startswith("MANUAL_CLAUDE")),
        "t5": sum(1 for s in raw_logs if str(s.get("gate_path","")).startswith("TIER5")),
    }

    return jsonify({
        "ok": True,
        "total_signals":   total,
        "filter_skipped":  n_filtered,
        "volatile_skipped":n_volatile,
        "claude_called":   claude_used,
        "filter_rate":     round(n_filtered/total*100,1) if total else 0,
        "approved":        approved,
        "rejected":        rejected,
        "win_rate":        win_rate,
        "wins":            len(wins),
        "losses":          len(losses),
        "today_signals":   len(today_sigs),
        "today_approved":  today_approved,
        "est_cost_usd":    round(est_cost, 4),
        "regime_breakdown":regime_counts,
        "strategy_breakdown": {
            "TREND_FOLLOW": len(trend_sigs),
            "REBOUND":      len(rebound_sigs),
        },
        "display_total_signals": len(display_logs),
        "_gate_counts": gate_counts,
        "by_symbol": by_symbol,
    })

@app.route("/api/recheck")
def api_recheck():
    """
    Recheck view ใช้ signal_log เป็นฐาน เพื่อให้เห็น log เก่า + ใหม่
    และ overlay outcome จาก recheck_log ถ้ามี
    """
    rechk = load_json(cfg.RECHECK_LOG)
    signal_logs = load_json(cfg.LOG_FILE)
    limit = min(int(request.args.get("limit", 200)), 500)
    days  = int(request.args.get("days", 7))
    date_filter = request.args.get("date", "").strip()
    from_date = request.args.get("from", "").strip()
    to_date = request.args.get("to", "").strip()

    signal_logs = [
        s for s in signal_logs
        if (s.get("conf") or 0) >= 70
        and s.get("direction") in ("Long", "Short")
    ]

    by_signal_id = {s.get("id"): s for s in signal_logs if s.get("id")}
    recheck_by_signal = {}
    for r in rechk:
        sig_id = r.get("signal_id")
        if sig_id and sig_id not in recheck_by_signal:
            recheck_by_signal[sig_id] = r

    def coalesce(*vals):
        for v in vals:
            if v not in (None, "", "—"):
                return v
        return None

    rows = []
    for s in signal_logs:
        sig_id = s.get("id")
        sig_date = (s.get("time") or "")[:10]
        rr = recheck_by_signal.get(sig_id, {})
        sr = s.get("recheck") or {}
        rows.append({
            "signal_id": sig_id,
            "symbol": s.get("symbol"),
            "direction": s.get("direction"),
            "verdict": s.get("verdict"),
            "conf": s.get("conf"),
            "pre_conf": s.get("pre_conf"),
            "gate_path": s.get("gate_path", "—"),
            "strategy": coalesce(rr.get("strategy"), s.get("strategy_used"), "—"),
            "regime": coalesce(rr.get("regime"), s.get("regime"), "—"),
            "entry": coalesce(rr.get("entry"), s.get("entry"), s.get("suggested_entry")),
            "ssl": coalesce(rr.get("ssl"), s.get("ssl"), s.get("suggested_ssl")),
            "hsl": coalesce(rr.get("hsl"), s.get("hsl"), s.get("suggested_hsl")),
            "tp1": coalesce(rr.get("tp1"), s.get("tp1"), s.get("suggested_tp1")),
            "tp2": coalesce(rr.get("tp2"), s.get("tp2"), s.get("suggested_tp2")),
            "tp3": coalesce(rr.get("tp3"), s.get("tp3"), s.get("suggested_tp3")),
            "current_price": coalesce(rr.get("current_price"), sr.get("price")),
            "outcome": coalesce(rr.get("outcome"), sr.get("outcome")),
            "pnl_pct": coalesce(rr.get("pnl_pct"), sr.get("pnl_pct")),
            "level_hit": coalesce(rr.get("level_hit"), sr.get("level_hit"), "—"),
            "reason": s.get("reject_reason") or s.get("reason") or s.get("filter_reason"),
            "signal_time": s.get("time"),
            "signal_time_thai": s.get("time_thai"),
            "recheck_date": coalesce(rr.get("recheck_date"), sr.get("recheck_date"), sig_date),
            "time": coalesce(rr.get("time"), sr.get("time"), s.get("time")),
        })

    available_dates = sorted({
        r.get("recheck_date", r.get("signal_time","")[:10] or r.get("time","")[:10])
        for r in rows if r.get("recheck_date") or r.get("signal_time") or r.get("time")
    }, reverse=True)

    if days > 0:
        cutoff = (datetime.now(TZ_THAI).date() - timedelta(days=days)).isoformat()
        rows = [r for r in rows
                if r.get("recheck_date", r.get("signal_time","")[:10] or r.get("time","")[:10]) >= cutoff]

    if date_filter:
        rows = [r for r in rows
                if r.get("recheck_date", r.get("signal_time","")[:10] or r.get("time","")[:10]) == date_filter]

    if from_date or to_date:
        start = from_date or to_date
        end = to_date or from_date
        if start and end and start > end:
            start, end = end, start
        rows = [r for r in rows
                if start <= r.get("recheck_date", r.get("signal_time","")[:10] or r.get("time","")[:10]) <= end]

    rows.sort(key=lambda r: (r.get("signal_time") or r.get("time") or ""), reverse=True)

    return jsonify({
        "ok": True,
        "data": rows[:limit],
        "days": days,
        "date": date_filter or None,
        "from": from_date or None,
        "to": to_date or None,
        "available_dates": available_dates,
    })

@app.route("/api/trend")
def api_trend():
    """
    แนวโน้ม BTC/ETH/BNB — ใช้ logic SET1v2 จริง
    BUG FIX #3: โหลด log ครั้งเดียวนอกลูป
    """
    trend = {}
    # BUG FIX #3: โหลด log ครั้งเดียว ไม่ใช่ทุก symbol
    all_logs = load_json(cfg.LOG_FILE)

    for sym in cfg.SYMBOLS:
        try:
            m = bot_module.fetch_market(sym)
            if not m or not m.get("tf_15m"):
                trend[sym] = {
                    "symbol": sym, "bias": "ERROR",
                    "price": 0, "change": 0,
                    "error": "fetch_market failed — Binance timeout หรือ klines ไม่ครบ"
                }
                continue

            t15 = m["tf_15m"]
            t1h = m["tf_1h"]  or {}
            t2h = m.get("tf_2h") or {}
            t4h = m["tf_4h"]  or {}

            regime, regime_conf, regime_reasons = bot_module.detect_regime(
                t1h, t4h, m["price"])

            # BUG FIX #1: ลบ score = 0 ที่ไม่ได้ใช้
            bias = "NEUTRAL"; strategy = "NONE"; direction = "N/A"

            if regime == "TRENDING":
                strategy = "TREND_FOLLOW"
                passed, direction, reasons, tf_bk = bot_module.check_multi_tf(
                    t4h, t1h, t15, m["price"],
                    tf_2h=t2h, market_ctx=m.get("market_ctx") or {})
                if passed:
                    bias = "BUY" if direction=="Long" else "SELL"
                else:
                    e_s4 = t4h.get("ema_short"); e_m4 = t4h.get("ema_mid")
                    if e_s4 and e_m4:
                        bias = "WEAK BUY" if e_s4 > e_m4 else "WEAK SELL"

            elif regime in ("RANGING","MIXED"):
                strategy = "REBOUND"
                passed, direction, reasons, rb_data = bot_module.check_rebound_30m(
                    m["tf_30m"], t1h, m["price"],
                    change_24h=m.get("change",0),
                    funding_rate=m.get("funding_rate"),
                    tf_4h=m.get("tf_4h") or {},
                    tf_15m=t15,
                )
                if passed:
                    bias = "REBOUND BUY" if direction=="Long" else "REBOUND SELL"
                else:
                    bias = "RANGING — รอ rebound"

            elif regime == "VOLATILE":
                bias = "VOLATILE — งดเทรด"

            # BUG FIX #3: ใช้ all_logs ที่โหลดไว้แล้ว
            recent = [s for s in all_logs if s.get("symbol")==sym
                      and s.get("verdict") not in ("FILTERED","ERROR")]
            latest = recent[0] if recent else None
            last5_reg = {}
            for s in recent[:10]:
                r = s.get("regime","UNKNOWN")
                last5_reg[r] = last5_reg.get(r, 0) + 1

            trend[sym] = {
                "symbol":         sym,
                "price":          m["price"],
                "change":         m["change"],
                "bias":           bias,
                "regime":         regime,
                "regime_conf":    regime_conf,
                "strategy":       strategy,
                "direction":      direction,
                "rsi_15":         t15.get("rsi"),
                "rsi_1h":         t1h.get("rsi"),
                "rsi_4h":         t4h.get("rsi"),
                "macd_hist_15":   t15.get("macd_hist"),
                "bb_pct_15":      t15.get("bb_pct"),
                "vol_ratio":      t15.get("vol_ratio"),
                "ema_aligned":    bool(t15.get("ema_short") and t15.get("ema_mid") and
                                   t15.get("ema_short") > t15.get("ema_mid")),
                "latest":         latest,
                "regime_history": last5_reg,
            }

        except Exception as e:
            trend[sym] = {"symbol": sym, "bias": "ERROR", "error": str(e)}

    return jsonify({"ok": True, "data": trend})

@app.route("/api/analyze", methods=["POST","OPTIONS"])
def api_analyze():
    """
    Manual AI analysis จาก dashboard
    ใช้ logic SET1v4: fetch_market → detect_regime → strategy → Claude
    ถ้า REBOUND → ส่ง Telegram อัตโนมัติ
    """
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    body      = request.get_json() or {}
    symbol    = body.get("symbol", "BTCUSDT")
    direction = body.get("direction", "Long")  # manual override
    if direction not in ("Long", "Short"):
        return jsonify({
            "ok": False,
            "error": "Invalid direction. Manual analysis requires Long or Short."
        }), 400

    try:
        # ── Step 1: ดึงข้อมูล ─────────────────────────────────
        m = bot_module.fetch_market(symbol)
        if not m:
            return jsonify({"ok": False,
                "error": f"Cannot fetch market data for {symbol} — Binance timeout หรือ network issue",
                "hint": "ลอง refresh อีกครั้ง หรือตรวจ docker logs crypto-api"
            }), 503

        # Guard: tf_15m ต้องมี
        if not m.get("tf_15m"):
            return jsonify({"ok": False,
                "error": "ดึง klines ไม่ได้ (15m และ 30m fail) — Binance timeout",
                "hint": "รอ 10 วิแล้วลองใหม่"
            }), 503

        t15 = m["tf_15m"]
        t1h = m["tf_1h"]  or {}
        t2h = m.get("tf_2h") or {}
        t4h = m["tf_4h"]  or {}
        entry_tf = m.get("entry_tf", "15m")  # บันทึกว่าใช้ TF ไหนจริง

        # ── Step 2: Detect Regime ─────────────────────────────
        regime, regime_conf, regime_reasons = bot_module.detect_regime(
            t1h, t4h, m["price"])

        # ── Step 3: เลือก strategy และ check ─────────────────
        strategy = "MANUAL"
        filter_reason = f"Manual {direction} request (entry TF: {entry_tf})"
        tf_bk = {}; rb_data = {}
        final_direction = direction
        passed = False
        pre_conf = 0
        selected_score = None
        opposite_score = None

        if regime == "VOLATILE":
            filter_reason = f"⚠️ VOLATILE market — manual override {direction}"

        elif regime == "TRENDING":
            strategy = "TREND_FOLLOW"
            passed, auto_dir, reasons, tf_bk = bot_module.check_multi_tf(
                t4h, t1h, t15, m["price"],
                tf_2h=t2h, market_ctx=m.get("market_ctx") or {})
            if auto_dir not in ("N/A", direction):
                passed = False
                reasons.append(f"Manual {direction} selected, but trend alignment prefers {auto_dir}")
            filter_reason = " | ".join(reasons)
            if passed and auto_dir == direction:
                pre_conf = 70
            elif auto_dir == direction:
                pre_conf = 50
            else:
                pre_conf = 20

        elif regime in ("RANGING","MIXED"):
            strategy = "REBOUND"
            passed, auto_dir, reasons, rb_data = bot_module.check_rebound_30m(
                m["tf_30m"], t1h, m["price"],
                change_24h   = m.get("change", 0),
                funding_rate = m.get("funding_rate"),
                rsi_series   = m.get("rsi_series_30m"),
                tf_5m        = m.get("tf_5m"),
                tf_4h        = t4h,
                tf_15m       = t15,
            )
            if auto_dir not in ("N/A", direction):
                passed = False
                reasons.append(f"Manual {direction} selected, but rebound setup prefers {auto_dir}")
            filter_reason = " | ".join(reasons)
            long_score  = rb_data.get("long_score", 0)
            short_score = rb_data.get("short_score", 0)
            selected_score = long_score if direction == "Long" else short_score
            opposite_score = short_score if direction == "Long" else long_score
            pre_conf = bot_module.compute_pre_conf(long_score, short_score, direction)

        ctx_aligned = False
        ctx_reasons = []
        if final_direction in ("Long", "Short"):
            ctx_bonus, ctx_reasons = bot_module.calc_market_context_bonus(
                m.get("market_ctx") or {}, final_direction
            )
            ctx_aligned = bot_module.is_market_context_aligned(
                m.get("market_ctx") or {}, final_direction
            )
            pre_conf_before_ctx = pre_conf
            pre_conf = max(0, min(95, pre_conf + ctx_bonus))
            if ctx_reasons:
                filter_reason = (
                    f"{filter_reason} | ctx_bonus={ctx_bonus:+d} "
                    f"({'; '.join(ctx_reasons[:3])})"
                )
            m["ctx_bonus"] = ctx_bonus
            m["ctx_reasons"] = ctx_reasons
            m["ctx_aligned"] = ctx_aligned
            m["pre_conf"] = pre_conf
            m["pre_conf_before_ctx"] = pre_conf_before_ctx

        # ── Step 4: 5-Tier Gate (same as bot.py) ─────────────
        #
        # Tier 5: pre_conf >= AUTO_APPROVE_CONF + alignment → APPROVED (no Claude)
        # Tier 4: pre_conf >= CLAUDE_MIN_CONF              → Claude validates
        # Tier 3: pre_conf >= WEAK_MIN_CONF, not passed    → NO TRADE
        # Manual analysis follows the same bot gate before Claude to avoid
        # conflicting manual scores for the same market snapshot.

        low_liq = bot_module.low_liquidity_block_reason(t15, pre_conf, passed=passed)
        if low_liq:
            capped_conf = min(pre_conf, cfg.CLAUDE_MIN_CONF - 1)
            sig = bot_module.build_signal(
                symbol, final_direction, m, "NO TRADE", capped_conf, {},
                low_liq,
                regime, regime_conf, regime_reasons,
                strategy, filter_reason, tf_bk, rb_data,
                gate_path="TIER3_LOW_LIQUIDITY_BLOCK",
                pre_conf=pre_conf,
                claude_called=False
            )
            logs = load_json(cfg.LOG_FILE)
            sig["reject_reason"] = low_liq
            sig["block_reason_code"] = "LOW_LIQUIDITY_BLOCK"
            sig["pre_conf_before_block"] = pre_conf
            logs.insert(0, sig)
            save_json(cfg.LOG_FILE, logs)
            return jsonify({"ok": True, "signal": sig, "tg_sent": False, "tg_reason": "not sent (LOW_LIQUIDITY_BLOCK)"})

        # ── Tier 5: AUTO APPROVE ──────────────────────────────
        auto_approved = False
        auto_approve_reason = ""
        if pre_conf >= cfg.AUTO_APPROVE_CONF and passed and not tf_bk.get("bias_fallback"):
            ema_15m_bull = (t15.get("ema_short",0) and t15.get("ema_mid",0) and
                            t15.get("ema_short") > t15.get("ema_mid"))
            ema_1h_bull  = (t1h.get("ema_short",0) and t1h.get("ema_mid",0) and
                            t1h.get("ema_short") > t1h.get("ema_mid"))
            if strategy == "TREND_FOLLOW":
                if final_direction == "Long" and ema_15m_bull and ema_1h_bull:
                    auto_approved = True
                    auto_approve_reason = f"TREND_FOLLOW: pre={pre_conf}% + EMA bull aligned"
                elif final_direction == "Short" and not ema_15m_bull and not ema_1h_bull:
                    auto_approved = True
                    auto_approve_reason = f"TREND_FOLLOW: pre={pre_conf}% + EMA bear aligned"
            elif strategy == "REBOUND":
                rb_ls = rb_data.get("long_score", 0)
                rb_ss = rb_data.get("short_score", 0)
                rb_score = rb_ls if final_direction == "Long" else rb_ss
                rb_margin = (rb_ls - rb_ss) if final_direction == "Long" else (rb_ss - rb_ls)
                rb_turn_ok = (
                    (final_direction == "Long" and rb_data.get("rsi_turning_up", False)) or
                    (final_direction == "Short" and rb_data.get("rsi_turning_down", False))
                )
                if (
                    pre_conf >= cfg.REBOUND_AUTO_APPROVE_CONF
                    and rb_turn_ok
                    and rb_score >= cfg.REBOUND_TIER5_MIN_SCORE
                    and rb_margin >= cfg.REBOUND_TIER5_MIN_MARGIN
                ):
                    auto_approved = True
                    auto_approve_reason = (
                        f"REBOUND {final_direction.upper()} TIER5-A: pre={pre_conf}% + "
                        f"score={rb_score} + margin={rb_margin} + rsi_turn"
                    )

        if auto_approved:
            fallback = bot_module.calc_fallback_levels(m["price"], final_direction)
            levels = fallback.copy()
            levels["entry"]       = str(round(m["price"], 2))
            levels["reason"]      = auto_approve_reason or f"Bot AUTO APPROVE: pre_conf={pre_conf}%"
            levels["reason_code"] = "AUTO_APPROVED"
            verdict, conf = "APPROVED", pre_conf
            sig = bot_module.build_signal(
                symbol, final_direction, m, verdict, conf, levels,
                f"BOT_AUTO:{pre_conf}", regime, regime_conf, regime_reasons,
                strategy, filter_reason, tf_bk, rb_data,
                gate_path="TIER5_AUTO_APPROVED",
                pre_conf=pre_conf,
                claude_called=False
            )
            logs = load_json(cfg.LOG_FILE)
            logs.insert(0, sig)
            save_json(cfg.LOG_FILE, logs)
            tg_sent = False; tg_reason = ""
            if tg_trade_eligible(sig):
                if strategy == "REBOUND" and rb_data and \
                   (rb_data.get("long_score",0) >= 5 or rb_data.get("short_score",0) >= 5):
                    tg_sent   = send_telegram_msg(build_tg_rebound_alert(sig, rb_data))
                    tg_reason = "rebound auto approved"
                else:
                    tg_sent   = send_telegram_msg(build_tg_trade_msg(sig))
                    tg_reason = "auto approved"
            else:
                tg_reason = "not sent (missing trade levels)"
            return jsonify({"ok": True, "signal": sig, "tg_sent": tg_sent, "tg_reason": tg_reason})

        # Manual follows bot gates: low-confidence or directionless requests are logged
        # as NO TRADE instead of letting Claude create conflicting manual scores.
        if final_direction not in ("Long", "Short"):
            capped_conf = min(pre_conf, cfg.CLAUDE_MIN_CONF - 1)
            pre_conf_before_no_direction = pre_conf
            pre_conf = capped_conf
            sig = bot_module.build_signal(
                symbol, final_direction, m, "NO TRADE", pre_conf, {},
                f"Direction={final_direction}: no valid long/short signal to validate",
                regime, regime_conf, regime_reasons,
                strategy, filter_reason, tf_bk, rb_data,
                gate_path="MANUAL_T3_NO_DIRECTION",
                pre_conf=pre_conf,
                claude_called=False
            )
            logs = load_json(cfg.LOG_FILE)
            sig["reject_reason"] = f"Direction={final_direction}: no valid long/short signal to validate"
            sig["pre_conf_before_no_direction"] = pre_conf_before_no_direction
            logs.insert(0, sig)
            save_json(cfg.LOG_FILE, logs)
            return jsonify({"ok": True, "signal": sig, "tg_sent": False, "tg_reason": "not sent (NO TRADE)"})

        effective_claude_min = cfg.CLAUDE_MIN_CONF
        if ctx_aligned and cfg.CTX_CLAUDE_GATE_REDUCTION > 0:
            effective_claude_min = cfg.CLAUDE_MIN_CONF - cfg.CTX_CLAUDE_GATE_REDUCTION
        m["effective_claude_min"] = effective_claude_min

        if pre_conf < effective_claude_min and not passed:
            sig = bot_module.build_signal(
                symbol, final_direction, m, "NO TRADE", pre_conf, {},
                f"Manual bot gate: pre_conf={pre_conf}% below Claude gate {effective_claude_min}%, filter not passed",
                regime, regime_conf, regime_reasons,
                strategy, filter_reason, tf_bk, rb_data,
                gate_path="MANUAL_BOT_REJECT",
                pre_conf=pre_conf,
                claude_called=False
            )
            logs = load_json(cfg.LOG_FILE)
            sig["reject_reason"] = f"Manual bot gate: pre_conf={pre_conf}% below Claude gate {effective_claude_min}%, filter not passed"
            logs.insert(0, sig)
            save_json(cfg.LOG_FILE, logs)
            return jsonify({"ok": True, "signal": sig, "tg_sent": False, "tg_reason": "not sent (NO TRADE)"})

        # ── Tier 4: Claude validates ──────────────────────────
        summary  = bot_module.build_summary(m, regime, strategy, tf_bk, rb_data, direction=final_direction)  # BUG-08 FIX
        ai_text  = bot_module.call_claude(symbol, final_direction, strategy, summary)
        verdict, conf, levels = bot_module.parse_ai(ai_text)

        if not levels.get("entry"):
            levels["entry"] = str(round(m["price"], 2))

        confidence_for_levels = max(pre_conf, conf) if pre_conf > 0 else conf

        # ✅ Fallback TP/SL — TRADEABLE only
        if verdict in ("APPROVED", "WEAK APPROVAL"):
            fallback = bot_module.calc_fallback_levels(m["price"], final_direction)
            if not levels.get("ssl"):  levels["ssl"] = fallback["ssl"]
            if not levels.get("hsl"):  levels["hsl"] = fallback["hsl"]
            if not levels.get("tp1"):  levels["tp1"] = fallback["tp1"]
            if not levels.get("tp2"):  levels["tp2"] = fallback["tp2"]
            if not levels.get("tp3"):  levels["tp3"] = fallback["tp3"]
        elif confidence_for_levels >= 50:
            fallback = bot_module.calc_fallback_levels(m["price"], final_direction)
            levels["suggested_entry"] = levels.get("entry") or str(round(m["price"], 2))
            levels["suggested_ssl"] = levels.get("ssl") or fallback["ssl"]
            levels["suggested_hsl"] = levels.get("hsl") or fallback["hsl"]
            levels["suggested_tp1"] = levels.get("tp1") or fallback["tp1"]
            levels["suggested_tp2"] = levels.get("tp2") or fallback["tp2"]
            levels["suggested_tp3"] = levels.get("tp3") or fallback["tp3"]

        # Manual log score must stay aligned with bot scoring; Claude confidence is kept
        # separately for audit so manual Long/Short clicks cannot create conflicting scores.
        display_conf = pre_conf if pre_conf > 0 else conf
        gate = "TIER4_CLAUDE" if pre_conf >= effective_claude_min else "MANUAL_CLAUDE"

        # ── Step 5: Build Signal ──────────────────────────────
        sig = bot_module.build_signal(
            symbol, final_direction, m, verdict, display_conf, levels, ai_text,
            regime, regime_conf, regime_reasons,
            strategy, filter_reason, tf_bk, rb_data,
            gate_path=gate,
            pre_conf=pre_conf,
            claude_called=True
        )
        if selected_score is not None:
            sig["selected_score"] = selected_score
            sig["opposite_score"] = opposite_score
        sig["ai_conf"] = conf

        # ── Step 6: บันทึก log ───────────────────────────────
        logs = load_json(cfg.LOG_FILE)
        logs.insert(0, sig)
        save_json(cfg.LOG_FILE, logs)

        # ── Step 7: ส่ง Telegram ─────────────────────────────
        # APPROVED / WEAK APPROVAL ส่งตามปกติ
        # REJECTED >=50 ส่งเพื่อ review พร้อม entry/tp/sl
        tg_sent = False; tg_reason = ""

        if tg_rejected_eligible(sig):
            tg_sent = send_telegram_msg(build_tg_rejected_msg(sig))
            tg_reason = "rejected review alert"
        elif tg_trade_eligible(sig):
            if strategy == "REBOUND" and rb_data and \
               (rb_data.get("long_score",0) >= 5 or rb_data.get("short_score",0) >= 5):
                tg_sent   = send_telegram_msg(build_tg_rebound_alert(sig, rb_data))
                tg_reason = "rebound approved alert"
            else:
                tg_sent   = send_telegram_msg(build_tg_trade_msg(sig))
                tg_reason = "approved signal"
        else:
            tg_reason = f"not sent ({verdict or 'UNKNOWN'})"

        return jsonify({
            "ok":       True,
            "signal":   sig,
            "tg_sent":  tg_sent,
            "tg_reason":tg_reason,
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        error_type, error_desc = bot_module.classify_runtime_error(e)
        return jsonify({
            "ok": False,
            "error": str(e),
            "error_type": error_type,
            "error_desc": error_desc,
        }), 500

@app.route("/api/export")
def api_export():
    """
    Export signal log เป็น JSON + CSV
    ?days=1  → วันนี้ตั้งแต่ 00:00 TH
    ?days=3  → 3 วันย้อนหลัง (นับ 00:00 TH เป็นวัน)
    ?days=7  → 7 วันย้อนหลัง
    ?days=14, 30 → ย้อนหลัง N วัน
    ?format=json หรือ csv (default csv)
    ?verdict=all หรือ approved (default all)
    """
    import io, csv as csv_mod
    from flask import Response
    import json as json_mod

    days    = int(request.args.get("days", 7))
    fmt     = request.args.get("format", "csv")
    verdict_filter = request.args.get("verdict", "all")
    logs    = load_json(cfg.LOG_FILE)

    # ── คำนวณ cutoff แบบ 00:00 TH ────────────────────────────
    # days=1 → ตั้งแต่ 00:00 วันนี้ (TH)
    # days=7 → ตั้งแต่ 00:00 เมื่อ 6 วันก่อน (TH) = 7 วันรวมวันนี้
    today_th  = now_thai().date()
    start_th  = today_th - timedelta(days=days-1)  # เช่น days=7 → 6 วันก่อน
    cutoff_dt = datetime(start_th.year, start_th.month, start_th.day,
                         0, 0, 0, tzinfo=TZ_THAI)

    filtered = []
    for s in logs:
        try:
            t = datetime.fromisoformat(s.get("time","").replace("Z","+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            t_thai = t.astimezone(TZ_THAI)
            if t_thai >= cutoff_dt:
                # filter verdict ถ้าระบุ
                if verdict_filter == "approved":
                    if s.get("verdict") not in ("APPROVED","WEAK APPROVAL"):
                        continue
                filtered.append(s)
        except:
            filtered.append(s)

    # ชื่อไฟล์ระบุช่วงวัน
    end_str   = today_th.strftime('%Y%m%d')
    start_str = start_th.strftime('%Y%m%d')
    filename  = f"signal_log_{start_str}_{end_str}"

    if fmt == "csv":
        output = io.StringIO()
        fields = ["time_thai","symbol","direction","price","entry",
                  "tp1","tp2","tp3","ssl","hsl","verdict","conf",
                  "regime","strategy_used","filter_reason","reject_reason",
                  "funding_rate"]
        writer = csv_mod.DictWriter(output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for s in filtered:
            row = {f: s.get(f,"") for f in fields}
            # เพิ่ม time_thai ถ้าไม่มี
            if not row.get("time_thai") and s.get("time"):
                try:
                    t = datetime.fromisoformat(s["time"].replace("Z","+00:00"))
                    row["time_thai"] = t.astimezone(TZ_THAI).strftime('%Y-%m-%d %H:%M TH')
                except: pass
            # เพิ่ม funding_rate จาก decision_log
            if not row.get("funding_rate"):
                rb = s.get("decision_log",{}).get("tf_30m_rebound",{})
                row["funding_rate"] = rb.get("funding_rate","")
            writer.writerow(row)
        csv_data = output.getvalue().encode("utf-8-sig")
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename={filename}.csv"}
        )
    else:
        json_data = json_mod.dumps(filtered, indent=2, ensure_ascii=False).encode("utf-8")
        return Response(
            json_data,
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment;filename={filename}.json"}
        )

@app.route("/api/rebound_scan")
def api_rebound_scan():
    """
    สแกน Rebound ทุก symbol ทันที
    เรียกได้จาก dashboard ปุ่ม "Scan Rebound"
    """
    results = []
    for sym in cfg.SYMBOLS:
        try:
            m = bot_module.fetch_market(sym)
            if not m: continue

            t1h = m["tf_1h"] or {}
            regime, _, _ = bot_module.detect_regime(
                t1h, m["tf_4h"] or {}, m["price"])

            passed, direction, reasons, rb_data = bot_module.check_rebound_30m(
                m["tf_30m"], t1h, m["price"],
                change_24h=m.get("change",0),
                funding_rate=m.get("funding_rate"),
                tf_4h=m.get("tf_4h") or {},
                tf_15m=m.get("tf_15m") or {},
            )

            result = {
                "symbol":    sym,
                "price":     m["price"],
                "regime":    regime,
                "rebound_passed": passed,
                "direction": direction,
                "reasons":   reasons,
                "long_score":  rb_data.get("long_score", 0),
                "short_score": rb_data.get("short_score", 0),
                "bb_pct":    rb_data.get("bb_pct"),
                "rsi_30m":   rb_data.get("rsi"),
                "vol_ratio": rb_data.get("vol_ratio"),
            }
            results.append(result)

            # ส่ง Telegram ถ้า rebound ผ่าน
            if passed:
                alert = build_tg_rebound_alert_simple(sym, direction, m["price"], rb_data, reasons)
                send_telegram_msg(alert)

        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    return jsonify({"ok": True, "data": results, "scanned": len(results)})

# ─── TELEGRAM MESSAGE BUILDERS ────────────────────────────
def _fmt(v):
    if not v: return "—"
    try: return f"${float(v):,.2f}"
    except: return "—"

def _rsi_line(sig):
    ind = sig.get("indicators", {})
    t15 = ind.get("15m",{}); t1h = ind.get("1h",{}); t4h = ind.get("4h",{})
    return f"📊 RSI 15m:{t15.get('rsi','—')} | 1h:{t1h.get('rsi','—')} | 4h:{t4h.get('rsi','—')}\n"

def _tg_escape(value, limit=None):
    text = "" if value is None else str(value)
    if limit is not None:
        text = text[:limit]
    for ch in (chr(92), "_", "*", "`", "["):
        text = text.replace(ch, chr(92) + ch)
    return text

def _tg_pct_text(entry, target, direction):
    try:
        e = float(entry); t = float(target)
        if e == 0:
            return ""
        pct = ((t - e) / e * 100.0) if direction == "Long" else ((e - t) / e * 100.0)
        sign = "+" if pct > 0 else ""
        return f"({sign}{pct:.1f}%)"
    except Exception:
        return ""

def _tg_level_line(icon, label, value, entry, direction, extra=""):
    rendered = _fmt(value) if value is not None else "—"
    pct = _tg_pct_text(entry, value, direction) if value is not None else ""
    suffix = f" {extra}" if extra else ""
    details = " ".join([x for x in [pct, suffix.strip()] if x]).strip()
    return f"{icon} {label}: {rendered}" + (f" {details}" if details else "") + "\n"

def _tg_header(title, badge, subtitle=None):
    msg = f"{title}\n⚡ *{badge}*\n"
    if subtitle:
        msg += f"_{subtitle}_\n"
    msg += "━━━━━━\n\n"
    return msg

def tg_trade_eligible(sig):
    is_manual_trade = (
        str(sig.get("gate_path", "")).startswith("MANUAL")
        and (sig.get("conf") or 0) >= 70
    )
    return bool(
        (sig.get("verdict") in ("APPROVED", "WEAK APPROVAL") or is_manual_trade)
        and sig.get("symbol")
        and sig.get("direction")
        and sig.get("entry")
        and sig.get("tp1")
        and (sig.get("hsl") or sig.get("ssl"))
    )

def tg_rejected_eligible(sig):
    return bool(
        sig.get("verdict") == "REJECTED"
        and (sig.get("conf") or 0) >= 50
        and sig.get("symbol")
        and sig.get("direction") in ("Long", "Short")
        and sig.get("entry")
        and sig.get("tp1")
        and (sig.get("hsl") or sig.get("ssl"))
    )

def build_tg_trade_msg(sig):
    """Trade alert for APPROVED / WEAK APPROVAL / eligible MANUAL signals."""
    verdict = sig["verdict"]
    is_manual_trade = (
        str(sig.get("gate_path", "")).startswith("MANUAL")
        and verdict not in ("APPROVED", "WEAK APPROVAL")
    )
    label = "MANUAL SIGNAL" if is_manual_trade else ("APPROVED SIGNAL" if verdict == "APPROVED" else "WEAK APPROVAL")
    sym = sig["symbol"].replace("USDT", "")
    direction = sig["direction"]
    title = ("🟢 LONG" if direction == "Long" else "🔴 SHORT") + f" {sym}/USDT | Conf: {sig.get('conf', '—')}/100"
    regime = sig.get("regime", "—")
    strat = sig.get("strategy_used", "—")
    gate = sig.get("gate_path", "—")
    subtitle = None
    if verdict == "WEAK APPROVAL":
        subtitle = "trade ได้ แต่ควรลด size หรือรอ confirmation เพิ่ม"
    elif is_manual_trade:
        subtitle = "manual trigger — ใช้ระดับราคาเดียวกับ dashboard"

    msg = _tg_header(title, label, subtitle)
    msg += f"🕐 {now_thai().strftime('%H:%M')} TH\n"
    msg += f"📍 {_tg_escape(regime)} | Strategy: {_tg_escape(strat)}\n"
    msg += f"🔀 Gate: {_tg_escape(gate)}\n\n"
    msg += _tg_level_line("💰", "Entry", sig.get("entry"), sig.get("entry"), direction, "(ตลาด)")
    msg += _tg_level_line("🎯", "TP1", sig.get("tp1"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP2", sig.get("tp2"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP3", sig.get("tp3"), sig.get("entry"), direction)
    msg += _tg_level_line("🛡", "Soft SL", sig.get("ssl"), sig.get("entry"), direction)
    msg += _tg_level_line("🛑", "Hard SL", sig.get("hsl"), sig.get("entry"), direction)
    msg += "\n"
    msg += _rsi_line(sig)
    ind = sig.get("indicators", {})
    t15 = ind.get("15m", {})
    msg += f"📈 MACD hist 15m: {t15.get('macd_hist', '—')}\n"
    if t15.get("vol_ratio"):
        msg += f"📦 Volume: {t15['vol_ratio']:.1f}x avg\n"
    if sig.get("funding_rate") is not None:
        msg += f"💸 Funding: {sig.get('funding_rate'):+.4f}%\n"
    if sig.get("risk_flags"):
        msg += f"\n⚠️ ความเสี่ยง: {_tg_escape(sig['risk_flags'][0], 180)}\n"
    if sig.get("reason"):
        msg += f"\n💬 {_tg_escape(sig['reason'], 260)}\n"
    return msg

# Keep old name as alias for callers
build_tg_msg = build_tg_trade_msg


def build_tg_rejected_msg(sig):
    """Rejected alert in the same visual style, with full levels for dashboard consistency."""
    sym = sig["symbol"].replace("USDT", "")
    direction = sig["direction"]
    gate = str(sig.get("gate_path", ""))
    manual_suffix = " (manual)" if gate.startswith("MANUAL") else ""
    title = ("🟢 LONG" if direction == "Long" else "🔴 SHORT") + f" {sym}/USDT | Conf: {sig.get('conf', '—')}/100{manual_suffix}"
    regime = sig.get("regime", "—")
    strat = sig.get("strategy_used", "—")
    rc = sig.get("reason_code", "—")
    reason = sig.get("reject_reason") or sig.get("reason") or "ไม่มีรายละเอียด"
    ind = sig.get("indicators", {})
    t15 = ind.get("15m", {})

    msg = _tg_header(title, "REJECTED")
    msg += f"🕐 {now_thai().strftime('%H:%M')} TH\n"
    msg += f"📍 {_tg_escape(_fmt_regime(regime))} | Strategy: {_tg_escape(strat)}\n"
    if gate:
        msg += f"🔀 Gate: {_tg_escape(gate)}\n"
    msg += "\n"
    msg += _tg_level_line("💰", "Entry", sig.get("entry"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP1", sig.get("tp1"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP2", sig.get("tp2"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP3", sig.get("tp3"), sig.get("entry"), direction)
    msg += _tg_level_line("🛡", "Soft SL", sig.get("ssl"), sig.get("entry"), direction)
    msg += _tg_level_line("🛑", "Hard SL", sig.get("hsl"), sig.get("entry"), direction)
    msg += "\n"
    if rc and rc != "—":
        msg += f"📋 Code: {_tg_escape(rc)}\n"
    msg += _rsi_line(sig)
    msg += f"📈 MACD hist 15m: {t15.get('macd_hist', '—')}\n"
    if t15.get("vol_ratio"):
        msg += f"📦 Volume: {t15['vol_ratio']:.1f}x avg\n"
    if sig.get("funding_rate") is not None:
        msg += f"💸 Funding: {sig.get('funding_rate'):+.4f}%\n"
    msg += f"\n💬 {_tg_escape(reason, 320)}\n"
    msg += "\n━━━━━━"
    return msg

def build_tg_rebound_alert(sig, rb_data):
    """Rebound alert with the unified Telegram layout."""
    verdict = sig["verdict"]
    is_manual_trade = (
        str(sig.get("gate_path", "")).startswith("MANUAL")
        and sig.get("entry")
        and sig.get("tp1")
        and (sig.get("hsl") or sig.get("ssl"))
        and (sig.get("conf") or 0) >= 70
    )
    sym = sig["symbol"].replace("USDT", "")
    direction = sig["direction"]
    title = ("🟢 LONG" if direction == "Long" else "🔴 SHORT") + f" {sym}/USDT | Conf: {sig.get('conf', '—')}/100"
    if is_manual_trade:
        label = "MANUAL SIGNAL"
        subtitle = "rebound setup — ใช้ระดับราคาตาม dashboard"
    elif verdict == "APPROVED":
        label = "APPROVED SIGNAL"
        subtitle = "rebound setup — tradeable"
    elif verdict == "WEAK APPROVAL":
        label = "WEAK APPROVAL"
        subtitle = "rebound setup — trade ได้แต่ควรลด size"
    else:
        label = "REJECTED"
        subtitle = "rebound setup — ไม่เทรด แต่ส่งเพื่อ review"
    regime = sig.get("regime", "—")
    strat = sig.get("strategy_used", "—")
    gate = sig.get("gate_path", "—")

    msg = _tg_header(title, label, subtitle)
    msg += f"🕐 {now_thai().strftime('%H:%M')} TH\n"
    msg += f"📍 {_tg_escape(regime)} | Strategy: {_tg_escape(strat)}\n"
    msg += f"🔀 Gate: {_tg_escape(gate)}\n\n"
    msg += _tg_level_line("💰", "Entry", sig.get("entry"), sig.get("entry"), direction, "(ตลาด)")
    msg += _tg_level_line("🎯", "TP1", sig.get("tp1"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP2", sig.get("tp2"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP3", sig.get("tp3"), sig.get("entry"), direction)
    msg += _tg_level_line("🛡", "Soft SL", sig.get("ssl"), sig.get("entry"), direction)
    msg += _tg_level_line("🛑", "Hard SL", sig.get("hsl"), sig.get("entry"), direction)
    msg += "\n"
    msg += f"📊 30m Score: L={rb_data.get('long_score', 0)} | S={rb_data.get('short_score', 0)}\n"
    msg += f"📈 BB%B: {rb_data.get('bb_pct', '—')} | RSI: {rb_data.get('rsi', '—')} | Vol: {rb_data.get('vol_ratio', '—')}x\n"
    if sig.get("funding_rate") is not None:
        msg += f"💸 Funding: {sig.get('funding_rate'):+.4f}%\n"
    reason = sig.get("reason") or sig.get("reject_reason") or sig.get("filter_reason")
    if reason:
        msg += f"\n💬 {_tg_escape(reason, 260)}\n"
    return msg


def build_tg_rebound_alert_simple(symbol, direction, price, rb_data, reasons):
    """Rebound scan alert in the same visual family as other Telegram messages."""
    sym = symbol.replace("USDT", "")
    conf = rb_data.get("conf") or rb_data.get("confidence") or "—"
    title = ("🟢 LONG" if direction == "Long" else "🔴 SHORT") + f" {sym}/USDT | Conf: {conf}"
    subtitle = rb_data.get("subtitle") or "15m/1h bounce — 4h ยังไม่ confirm"
    regime = rb_data.get("regime") or "RANGING"
    strategy = rb_data.get("strategy") or "REBOUND"
    gate = rb_data.get("gate_path") or "SCAN"

    msg = _tg_header(title, "REBOUND SCAN", subtitle)
    msg += f"🕐 {now_thai().strftime('%H:%M')} TH\n"
    msg += f"📍 {_tg_escape(regime)} | Strategy: {_tg_escape(strategy)}\n"
    msg += f"🔀 Gate: {_tg_escape(gate)}\n\n"
    msg += f"💰 Entry: {_fmt(price)} (ตลาด)\n"
    msg += f"📊 30m Score: L={rb_data.get('long_score', 0)} | S={rb_data.get('short_score', 0)}\n"
    msg += f"📈 BB%B: {rb_data.get('bb_pct', '—')} | RSI: {rb_data.get('rsi', '—')} | Vol: {rb_data.get('vol_ratio', '—')}x\n"
    msg += f"\n💬 {_tg_escape(' | '.join(reasons[:3]), 260)}\n"
    return msg

# ─── STARTUP ──────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 API server v4: {cfg.FLASK_HOST}:{cfg.FLASK_PORT}")
    print(f"📊 Dashboard: http://sasi.asia/dashboard")
    app.run(host=cfg.FLASK_HOST, port=cfg.FLASK_PORT, debug=False)
