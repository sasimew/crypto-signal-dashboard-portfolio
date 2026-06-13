#!/usr/bin/env python3
"""
Crypto Signal Bot — SET3 v1
════════════════════════════════════════════════════════════
การเปลี่ยนแปลงจาก SET2v2 (อิงจากการวิเคราะห์ log 2,456 signals)

Data basis: signal_log_20260428_20260511.json (2,456 signals, 10 วัน)
  - LOW_LIQUIDITY_BLOCK: 806 signals (32.8%) — avg pre_conf_before_block=61
    → 22.6% ของ vol_ratio=0.000 เป็น missing data ไม่ใช่ low vol จริง
  - Claude reject rate: 96.1% (688/716) — 203 cases entry_ok=True แต่ถูก reject
    เหตุ: 4h/1h RSI overbought (>70-85) = exhaustion zone ไม่ใช่ entry zone
  - REBOUND: 0 APPROVED ใน 10 วัน — WEAK_MARGIN_BLOCK 65/95 สัญญาณ
    margin requirement=4 แต่ data จริงได้ max=3 สำหรับ short-term exhaustion signals
  - 493 TREND_FOLLOW signals ที่ 1h RSI>70 หรือ <30 = missed SHORT/LONG rebound

[SET3-1] VOL_RATIO MISSING DATA FIX
  vol_ratio=0.000 → ข้ามการ check (missing data ≠ low volume)
  vol_ratio 0<x<0.1 → ยัง block เหมือนเดิม
  เพิ่ม log field: vol_data_missing=True/False

[SET3-2] HTF EXHAUSTION REBOUND SCORER
  เพิ่ม check_htf_exhaustion() แยกออกมาจาก check_rebound_30m()
  trigger เมื่อ 4h RSI>72 (overbought) หรือ <28 (oversold)
  + 1h RSI confirm momentum slowing
  + 15m early reversal sign (ไม่ต้องรอ 30m confirm)
  strategy label: "REBOUND_HTF" (แยกจาก REBOUND เดิม เพื่อ track win rate แยก)

[SET3-3] REBOUND MARGIN REDUCTION (HTF PATH ONLY)
  REBOUND_HTF: margin >= 2 (จาก 4)
  REBOUND ปกติ (30m): ยังคง margin >= 4
  เหตุผล: HTF exhaustion signal มี structural confirmation จาก higher TF
  → margin ต่ำได้ เพราะ 4h RSI ยืนยันแล้ว

[SET3-4] EXHAUSTION PENALTY — ลด Long pre_conf ที่ overbought extreme
  4h RSI>72 + BB_pct>0.90 + direction=Long → pre_conf -= 10
  block_reason_code: "HTF_EXHAUSTION_LONG"
  → ลด wasted Claude calls ~200 signals/10วัน

[SET3-5] SYSTEM PROMPT UPDATE
  เพิ่ม REBOUND_HTF rules สำหรับ Claude
  เพิ่ม exhaustion zone guidance (4h RSI>72 = caution zone for LONG)

Data evidence:
  - REBOUND short_score=6/long_score=4 (margin=2) blocked 65 สัญญาณใน 10 วัน
  - 1h RSI>70 → 400 TREND_FOLLOW signals = missed SHORT rebound opportunities
  - vol_ratio=0.000 blocked 547 signals — median vol_ratio of ALL blocked = 0.000
════════════════════════════════════════════════════════════
"""
import json, os, sys, time, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config_set2 as cfg
except ImportError:
    import config as cfg

TZ_THAI = timezone(timedelta(hours=7))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATTERN_MEMORY_PATH = os.path.join(BASE_DIR, "data", "pattern_memory_beta.json")
PATTERN_MEMORY_SUMMARY_PATH = os.path.join(BASE_DIR, "data", "beta_research_summary.json")
_PATTERN_MEMORY_CACHE = None

def now_thai():
    return datetime.now(TZ_THAI)

def log(msg):
    print(f"[{now_thai().strftime('%Y-%m-%d %H:%M:%S')} TH] {msg}", flush=True)

def http_get(url, timeout=15):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log(f"HTTP GET {url[:50]}: {e}")
        return None

def http_post(url, headers, body, timeout=40):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log(f"HTTP POST: {e}")
        return None

def sfloat(v):
    try:
        if v in (None, "", "—"):
            return None
        return float(str(v).replace(",", ""))
    except Exception:
        return None

def clamp(v, lo=0, hi=95):
    return max(lo, min(hi, v))

def conf_bucket(score):
    score = sfloat(score)
    if score is None:
        return "unknown"
    if score < 25: return "00_24"
    if score < 35: return "25_34"
    if score < 45: return "35_44"
    if score < 55: return "45_54"
    if score < 65: return "55_64"
    if score < 75: return "65_74"
    if score < 85: return "75_84"
    return "85_plus"

def ctx_bucket(ctx_bonus):
    ctx_bonus = sfloat(ctx_bonus)
    if ctx_bonus is None or ctx_bonus == 0:
        return "neutral"
    return "pos" if ctx_bonus > 0 else "neg"

def tf_bias(tf):
    tf = tf or {}
    e_s = sfloat(tf.get("ema_short"))
    e_m = sfloat(tf.get("ema_mid"))
    macd = sfloat(tf.get("macd_hist"))
    rsi = sfloat(tf.get("rsi"))
    if e_s is not None and e_m is not None:
        if e_s > e_m: return "BULLISH"
        if e_s < e_m: return "BEARISH"
    if macd is not None:
        if macd > 0: return "BULLISH"
        if macd < 0: return "BEARISH"
    if rsi is not None:
        if rsi >= 55: return "BULLISH"
        if rsi <= 45: return "BEARISH"
    return "NEUTRAL"

def mtf_alignment_bucket(direction, t15=None, t1h=None, t2h=None, t4h=None):
    want = "BULLISH" if direction == "Long" else "BEARISH"
    checks = [tf_bias(t15), tf_bias(t1h), tf_bias(t2h), tf_bias(t4h)]
    total = sum(1 for b in checks if b != "NEUTRAL")
    aligned = sum(1 for b in checks if b == want)
    ratio = (aligned / total) if total else 0
    bucket = "high" if ratio >= 0.75 else "mid" if ratio >= 0.5 else "low"
    conflict = any(b not in ("NEUTRAL", want) for b in checks if b)
    return bucket, aligned, total, conflict

def load_pattern_memory():
    global _PATTERN_MEMORY_CACHE
    if _PATTERN_MEMORY_CACHE is not None:
        return _PATTERN_MEMORY_CACHE
    memory = {"patterns": {}, "summary": {}}
    try:
        if os.path.exists(PATTERN_MEMORY_PATH):
            with open(PATTERN_MEMORY_PATH, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                memory["patterns"] = payload
        if os.path.exists(PATTERN_MEMORY_SUMMARY_PATH):
            with open(PATTERN_MEMORY_SUMMARY_PATH, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                memory["summary"] = payload
    except Exception as e:
        log(f"⚠️ pattern_memory load failed: {e}")
    _PATTERN_MEMORY_CACHE = memory
    return _PATTERN_MEMORY_CACHE

def build_pattern_key(strategy, regime, direction, score, ctx_bonus, mtf_bucket):
    return "|".join([
        str(strategy or "na"),
        str(regime or "na"),
        str(direction or "na"),
        conf_bucket(score),
        ctx_bucket(ctx_bonus),
        str(mtf_bucket or "na"),
    ])

def pattern_memory_adjustment(pattern_key):
    memory = load_pattern_memory().get("patterns", {})
    entry = memory.get(pattern_key) or {}
    sample = int(entry.get("sample_size") or 0)
    tp_rate = sfloat(entry.get("tp_rate")) or 0
    miss_rate = sfloat(entry.get("miss_rate")) or 0
    sl_rate = sfloat(entry.get("sl_rate")) or 0
    adjust = 0
    reasons = []
    if sample >= 20:
        if tp_rate < 0.20 and miss_rate > 0.60:
            adjust -= 12
            reasons.append(f"pattern weak tp={tp_rate:.2f} miss={miss_rate:.2f}")
        elif tp_rate > 0.45 and miss_rate < 0.35:
            adjust += 6
            reasons.append(f"pattern strong tp={tp_rate:.2f}")
        elif sl_rate > 0.15:
            adjust -= 6
            reasons.append(f"pattern sl risk={sl_rate:.2f}")
    return adjust, entry, reasons

def classify_runtime_error(exc):
    msg = str(exc or "")
    low = msg.lower()
    if "anthropic_api_key not configured" in low:
        return "CLAUDE_KEY", "Claude API key not configured"
    if "timed out" in low or "timeout" in low:
        return "CLAUDE_TIMEOUT", "Claude request timed out"
    if "429" in low or "rate limit" in low or "overloaded" in low:
        return "CLAUDE_RATE_LIMIT", "Claude rate limit / overloaded"
    if "temporary failure" in low or "name or service not known" in low:
        return "CLAUDE_NETWORK", "Claude network error"
    return "RUNTIME_ERROR", msg[:180] if msg else "Unknown runtime error"

# ═══════════════════════════════════════════════════════════
# INDICATOR SET 3
# ═══════════════════════════════════════════════════════════
INDICATOR_SET = {
    "name":             f"{cfg.BOT_VERSION} — SET3: HTF Exhaustion Rebound + Vol Missing Fix",
    "version":          "3.0",
    "configured":       "2026-05-11",
    "review_after":     "2026-05-18",
    "RSI_OVERBOUGHT":   cfg.RSI_OVERBOUGHT,
    "RSI_OVERSOLD":     cfg.RSI_OVERSOLD,
    "EMA_SHORT":        cfg.EMA_SHORT,
    "EMA_MID":          cfg.EMA_MID,
    "EMA_LONG":         cfg.EMA_LONG,
    "EMA_CROSS_BUFFER": cfg.EMA_CROSS_BUFFER,
    "MIN_RR":           cfg.MIN_RR,
    "MACD":             f"{cfg.MACD_FAST}/{cfg.MACD_SLOW}/{cfg.MACD_SIGNAL}",
    "MACD_FAST":        cfg.MACD_FAST,
    "MACD_SLOW":        cfg.MACD_SLOW,
    "MACD_SIGNAL":      cfg.MACD_SIGNAL,
    "BB":               f"{cfg.BB_PERIOD} period {cfg.BB_STD}SD",
    "BB_PERIOD":        cfg.BB_PERIOD,
    "BB_STD":           cfg.BB_STD,
    "VOLUME_AVG":       cfg.VOLUME_AVG,
    "SWING_LOOKBACK":   cfg.SWING_LOOKBACK,
    "TF_BIAS":          cfg.TF_BIAS,
    "TF_DIRECTION":     cfg.TF_DIRECTION,
    "TF_ENTRY":         cfg.TF_ENTRY,
    "TF_REBOUND":       cfg.TF_REBOUND,
    # Rebound (unchanged from SET2)
    "REBOUND_BB_LOW":           cfg.REBOUND_BB_LOW,
    "REBOUND_BB_HIGH":          cfg.REBOUND_BB_HIGH,
    "REBOUND_VOL_MIN":          cfg.REBOUND_VOL_MIN,
    "REBOUND_SWING_BUFFER":     cfg.REBOUND_SWING_BUFFER,
    "REBOUND_MIN_SCORE":        cfg.REBOUND_MIN_SCORE,
    "REBOUND_MAX_SCORE":        cfg.REBOUND_MAX_SCORE,
    "REBOUND_1H_RSI_GUARD":     cfg.REBOUND_1H_RSI_GUARD,
    "REBOUND_RSI_TURN_WEIGHT":  cfg.REBOUND_RSI_TURN_WEIGHT,
    "REBOUND_MIN_MARGIN":       cfg.REBOUND_MIN_MARGIN,
    "REBOUND_MIN_SCORE_FOR_CLAUDE": cfg.REBOUND_MIN_SCORE_FOR_CLAUDE,
    # [SET3] HTF Exhaustion thresholds
    "SET3_HTF_RSI_OB":          72,   # 4h RSI overbought threshold
    "SET3_HTF_RSI_OS":          28,   # 4h RSI oversold threshold
    "SET3_HTF_RSI_1H_SLOW":     68,   # 1h RSI momentum slowing (short)
    "SET3_HTF_RSI_1H_SLOW_L":   32,   # 1h RSI momentum slowing (long)
    "SET3_HTF_BB_OB":           0.90, # 4h BB% overbought
    "SET3_HTF_BB_OS":           0.10, # 4h BB% oversold
    "SET3_HTF_MARGIN_MIN":      2,    # HTF path: margin >=2 (vs 30m path: >=4)
    "SET3_EXHAUSTION_RSI_OB":   72,   # 4h RSI threshold for TREND_FOLLOW penalty
    "SET3_EXHAUSTION_BB_OB":    0.90, # 4h BB% threshold for TREND_FOLLOW penalty
    "SET3_EXHAUSTION_PENALTY":  10,   # pre_conf penalty for overbought LONG
    # Regime (unchanged)
    "REGIME_ATR_TREND":         cfg.REGIME_ATR_TREND,
    "REGIME_ATR_VOLATILE":      cfg.REGIME_ATR_VOLATILE,
    "REGIME_BB_RANGING":        cfg.REGIME_BB_RANGING,
    "REGIME_EMA_DIFF":          cfg.REGIME_EMA_DIFF,
    "REGIME_MACD4H_STRONG":     cfg.REGIME_MACD4H_STRONG,
    "REGIME_MACD4H_MEDIUM":     cfg.REGIME_MACD4H_MEDIUM,
    "REGIME_MACD4H_WEAK":       cfg.REGIME_MACD4H_WEAK,
    # Multi-TF
    "MTF_4H_RSI_BULL":          cfg.MTF_4H_RSI_BULL,
    "MTF_4H_RSI_BEAR":          cfg.MTF_4H_RSI_BEAR,
    "MTF_1H_RSI_CONFIRM":       cfg.MTF_1H_RSI_CONFIRM,
    # Gate
    "FILTER_MIN_CONF":          cfg.FILTER_MIN_CONF,
    "WEAK_MIN_CONF":            cfg.WEAK_MIN_CONF,
    "CLAUDE_MIN_CONF":          cfg.CLAUDE_MIN_CONF,
    "AUTO_APPROVE_CONF":        cfg.AUTO_APPROVE_CONF,
    "REBOUND_AUTO_APPROVE_CONF":cfg.REBOUND_AUTO_APPROVE_CONF,
    "REBOUND_TIER5_MIN_SCORE":  cfg.REBOUND_TIER5_MIN_SCORE,
    "REBOUND_TIER5_MIN_MARGIN": cfg.REBOUND_TIER5_MIN_MARGIN,
    # Market Context
    "CTX_LS_SHORTS_EXTREME":    cfg.CTX_LS_SHORTS_EXTREME,
    "CTX_LS_SHORTS_HIGH":       cfg.CTX_LS_SHORTS_HIGH,
    "CTX_LS_LONGS_HIGH":        cfg.CTX_LS_LONGS_HIGH,
    "CTX_LS_LONGS_EXTREME":     cfg.CTX_LS_LONGS_EXTREME,
    "CTX_TAKER_AGGRESSIVE":     cfg.CTX_TAKER_AGGRESSIVE,
    "CTX_TAKER_MODERATE":       cfg.CTX_TAKER_MODERATE,
    "CTX_OI_DROP_STRONG":       cfg.CTX_OI_DROP_STRONG,
    "CTX_OI_DROP_MILD":         cfg.CTX_OI_DROP_MILD,
    "CTX_VOL_MIN_FOR_SIGNAL":   cfg.CTX_VOL_MIN_FOR_SIGNAL,
    "CTX_BONUS_MAX":            cfg.CTX_BONUS_MAX,
}

S = INDICATOR_SET

# ═══════════════════════════════════════════════════════════
# INDICATORS (unchanged from SET2)
# ═══════════════════════════════════════════════════════════

def get_klines(symbol, interval, limit=150):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    for attempt in range(3):
        result = http_get(url, timeout=20)
        if result and isinstance(result, list) and len(result) > 0:
            return result
        log(f"⚠️ get_klines attempt {attempt+1}/3: {symbol} {interval}")
        time.sleep(1.5)
    return []

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
    gains  = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g  = sum(gains[-period:]) / period
    avg_l  = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast  = calc_ema(closes, fast)
    ema_slow  = calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_line = ema_fast - ema_slow
    macd_hist = macd_line
    return macd_line, macd_line * 0.9, macd_hist

def calc_bollinger(closes, period=20, std_mult=2):
    if len(closes) < period:
        return None, None, None, None, None
    window = closes[-period:]
    mid    = sum(window) / period
    std    = (sum((c - mid)**2 for c in window) / period) ** 0.5
    upper  = mid + std_mult * std
    lower  = mid - std_mult * std
    bb_pct = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    bb_width = (upper - lower) / mid * 100
    return upper, lower, mid, round(bb_pct, 4), round(bb_width, 4)

def calc_volume_ratio(volumes, avg_period=20):
    if len(volumes) < avg_period + 1:
        return None
    avg = sum(volumes[-avg_period-1:-1]) / avg_period
    return round(volumes[-1] / avg, 2) if avg > 0 else None

def calc_swing_levels(highs, lows, lookback=20):
    if len(highs) < lookback:
        return None, None
    h_window = highs[-lookback:]
    l_window = lows[-lookback:]
    return max(h_window), min(l_window)

def calc_tf_data(klines):
    if not klines or len(klines) < 30:
        return None
    S = INDICATOR_SET
    opens   = [float(k[1]) for k in klines]
    highs   = [float(k[2]) for k in klines]
    lows    = [float(k[3]) for k in klines]
    closes  = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    ema9  = calc_ema(closes, S["EMA_SHORT"])
    ema21 = calc_ema(closes, S["EMA_MID"])
    ema200= calc_ema(closes, S["EMA_LONG"])
    rsi   = calc_rsi(closes)
    ml, ms, mh = calc_macd(closes, S["MACD_FAST"], S["MACD_SLOW"], S["MACD_SIGNAL"])
    bbu, bbl, bbm, bbp, bbw = calc_bollinger(closes, S["BB_PERIOD"], S["BB_STD"])
    vr    = calc_volume_ratio(volumes, S["VOLUME_AVG"])
    sh, sl_val = calc_swing_levels(highs, lows, S["SWING_LOOKBACK"])

    price  = closes[-1]
    h_last = highs[-1]
    l_last = lows[-1]
    atr    = (h_last - l_last)

    return {
        "ema_short": ema9, "ema_mid": ema21, "ema_long": ema200,
        "rsi": rsi,
        "macd_line": ml, "macd_signal": ms, "macd_hist": mh,
        "bb_upper": bbu, "bb_lower": bbl, "bb_mid": bbm,
        "bb_pct": bbp, "bb_width": bbw,
        "vol_ratio": vr,
        "atr": atr,
        "swings": {"swing_high": sh, "swing_low": sl_val},
        "close": price,
    }

# ═══════════════════════════════════════════════════════════
# REGIME DETECTION (unchanged from SET2)
# ═══════════════════════════════════════════════════════════
def detect_regime(tf_1h, tf_4h, price):
    S       = INDICATOR_SET
    reasons = []
    scores  = {"TRENDING": 0, "RANGING": 0, "VOLATILE": 0}

    if not tf_1h or not tf_4h:
        return "MIXED", 50, ["ข้อมูลไม่ครบ"]

    atr_pct_1h = (tf_1h["atr"] / price * 100) if tf_1h.get("atr") else 0
    atr_pct_4h = (tf_4h["atr"] / price * 100) if tf_4h.get("atr") else 0
    avg_atr_pct = (atr_pct_1h + atr_pct_4h) / 2

    mh4     = tf_4h.get("macd_hist") or 0
    mh4_abs = abs(mh4)
    mh4_dir = "BULL" if mh4 > 0 else "BEAR" if mh4 < 0 else "FLAT"

    if mh4_abs > S["REGIME_MACD4H_STRONG"]:
        scores["TRENDING"] += 4
        reasons.append(f"MACD4h hist={mh4:.1f} ({mh4_dir}) — STRONG TREND (+4)")
    elif mh4_abs > S["REGIME_MACD4H_MEDIUM"]:
        scores["TRENDING"] += 2
        reasons.append(f"MACD4h hist={mh4:.1f} ({mh4_dir}) — MEDIUM TREND (+2)")
    elif mh4_abs > S["REGIME_MACD4H_WEAK"]:
        scores["TRENDING"] += 1
        reasons.append(f"MACD4h hist={mh4:.1f} ({mh4_dir}) — WEAK TREND (+1)")
    else:
        reasons.append(f"MACD4h hist={mh4:.1f} — FLAT (no TRENDING vote)")

    macd_flat = mh4_abs <= S["REGIME_MACD4H_WEAK"]

    if avg_atr_pct > S["REGIME_ATR_VOLATILE"]:
        scores["VOLATILE"] += 3
        reasons.append(f"ATR สูงมาก {avg_atr_pct:.2f}% → VOLATILE")
    elif avg_atr_pct > S["REGIME_ATR_TREND"]:
        scores["TRENDING"] += 2
        reasons.append(f"ATR ปกติ {avg_atr_pct:.2f}% → TRENDING")
    else:
        if macd_flat:
            scores["RANGING"] += 2
            reasons.append(f"ATR ต่ำ {avg_atr_pct:.2f}% + MACD flat → RANGING (+2)")
        else:
            scores["RANGING"] += 1
            reasons.append(f"ATR ต่ำ {avg_atr_pct:.2f}% แต่ MACD trending → RANGING อ่อน (+1)")

    bbw = tf_1h.get("bb_width", 5)
    if bbw < S["REGIME_BB_RANGING"]:
        if macd_flat:
            scores["RANGING"] += 2
            reasons.append(f"BB width แคบ {bbw:.1f}% + MACD flat → RANGING (+2)")
        else:
            scores["RANGING"] += 1
            reasons.append(f"BB width แคบ {bbw:.1f}% แต่ MACD trending → RANGING อ่อน (+1)")
    elif bbw > 6:
        scores["TRENDING"] += 1
        reasons.append(f"BB width กว้าง {bbw:.1f}% → TRENDING (+1)")

    e_s4 = tf_4h.get("ema_short"); e_m4 = tf_4h.get("ema_mid")
    if e_s4 and e_m4:
        ema_diff_4h = abs(e_s4 - e_m4) / e_m4 * 100
        if ema_diff_4h > S["REGIME_EMA_DIFF"]:
            scores["TRENDING"] += 2
            reasons.append(f"EMA4h ห่างกัน {ema_diff_4h:.2f}% → TRENDING (+2)")
        else:
            scores["RANGING"] += 1
            reasons.append(f"EMA4h ใกล้กัน {ema_diff_4h:.2f}% → RANGING (+1)")

    e_s1 = tf_1h.get("ema_short"); e_m1 = tf_1h.get("ema_mid")
    if e_s1 and e_m1:
        ema_diff_1h = abs(e_s1 - e_m1) / e_m1 * 100
        if ema_diff_1h > S["REGIME_EMA_DIFF"]:
            scores["TRENDING"] += 1
            reasons.append(f"EMA1h ห่างกัน {ema_diff_1h:.2f}% → TRENDING (+1)")

    total = sum(scores.values()) or 1
    conf  = min(round(max(scores.values()) / total * 100), 95)

    if scores["VOLATILE"] >= 3 and scores["VOLATILE"] > max(scores["TRENDING"], scores["RANGING"]):
        return "VOLATILE", conf, reasons
    if scores["TRENDING"] > scores["RANGING"]:
        return "TRENDING", conf, reasons
    if scores["RANGING"] >= scores["TRENDING"]:
        return "RANGING", conf, reasons
    return "MIXED", 50, reasons

# ═══════════════════════════════════════════════════════════
# TREND FOLLOW (unchanged from SET2)
# ═══════════════════════════════════════════════════════════
def check_multi_tf(tf_4h, tf_1h, tf_15m, price, tf_2h=None, market_ctx=None):
    S       = INDICATOR_SET
    reasons = []
    tf_bk   = {}

    if not tf_4h or not tf_1h or not tf_15m:
        return False, "N/A", ["ข้อมูล TF ไม่ครบ"], {}

    def htf_bias(tf, label):
        e_s = tf.get("ema_short") if tf else None
        e_m = tf.get("ema_mid") if tf else None
        rsi = tf.get("rsi") if tf else None
        mh  = (tf or {}).get("macd_hist") or 0
        bias = "NEUTRAL"
        local_reasons = []
        if not (e_s and e_m and rsi):
            local_reasons.append(f"{label} Bias: NEUTRAL (ข้อมูลไม่ครบ)")
            return bias, e_s, e_m, rsi, mh, local_reasons
        bull_conditions = [e_s > e_m, rsi > S["MTF_4H_RSI_BULL"], price > e_m]
        bear_conditions = [e_s < e_m, rsi < S["MTF_4H_RSI_BEAR"], price < e_m]
        macd_bull = mh > 0
        macd_bear = mh < 0
        if sum(bull_conditions) >= 2 and macd_bull:
            bias = "BULLISH"
            local_reasons.append(f"{label} Bias: BULLISH (EMA9>EMA21, RSI={rsi:.1f}, MACD={mh:.1f}+)")
        elif sum(bear_conditions) >= 2 and macd_bear:
            bias = "BEARISH"
            local_reasons.append(f"{label} Bias: BEARISH (EMA9<EMA21, RSI={rsi:.1f}, MACD={mh:.1f}-)")
        elif sum(bull_conditions) >= 2:
            bias = "BULLISH"
            local_reasons.append(f"{label} Bias: BULLISH (EMA+RSI aligned, MACD={mh:.1f} weak)")
        elif sum(bear_conditions) >= 2:
            bias = "BEARISH"
            local_reasons.append(f"{label} Bias: BEARISH (EMA+RSI aligned, MACD={mh:.1f} weak)")
        else:
            local_reasons.append(f"{label} Bias: NEUTRAL (RSI={rsi:.1f}, MACD={mh:.1f})")
        return bias, e_s, e_m, rsi, mh, local_reasons

    bias_4h, e_s4, e_m4, rsi_4, mh_4, bias_reasons = htf_bias(tf_4h, "4h")
    reasons.extend(bias_reasons)
    tf_bk["4h"] = {"bias": bias_4h, "ema_short": e_s4, "ema_mid": e_m4,
                   "rsi": rsi_4, "macd_hist": mh_4}

    bias_source = "4h"
    bias_fallback = False
    if bias_4h == "NEUTRAL":
        bias_2h, e_s2, e_m2, rsi_2, mh_2, bias2_reasons = htf_bias(tf_2h or {}, "2h")
        reasons.extend(bias2_reasons)
        tf_bk["2h"] = {"bias": bias_2h, "ema_short": e_s2, "ema_mid": e_m2,
                       "rsi": rsi_2, "macd_hist": mh_2, "fallback_bias": bias_2h != "NEUTRAL"}
        if bias_2h == "NEUTRAL":
            return False, "N/A", reasons, tf_bk
        candidate_dir = "Long" if bias_2h == "BULLISH" else "Short"
        support, oppose = market_context_alignment_score(market_ctx or {}, candidate_dir)
        if not market_ctx or market_ctx.get("ls_ratio") is None:
            reasons.append(f"2h fallback allowed: L/S context unavailable for {candidate_dir}")
        elif oppose >= 2 and support == 0:
            reasons.append(f"2h fallback blocked: market context strongly opposes {candidate_dir}")
            return False, "N/A", reasons, tf_bk
        elif not is_market_context_aligned(market_ctx, candidate_dir):
            reasons.append(f"2h fallback allowed with caution: market context not aligned for {candidate_dir}")
        bias_4h = bias_2h
        bias_source = "2h"
        bias_fallback = True
        reasons.append(f"Using 2h fallback bias: {bias_2h} (4h neutral)")

    tf_bk["bias_source"] = bias_source
    tf_bk["bias_fallback"] = bias_fallback

    e_s1  = tf_1h.get("ema_short"); e_m1 = tf_1h.get("ema_mid")
    rsi_1 = tf_1h.get("rsi"); mh_1 = tf_1h.get("macd_hist")
    dir_1h = "NEUTRAL"

    if e_s1 and e_m1 and rsi_1:
        if bias_4h == "BULLISH":
            if e_s1 > e_m1 and rsi_1 > S["MTF_1H_RSI_CONFIRM"]:
                dir_1h = "LONG"
                reasons.append(f"1h Direction: LONG (EMA9>{e_m1:.0f}, RSI={rsi_1:.1f}, bias={bias_source})")
            elif cfg.TREND_USE_PULLBACK_ENTRY and e_s1 > e_m1 * 0.998 and rsi_1 > 40:
                dir_1h = "LONG"
                reasons.append(f"1h Direction: LONG pullback (price near EMA21, RSI={rsi_1:.1f})")
            else:
                reasons.append(f"1h Direction: ไม่ confirm (RSI={rsi_1:.1f}, EMA9/21 ratio={(e_s1/e_m1):.4f})")
        elif bias_4h == "BEARISH":
            if e_s1 < e_m1 and rsi_1 < (100 - S["MTF_1H_RSI_CONFIRM"]):
                dir_1h = "SHORT"
                reasons.append(f"1h Direction: SHORT (EMA9<{e_m1:.0f}, RSI={rsi_1:.1f}, bias={bias_source})")
            elif cfg.TREND_USE_PULLBACK_ENTRY and e_s1 < e_m1 * 1.002 and rsi_1 < 60:
                dir_1h = "SHORT"
                reasons.append(f"1h Direction: SHORT pullback (price near EMA21, RSI={rsi_1:.1f})")
            else:
                reasons.append(f"1h Direction: ไม่ confirm (RSI={rsi_1:.1f})")

    tf_bk["1h"] = {"direction": dir_1h, "ema_short": e_s1, "ema_mid": e_m1,
                   "rsi": rsi_1, "macd_hist": mh_1}

    if dir_1h == "NEUTRAL":
        return False, "N/A", reasons, tf_bk

    e_s15  = tf_15m.get("ema_short"); e_m15 = tf_15m.get("ema_mid")
    rsi_15 = tf_15m.get("rsi"); mh_15 = tf_15m.get("macd_hist")
    bbp_15 = tf_15m.get("bb_pct"); vr_15 = tf_15m.get("vol_ratio")
    entry_ok = False
    buf = S["EMA_CROSS_BUFFER"]

    if dir_1h == "LONG":
        ema_ok = bool(e_s15 and e_m15 and e_s15 > e_m15 * (1 + buf))
        rsi_ok = bool(rsi_15 and rsi_15 < cfg.RSI_OVERBOUGHT)
        if ema_ok and rsi_ok:
            entry_ok = True
            reasons.append(f"15m Entry: LONG ✅ EMA aligned | RSI={rsi_15:.1f} | MACD={mh_15:.2f}")
        else:
            fail = []
            if not ema_ok: fail.append(f"EMA9({e_s15:.0f})<EMA21({e_m15:.0f})" if e_s15 and e_m15 else "EMA missing")
            if not rsi_ok: fail.append(f"RSI={rsi_15:.1f} overbought")
            reasons.append(f"15m Entry: ไม่ผ่าน [{' | '.join(fail)}]")
    elif dir_1h == "SHORT":
        ema_ok = bool(e_s15 and e_m15 and e_s15 < e_m15 * (1 - buf))
        rsi_ok = bool(rsi_15 and rsi_15 > cfg.RSI_OVERSOLD)
        if ema_ok and rsi_ok:
            entry_ok = True
            reasons.append(f"15m Entry: SHORT ✅ EMA aligned | RSI={rsi_15:.1f} | MACD={mh_15:.2f}")
        else:
            fail = []
            if not ema_ok: fail.append(f"EMA9({e_s15:.0f})>EMA21({e_m15:.0f})" if e_s15 and e_m15 else "EMA missing")
            if not rsi_ok: fail.append(f"RSI={rsi_15:.1f} oversold")
            reasons.append(f"15m Entry: ไม่ผ่าน [{' | '.join(fail)}]")

    tf_bk["15m"] = {"entry_ok": entry_ok, "ema_short": e_s15, "ema_mid": e_m15,
                    "rsi": rsi_15, "macd_hist": mh_15, "bb_pct": bbp_15, "vol_ratio": vr_15}

    direction = "Long" if dir_1h == "LONG" else "Short"
    return entry_ok, direction, reasons, tf_bk

# ═══════════════════════════════════════════════════════════
# HELPERS (unchanged)
# ═══════════════════════════════════════════════════════════
def is_bullish_stack(tf):
    es = tf.get("ema_short") or 0; em = tf.get("ema_mid") or 0
    return es > em and es > 0 and em > 0

def is_bearish_stack(tf):
    es = tf.get("ema_short") or 0; em = tf.get("ema_mid") or 0
    return es < em and es > 0 and em > 0

def should_block_countertrend_rebound(direction, tf_1h, tf_15m):
    if not cfg.BLOCK_COUNTERTREND:
        return False, ""
    if direction == "Short":
        if is_bullish_stack(tf_1h or {}) and is_bullish_stack(tf_15m or {}):
            r1h = (tf_1h or {}).get("rsi", 50)
            return True, f"COUNTERTREND BLOCK: SHORT แต่ 1h+15m EMA bullish (1h RSI={r1h:.0f})"
    if direction == "Long":
        if is_bearish_stack(tf_1h or {}) and is_bearish_stack(tf_15m or {}):
            r1h = (tf_1h or {}).get("rsi", 50)
            return True, f"COUNTERTREND BLOCK: LONG แต่ 1h+15m EMA bearish (1h RSI={r1h:.0f})"
    return False, ""

# ═══════════════════════════════════════════════════════════
# SHORT PRE-FILTER (unchanged from SET2)
# ═══════════════════════════════════════════════════════════
def should_block_short_set2(tf_4h, tf_1h, rb_data):
    mh_4  = (tf_4h or {}).get("macd_hist") or 0
    mh_1  = (tf_1h or {}).get("macd_hist") or 0
    rsi_1 = (tf_1h or {}).get("rsi") or 50
    rsi_turn_dn = rb_data.get("rsi_turning_down", False)
    short_score = rb_data.get("short_score", 0)
    if cfg.SHORT_REQUIRE_4H_BEARISH and mh_4 >= 0:
        return True, f"SHORT BLOCK: 4h MACD={mh_4:.1f} ≥0 (ยังไม่ bearish)"
    if mh_1 > cfg.SHORT_BLOCK_1H_MACD_STRONG:
        return True, f"SHORT BLOCK: 1h MACD={mh_1:.1f} > {cfg.SHORT_BLOCK_1H_MACD_STRONG} (strong uptrend)"
    if cfg.SHORT_REQUIRE_RSI_TURN_DOWN and not rsi_turn_dn:
        return True, f"SHORT BLOCK: rsi_turning_down=False (ต้องการ momentum reversal)"
    if short_score < cfg.REBOUND_MIN_SCORE_FOR_CLAUDE:
        return True, f"SHORT BLOCK: short_score={short_score} < {cfg.REBOUND_MIN_SCORE_FOR_CLAUDE}"
    if rsi_1 > cfg.SHORT_REQUIRE_1H_RSI_MAX:
        return True, f"SHORT BLOCK: 1h RSI={rsi_1:.1f} > {cfg.SHORT_REQUIRE_1H_RSI_MAX} (overbought แต่ EMA ยัง bull)"
    return False, ""

# ═══════════════════════════════════════════════════════════
# [SET3-NEW] HTF EXHAUSTION REBOUND SCORER
# ═══════════════════════════════════════════════════════════
def check_htf_exhaustion(tf_4h, tf_1h, tf_15m, price):
    """
    SET3 — HTF Exhaustion Rebound Detection

    Problem solved: REBOUND 30m ต้องการ margin>=4 ทำให้ block 68%
    แต่ signals ที่ 4h RSI>72 + BB ชิดขอบ = structural exhaustion
    ไม่จำเป็นต้องรอ 30m confirm เพราะ HTF ยืนยันแล้ว

    Logic:
      SHORT exhaustion: 4h RSI>72 + 1h RSI>68 + 15m early weakening
      LONG exhaustion:  4h RSI<28 + 1h RSI<32 + 15m early recovery

    Returns: (passed, direction, reasons, htf_data)
    htf_data ใช้ structure เดิมของ rb_data เพื่อ compatibility กับ build_signal

    margin ใช้ 2 แทน 4 เพราะ HTF ทำหน้าที่ confirm แทน 30m margin
    """
    S = INDICATOR_SET
    reasons = []
    htf_data = {}

    if not tf_4h or not tf_1h:
        return False, "N/A", ["ข้อมูล HTF ไม่ครบ"], {}

    rsi_4h  = tf_4h.get("rsi") or 50
    bb_4h   = tf_4h.get("bb_pct") or 0.5
    mh_4h   = tf_4h.get("macd_hist") or 0
    rsi_1h  = tf_1h.get("rsi") or 50
    mh_1h   = tf_1h.get("macd_hist") or 0

    rsi_15m = (tf_15m or {}).get("rsi") or 50
    mh_15m  = (tf_15m or {}).get("macd_hist") or 0
    es_15m  = (tf_15m or {}).get("ema_short") or 0
    em_15m  = (tf_15m or {}).get("ema_mid") or 0

    HTF_RSI_OB   = S["SET3_HTF_RSI_OB"]     # 72
    HTF_RSI_OS   = S["SET3_HTF_RSI_OS"]     # 28
    HTF_RSI_1H_S = S["SET3_HTF_RSI_1H_SLOW"]   # 68 (short)
    HTF_RSI_1H_L = S["SET3_HTF_RSI_1H_SLOW_L"] # 32 (long)
    HTF_BB_OB    = S["SET3_HTF_BB_OB"]      # 0.90
    HTF_BB_OS    = S["SET3_HTF_BB_OS"]      # 0.10
    HTF_MARGIN   = S["SET3_HTF_MARGIN_MIN"] # 2

    # ── SHORT exhaustion ──────────────────────────────────
    short_htf_score = 0
    short_htf_reasons = []

    if rsi_4h > HTF_RSI_OB:
        short_htf_score += 3
        short_htf_reasons.append(f"4h RSI={rsi_4h:.1f} overbought (>{HTF_RSI_OB}) (+3) ★")
    if bb_4h > HTF_BB_OB:
        short_htf_score += 2
        short_htf_reasons.append(f"4h BB%={bb_4h:.2f} ชิดขอบบน (>{HTF_BB_OB}) (+2)")
    if rsi_1h > HTF_RSI_1H_S:
        short_htf_score += 2
        short_htf_reasons.append(f"1h RSI={rsi_1h:.1f} elevated (>{HTF_RSI_1H_S}) (+2)")
    # 15m early weakening signs
    if mh_15m < 0 and rsi_4h > HTF_RSI_OB:
        short_htf_score += 1
        short_htf_reasons.append(f"15m MACD={mh_15m:.2f} negative = early divergence (+1)")
    if rsi_15m < 40 and rsi_4h > HTF_RSI_OB:
        short_htf_score += 1
        short_htf_reasons.append(f"15m RSI={rsi_15m:.1f} weakening while 4h OB (+1)")
    if mh_4h < 0:
        short_htf_score += 1
        short_htf_reasons.append(f"4h MACD={mh_4h:.2f} turning negative (+1)")

    # ── LONG exhaustion (oversold recovery) ───────────────
    long_htf_score = 0
    long_htf_reasons = []

    if rsi_4h < HTF_RSI_OS:
        long_htf_score += 3
        long_htf_reasons.append(f"4h RSI={rsi_4h:.1f} oversold (<{HTF_RSI_OS}) (+3) ★")
    if bb_4h < HTF_BB_OS:
        long_htf_score += 2
        long_htf_reasons.append(f"4h BB%={bb_4h:.2f} ชิดขอบล่าง (<{HTF_BB_OS}) (+2)")
    if rsi_1h < HTF_RSI_1H_L:
        long_htf_score += 2
        long_htf_reasons.append(f"1h RSI={rsi_1h:.1f} depressed (<{HTF_RSI_1H_L}) (+2)")
    if mh_15m > 0 and rsi_4h < HTF_RSI_OS:
        long_htf_score += 1
        long_htf_reasons.append(f"15m MACD={mh_15m:.2f} positive = early recovery (+1)")
    if rsi_15m > 60 and rsi_4h < HTF_RSI_OS:
        long_htf_score += 1
        long_htf_reasons.append(f"15m RSI={rsi_15m:.1f} recovering while 4h OS (+1)")
    if mh_4h > 0:
        long_htf_score += 1
        long_htf_reasons.append(f"4h MACD={mh_4h:.2f} turning positive (+1)")

    # Build htf_data ให้ compatible กับ rb_data structure
    htf_data = {
        "bb_pct":        bb_4h,
        "rsi":           rsi_4h,
        "macd_hist":     mh_4h,
        "vol_ratio":     (tf_15m or {}).get("vol_ratio"),
        "swing_high":    (tf_4h.get("swings") or {}).get("swing_high"),
        "swing_low":     (tf_4h.get("swings") or {}).get("swing_low"),
        "long_score":    long_htf_score,
        "short_score":   short_htf_score,
        "htf_mode":      True,          # flag สำหรับ Claude + monitoring
        "rsi_4h":        rsi_4h,
        "rsi_1h":        rsi_1h,
        "bb_pct_4h":     bb_4h,
        "rsi_turning_up":   long_htf_score >= 3,
        "rsi_turning_down": short_htf_score >= 3,
        "funding_bonus": 0,
        "drop_bonus": 0,
    }

    # ── เลือก direction ────────────────────────────────────
    min_htf_score = 5  # ต้องการ 4h RSI extreme + อย่างน้อย 1 confirm อื่น
    htf_margin    = HTF_MARGIN

    if short_htf_score >= min_htf_score and short_htf_score > long_htf_score:
        margin = short_htf_score - long_htf_score
        if margin >= htf_margin:
            reasons = [f"REBOUND_HTF SHORT: {r}" for r in short_htf_reasons]
            htf_data["margin_used"] = margin
            return True, "Short", reasons, htf_data
        else:
            reasons.append(f"HTF SHORT margin={margin} < {htf_margin} — WEAK_HTF_MARGIN_BLOCK")
            return False, "N/A", reasons, htf_data

    if long_htf_score >= min_htf_score and long_htf_score > short_htf_score:
        margin = long_htf_score - short_htf_score
        if margin >= htf_margin:
            reasons = [f"REBOUND_HTF LONG: {r}" for r in long_htf_reasons]
            htf_data["margin_used"] = margin
            return True, "Long", reasons, htf_data
        else:
            reasons.append(f"HTF LONG margin={margin} < {htf_margin} — WEAK_HTF_MARGIN_BLOCK")
            return False, "N/A", reasons, htf_data

    reasons.append(
        f"HTF exhaustion ไม่ผ่าน: short={short_htf_score} long={long_htf_score} "
        f"(ต้องการ>={min_htf_score} + margin>={htf_margin})"
    )
    return False, "N/A", reasons, htf_data

# ═══════════════════════════════════════════════════════════
# REBOUND 30m SCORING (unchanged from SET2)
# ═══════════════════════════════════════════════════════════
def check_rebound_30m(tf_30m, tf_1h, price, change_24h=0,
                      funding_rate=None, rsi_series=None, tf_5m=None, tf_4h=None, tf_15m=None):
    """SET2 Rebound Hunt — unchanged. margin>=4 ยังคงเดิม."""
    S       = INDICATOR_SET
    reasons = []
    rb_data = {}

    if not tf_30m:
        return False, "N/A", ["ไม่มีข้อมูล 30m"], {}

    bbp   = tf_30m.get("bb_pct")
    rsi   = tf_30m.get("rsi")
    mh    = tf_30m.get("macd_hist")
    vr    = tf_30m.get("vol_ratio")
    sw    = tf_30m.get("swings") or {}
    sh    = sw.get("swing_high"); sl_val = sw.get("swing_low")
    bbu   = tf_30m.get("bb_upper"); bbl = tf_30m.get("bb_lower")
    buf   = cfg.REBOUND_SWING_BUFFER

    rsi_1h = tf_1h.get("rsi") if tf_1h else None
    if rsi_1h:
        guard = cfg.REBOUND_1H_RSI_GUARD
        if rsi_1h > 75 or rsi_1h < guard:
            reasons.append(f"1h RSI extreme ({rsi_1h:.1f}) — งด Rebound")
            return False, "N/A", reasons, {}

    rsi_4h = tf_4h.get("rsi") if tf_4h else None
    block_short_4h = rsi_4h and rsi_4h < 35
    block_long_4h  = rsi_4h and rsi_4h > 65

    rsi_turning_up   = False
    rsi_turning_down = False
    if rsi_series and len(rsi_series) >= 3:
        r_now  = rsi_series[-1]
        r_prev = rsi_series[-3]
        if r_now and r_prev:
            if r_now > r_prev + 1.5 and r_now < 52:
                rsi_turning_up = True
            if r_now < r_prev - 1.5 and r_now > 48:
                rsi_turning_down = True

    funding_bonus = 0
    if funding_rate is not None:
        if funding_rate < cfg.FUNDING_BONUS_HIGH:
            funding_bonus = 2
        elif funding_rate < cfg.FUNDING_BONUS_MED:
            funding_bonus = 1

    drop_bonus = 0
    if change_24h < cfg.DROP_BONUS_HIGH:
        drop_bonus = 2
    elif change_24h < cfg.DROP_BONUS_MED:
        drop_bonus = 1

    micro_long_ok  = False
    micro_short_ok = False
    if tf_5m:
        rsi_5m = tf_5m.get("rsi", 50)
        mh_5m  = tf_5m.get("macd_hist", 0) or 0
        if rsi_5m and rsi_5m > 20 and mh_5m > -0.001:
            micro_long_ok = True
        if rsi_5m and rsi_5m < 80 and mh_5m < 0.001:
            micro_short_ok = True

    RSI_TURN_W = cfg.REBOUND_RSI_TURN_WEIGHT

    long_score = 0; long_reasons = []
    if bbp is not None and bbp < cfg.REBOUND_BB_LOW:
        long_score += 2; long_reasons.append(f"BB%B={bbp:.2f} ใกล้ lower band (+2)")
    if rsi and rsi < cfg.RSI_OVERSOLD:
        long_score += 2; long_reasons.append(f"RSI={rsi:.1f} Oversold (+2)")
    if sl_val and abs(price - sl_val) / sl_val < buf:
        long_score += 2; long_reasons.append(f"ใกล้ Swing Low ${sl_val:.2f} (+2)")
    if mh is not None and mh > -0.0001:
        long_score += 1; long_reasons.append(f"MACD hist={mh:.4f} turning up (+1)")
    if vr and vr > cfg.REBOUND_VOL_MIN:
        long_score += 1; long_reasons.append(f"Volume={vr:.1f}x avg (+1)")
    if rsi_turning_up:
        long_score += RSI_TURN_W; long_reasons.append(f"RSI momentum turning up (+{RSI_TURN_W}) ★")
    if funding_bonus > 0:
        long_score += funding_bonus; long_reasons.append(f"Funding={funding_rate:+.4f}% → squeeze risk (+{funding_bonus})")
    if drop_bonus > 0:
        long_score += drop_bonus; long_reasons.append(f"24h drop={change_24h:.1f}% → flush (+{drop_bonus})")
    if micro_long_ok:
        long_score += 1; long_reasons.append(f"5m micro entry OK (+1)")

    short_score = 0; short_reasons = []
    if bbp is not None and bbp > cfg.REBOUND_BB_HIGH:
        short_score += 2; short_reasons.append(f"BB%B={bbp:.2f} ใกล้ upper band (+2)")
    if rsi and rsi > cfg.RSI_OVERBOUGHT:
        short_score += 2; short_reasons.append(f"RSI={rsi:.1f} Overbought (+2)")
    if sh and abs(price - sh) / sh < buf:
        short_score += 2; short_reasons.append(f"ใกล้ Swing High ${sh:.2f} (+2)")
    if mh is not None and mh < 0.0001:
        short_score += 1; short_reasons.append(f"MACD hist={mh:.4f} turning down (+1)")
    if vr and vr > cfg.REBOUND_VOL_MIN:
        short_score += 1; short_reasons.append(f"Volume={vr:.1f}x avg (+1)")
    if rsi_turning_down:
        short_score += RSI_TURN_W; short_reasons.append(f"RSI momentum turning down (+{RSI_TURN_W}) ★")
    if funding_rate is not None and funding_rate > 0.03:
        short_score += 2; short_reasons.append(f"Funding={funding_rate:+.4f}% → long squeeze risk (+2)")
    elif funding_rate is not None and funding_rate > 0.01:
        short_score += 1; short_reasons.append(f"Funding={funding_rate:+.4f}% → longs dominant (+1)")
    if change_24h > 5.0:
        short_score += 2; short_reasons.append(f"24h rise={change_24h:.1f}% → overextended (+2)")
    elif change_24h > 3.0:
        short_score += 1; short_reasons.append(f"24h rise={change_24h:.1f}% → possible fade (+1)")
    if micro_short_ok:
        short_score += 1; short_reasons.append(f"5m micro entry OK (+1)")

    rb_data = {
        "bb_pct": bbp, "rsi": rsi, "macd_hist": mh, "vol_ratio": vr,
        "swing_high": sh, "swing_low": sl_val,
        "long_score": long_score, "short_score": short_score,
        "bb_upper": bbu, "bb_lower": bbl,
        "funding_rate": funding_rate,
        "rsi_turning_up": rsi_turning_up,
        "rsi_turning_down": rsi_turning_down,
        "funding_bonus": funding_bonus,
        "drop_bonus": drop_bonus,
        "htf_mode": False,
    }

    min_score = cfg.REBOUND_MIN_SCORE

    if long_score >= min_score and long_score > short_score:
        if block_long_4h:
            reasons.append(f"⚠️ 4h RSI={rsi_4h:.1f} overbought — งด LONG rebound")
            return False, "N/A", reasons, rb_data
        margin = long_score - short_score
        if margin < cfg.REBOUND_MIN_MARGIN:
            reasons.append(
                f"LONG margin={margin} < {cfg.REBOUND_MIN_MARGIN} (long={long_score}, short={short_score})"
                f" — WEAK_MARGIN_BLOCK"
            )
            return False, "N/A", reasons, rb_data
        reasons = [f"REBOUND LONG: {r}" for r in long_reasons]
        return True, "Long", reasons, rb_data

    if short_score >= min_score and short_score > long_score:
        if block_short_4h:
            reasons.append(f"⚠️ 4h RSI={rsi_4h:.1f} oversold — งด SHORT rebound")
            return False, "N/A", reasons, rb_data
        margin = short_score - long_score
        if margin < cfg.REBOUND_MIN_MARGIN:
            reasons.append(
                f"SHORT margin={margin} < {cfg.REBOUND_MIN_MARGIN} (short={short_score}, long={long_score})"
                f" — WEAK_MARGIN_BLOCK"
            )
            return False, "N/A", reasons, rb_data
        reasons = [f"REBOUND SHORT: {r}" for r in short_reasons]
        return True, "Short", reasons, rb_data

    reasons.append(
        f"Rebound score ไม่ผ่าน: Long={long_score} Short={short_score} "
        f"(ต้องการ>={min_score} และ margin>={cfg.REBOUND_MIN_MARGIN})"
    )
    return False, "N/A", reasons, rb_data

def compute_pre_conf(long_score, short_score, direction):
    best = long_score if direction == "Long" else short_score if direction == "Short" \
           else max(long_score, short_score)
    return min(round(best / cfg.REBOUND_MAX_SCORE * 100), 95)

def get_persistence_bonus(symbol, direction, logs, window=3):
    recent = [s for s in logs[:window*3]
              if s.get("symbol") == symbol
              and s.get("direction") == direction
              and s.get("strategy_used") in ("REBOUND", "REBOUND_HTF")
              and s.get("verdict") in ("WEAK SIGNAL", "FILTERED")]
    if len(recent) >= window:
        return cfg.PERSISTENCE_BONUS
    return 0

# ═══════════════════════════════════════════════════════════
# MARKET CONTEXT (unchanged from SET2)
# ═══════════════════════════════════════════════════════════
def fetch_market_context(symbol):
    ctx = {}
    try:
        ls_data = http_get(
            f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
            f"?symbol={symbol}&period=5m&limit=2", timeout=8)
        if ls_data and isinstance(ls_data, list) and len(ls_data) >= 1:
            latest = ls_data[-1]
            ls_ratio  = float(latest.get("longShortRatio", 1.0))
            long_pct  = float(latest.get("longAccount",   0.5)) * 100
            short_pct = 100 - long_pct
            ctx.update({"ls_ratio": round(ls_ratio, 4),
                        "ls_long_pct": round(long_pct, 2),
                        "ls_short_pct": round(short_pct, 2)})
    except Exception as e:
        log(f"  ⚠️ market_ctx L/S fetch failed: {e}")
    try:
        tk_data = http_get(
            f"https://fapi.binance.com/futures/data/takerlongshortRatio"
            f"?symbol={symbol}&period=5m&limit=3", timeout=8)
        if tk_data and isinstance(tk_data, list) and len(tk_data) >= 1:
            buy_pcts  = [float(d.get("buySellRatio", 0.5)) for d in tk_data]
            buy_ratio = sum(buy_pcts) / len(buy_pcts)
            buy_pct   = buy_ratio / (1 + buy_ratio) * 100
            sell_pct  = 100 - buy_pct
            ctx.update({"taker_buy_pct": round(buy_pct, 2),
                        "taker_sell_pct": round(sell_pct, 2),
                        "taker_ratio": round(buy_ratio, 4)})
    except Exception as e:
        log(f"  ⚠️ market_ctx Taker fetch failed: {e}")
    try:
        oi_data = http_get(
            f"https://fapi.binance.com/futures/data/openInterestHist"
            f"?symbol={symbol}&period=5m&limit=6", timeout=8)
        if oi_data and isinstance(oi_data, list) and len(oi_data) >= 2:
            oi_now = float(oi_data[-1].get("sumOpenInterest", 0))
            oi_old = float(oi_data[0].get("sumOpenInterest",  0))
            oi_chg = ((oi_now - oi_old) / oi_old * 100) if oi_old > 0 else 0
            ctx.update({"oi_now": round(oi_now, 2), "oi_change_pct": round(oi_chg, 4)})
    except Exception as e:
        log(f"  ⚠️ market_ctx OI fetch failed: {e}")
    if cfg.CTX_LOG_UNAVAILABLE and not ctx:
        log(f"  ⚠️ market_ctx: all endpoints failed — scoring without context")
    return ctx

def calc_market_context_bonus(ctx, direction):
    if not ctx:
        return 0, ["market_ctx unavailable — no bonus applied"]
    S       = INDICATOR_SET
    bonus   = 0
    reasons = []
    dir_up  = direction.upper()
    ls    = ctx.get("ls_ratio")
    t_buy = ctx.get("taker_buy_pct")
    t_sell= ctx.get("taker_sell_pct")
    oi_ch = ctx.get("oi_change_pct")
    if ls is not None:
        if dir_up == "LONG":
            if ls < S["CTX_LS_SHORTS_EXTREME"]:
                raw = +3
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += raw; reasons.append(f"L/S={ls:.3f} shorts extreme → squeeze +3")
                else:
                    reasons.append(f"L/S={ls:.3f} shorts extreme (positive bonus Long deferred)")
            elif ls < S["CTX_LS_SHORTS_HIGH"]:
                raw = +2
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += raw; reasons.append(f"L/S={ls:.3f} shorts high → bounce +2")
                else:
                    reasons.append(f"L/S={ls:.3f} shorts high (positive bonus Long deferred)")
            elif ls > S["CTX_LS_LONGS_EXTREME"]:
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus -= 3; reasons.append(f"L/S={ls:.3f} longs extreme → liquidation risk -3")
            elif ls > S["CTX_LS_LONGS_HIGH"]:
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus -= 2; reasons.append(f"L/S={ls:.3f} longs crowded → weak long -2")
        elif dir_up == "SHORT":
            if ls > S["CTX_LS_LONGS_EXTREME"]:
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += 3; reasons.append(f"L/S={ls:.3f} longs extreme → short squeeze +3")
            elif ls > S["CTX_LS_LONGS_HIGH"]:
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += 2; reasons.append(f"L/S={ls:.3f} longs high → short bounce +2")
            elif ls < S["CTX_LS_SHORTS_EXTREME"]:
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus -= 2; reasons.append(f"L/S={ls:.3f} shorts extreme → risky short -2")
            elif ls < S["CTX_LS_SHORTS_HIGH"]:
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus -= 1; reasons.append(f"L/S={ls:.3f} shorts crowded → weak short -1")
    if t_buy is not None and t_sell is not None:
        if dir_up == "LONG":
            if t_buy >= S["CTX_TAKER_AGGRESSIVE"]:
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += 2; reasons.append(f"Taker buy={t_buy:.1f}% aggressive → Long +2")
                else:
                    reasons.append(f"Taker buy={t_buy:.1f}% aggressive (positive Long deferred)")
            elif t_buy >= S["CTX_TAKER_MODERATE"]:
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += 1; reasons.append(f"Taker buy={t_buy:.1f}% moderate → Long +1")
                else:
                    reasons.append(f"Taker buy={t_buy:.1f}% moderate (positive Long deferred)")
            elif t_sell >= S["CTX_TAKER_AGGRESSIVE"]:
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus -= 2; reasons.append(f"Taker sell={t_sell:.1f}% aggressive → Long -2")
            elif t_sell >= S["CTX_TAKER_MODERATE"]:
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus -= 1; reasons.append(f"Taker sell={t_sell:.1f}% moderate → Long -1")
        elif dir_up == "SHORT":
            if t_sell >= S["CTX_TAKER_AGGRESSIVE"]:
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += 2; reasons.append(f"Taker sell={t_sell:.1f}% aggressive → Short +2")
            elif t_sell >= S["CTX_TAKER_MODERATE"]:
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += 1; reasons.append(f"Taker sell={t_sell:.1f}% moderate → Short +1")
            elif t_buy >= S["CTX_TAKER_AGGRESSIVE"]:
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus -= 2; reasons.append(f"Taker buy={t_buy:.1f}% aggressive → Short -2")
            elif t_buy >= S["CTX_TAKER_MODERATE"]:
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus -= 1; reasons.append(f"Taker buy={t_buy:.1f}% moderate → Short -1")
    if oi_ch is not None:
        if oi_ch < S["CTX_OI_DROP_STRONG"]:
            bonus -= 2; reasons.append(f"OI drop={oi_ch:.2f}% strong close-out → momentum weak -2")
        elif oi_ch < S["CTX_OI_DROP_MILD"]:
            bonus -= 1; reasons.append(f"OI drop={oi_ch:.2f}% mild close-out → caution -1")
        elif oi_ch > 1.0:
            reasons.append(f"OI rise={oi_ch:.2f}% — direction unknown, neutral")
    bonus = max(-S["CTX_BONUS_MAX"], min(S["CTX_BONUS_MAX"], bonus))
    if not reasons:
        reasons.append("market_ctx neutral — no bonus")
    return bonus, reasons

def market_context_alignment_score(ctx, direction):
    if not ctx:
        return 0, 0
    ls    = ctx.get("ls_ratio")
    t_buy = ctx.get("taker_buy_pct")
    t_sell= ctx.get("taker_sell_pct")
    dir_up= direction.upper()
    support = 0; oppose = 0
    if ls is not None:
        if dir_up == "LONG":
            if ls < 0.90: support += 1
            if ls > 1.20: oppose  += 1
        elif dir_up == "SHORT":
            if ls > 1.10: support += 1
            if ls < 0.80: oppose  += 1
    if t_buy is not None and t_sell is not None:
        if dir_up == "LONG":
            if t_buy  >= 53: support += 1
            if t_buy  <= 45: oppose  += 1
        elif dir_up == "SHORT":
            if t_sell >= 53: support += 1
            if t_buy  >= 55: oppose  += 1
    return support, oppose

def is_market_context_aligned(ctx, direction):
    support, oppose = market_context_alignment_score(ctx, direction)
    return support >= 1 and oppose == 0

def fetch_market(symbol):
    ticker = None
    for attempt in range(3):
        ticker = http_get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}", timeout=20)
        if ticker and isinstance(ticker, dict) and "lastPrice" in ticker:
            break
        log(f"⚠️ ticker attempt {attempt+1}/3: {symbol}")
        time.sleep(1.5)

    if not ticker or "lastPrice" not in ticker:
        log(f"❌ fetch_market: ticker failed: {symbol}")
        return None

    price  = float(ticker["lastPrice"])
    high   = float(ticker["highPrice"])
    low    = float(ticker["lowPrice"])
    change = float(ticker["priceChangePercent"])
    volume = float(ticker["quoteVolume"])
    atr    = (high - low) / 14

    k5  = get_klines(symbol, "5m",  100)
    k15 = get_klines(symbol, "15m", 200)
    k30 = get_klines(symbol, "30m", 150)
    k1h = get_klines(symbol, "1h",  150)
    k2h = get_klines(symbol, "2h",  150)
    k4h = get_klines(symbol, "4h",  150)

    tf_5m  = calc_tf_data(k5)  if len(k5)  >= 30 else None
    tf_15m = calc_tf_data(k15) if len(k15) >= 30 else None
    tf_30m = calc_tf_data(k30) if len(k30) >= 30 else None
    tf_1h  = calc_tf_data(k1h) if len(k1h) >= 30 else None
    tf_2h  = calc_tf_data(k2h) if len(k2h) >= 30 else None
    tf_4h  = calc_tf_data(k4h) if len(k4h) >= 30 else None

    entry_tf_used = "15m"
    if not tf_15m and tf_30m:
        tf_15m = tf_30m
        entry_tf_used = "30m (fallback)"
    if not tf_15m:
        log(f"❌ fetch_market: both 15m+30m failed: {symbol}")
        return None

    funding_rate = None
    try:
        fd = http_get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}", timeout=8)
        if fd and "lastFundingRate" in fd:
            funding_rate = round(float(fd["lastFundingRate"]) * 100, 4)
    except:
        pass

    rsi_series_30m = None
    if len(k30) >= 35:
        closes = [float(k[4]) for k in k30]
        try:
            rsi_series_30m = [calc_rsi(closes[:i+1]) for i in range(len(closes)-3, len(closes))]
        except:
            pass

    log(f"✅ fetch_market: {symbol} ${price:,.2f} | 5m={bool(tf_5m)} 1h={bool(tf_1h)} 2h={bool(tf_2h)} 4h={bool(tf_4h)} | funding={funding_rate}%")

    market_ctx = fetch_market_context(symbol)
    if market_ctx:
        log(f"  📡 market_ctx: L/S={market_ctx.get('ls_ratio','?')} "
            f"taker_buy={market_ctx.get('taker_buy_pct','?')}% "
            f"OI_chg={market_ctx.get('oi_change_pct','?')}%")

    return {
        "symbol": symbol, "price": price,
        "high": high, "low": low, "change": change,
        "volume": volume, "atr": atr,
        "tf_5m": tf_5m, "tf_15m": tf_15m, "tf_30m": tf_30m,
        "tf_1h": tf_1h, "tf_2h": tf_2h, "tf_4h": tf_4h,
        "entry_tf":       entry_tf_used,
        "funding_rate":   funding_rate,
        "rsi_series_30m": rsi_series_30m,
        "market_ctx":     market_ctx,
    }

# ═══════════════════════════════════════════════════════════
# CLAUDE SYSTEM PROMPT — SET3 (updated)
# ═══════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are a compact crypto futures signal validator (SET3v1).

Bot has filtered and scored signals. Your job: validate gray-zone setups ONLY.

RULES:
- EMA 9/21/200 | Binance Futures | 15m entry, 1h+4h confirmation
- Min R:R 1.5 | Hard SL risk <= 8% | Reject if > 12%
- VOLATILE regime → always NO_TRADE
- For REBOUND LONG: accept bearish 4h MACD if rsi_turn_up=true (counter-trend rebound is valid)
- For REBOUND SHORT: require 4h bearish + rsi_turn_down=true + short_score>=7
- For REBOUND_HTF SHORT: 4h RSI>72 is the primary signal — EMA alignment AGAINST direction is EXPECTED
  Accept if: 4h RSI>72 + short_htf_score>=5 + margin>=2 (counter-trend entry at exhaustion point)
- For REBOUND_HTF LONG: 4h RSI<28 is the primary signal — same logic reversed
- For TREND_FOLLOW: confirm HTF alignment before entry
- Exhaustion zone: 4h RSI>72 → LONG entries face mean-reversion risk, raise caution
- 2h timeframe is fallback only when 4h bias is neutral; never let 2h override clear 4h bias
- Countertrend without rejection confirmation → REJECTED

MARKET CONTEXT (supporting evidence only):
- ctx.ls_ratio < 0.85 → shorts overcrowded → bounce potential supports Long
- ctx.ls_ratio > 1.15 → longs overcrowded → squeeze potential supports Short
- ctx.taker_buy_pct > 55 → aggressive buyers → favors Long momentum
- ctx.taker_sell_pct > 55 → aggressive sellers → favors Short momentum
- ctx.oi_change_pct < -1.0 → positions closing → momentum weakening, raise caution
- ctx_bonus shown in payload = net microstructure score already applied to pre_conf

VOLUME WARNING: if vol_ratio_15m < 0.10 AND vol_data_missing=false, flag as liquidity_risk.
If vol_data_missing=true, ignore vol_ratio for this check.

Respond ONLY with valid JSON, no markdown, no preamble:
{
  "verdict": "APPROVED|WEAK_APPROVAL|REJECTED|NO_TRADE",
  "confidence": 0-100,
  "reason_code": "TREND_ALIGNED|MOMENTUM_CONFIRM|STRUCTURE_VALID|RR_OK|HTF_EXHAUSTION_SHORT|HTF_EXHAUSTION_LONG|COUNTERTREND|WEAK_MOMENTUM|INVALID_STRUCTURE|RR_FAIL|HTF_CONFLICT|VOLATILE_REGIME|FUNDING_RISK|CTX_CONFLICT|INSUFFICIENT_DATA",
  "ssl": "price_or_null",
  "hsl": "price_or_null",
  "tp1": "price_or_null",
  "tp2": "price_or_null",
  "tp3": "price_or_null",
  "notes": ["max 3 bullets"],
  "risk_flags": []
}"""

def call_claude(symbol, direction, strategy, summary):
    if not cfg.ANTHROPIC_API_KEY or "YOUR" in cfg.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    prompt = f"Validate {symbol} {direction} signal. Strategy: {strategy}. Data: {summary}"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": cfg.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": cfg.CLAUDE_MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}]
    }
    last_error = None
    for attempt, delay in enumerate((0, 2, 5), start=1):
        if delay: time.sleep(delay)
        result = http_post("https://api.anthropic.com/v1/messages", headers, body)
        if not result:
            last_error = RuntimeError(f"Claude transport error (attempt {attempt}/3)"); continue
        if "error" in result:
            msg  = result["error"].get("message") or "Unknown Claude error"
            last_error = RuntimeError(f"Claude: {msg}")
            low  = msg.lower()
            if any(x in low for x in ("rate limit","overloaded","timeout","tempor","try again","529","502","503","504")):
                if attempt < 3: continue
            raise last_error
        try:
            return result["content"][0]["text"]
        except Exception:
            last_error = RuntimeError(f"Claude malformed response (attempt {attempt}/3)")
    raise last_error or RuntimeError("No response from Claude")

def build_summary(m, regime, strategy, tf_bk, rb_data=None, direction=None):
    t15 = m["tf_15m"] or {}
    t1h = m["tf_1h"]  or {}
    t2h = m.get("tf_2h") or {}
    t4h = m["tf_4h"]  or {}

    def r2(v): return round(v, 2) if v is not None else None

    # [SET3] vol_data_missing flag for Claude
    t15_vr = t15.get("vol_ratio")
    vol_data_missing = (t15_vr is not None and t15_vr == 0.0)

    payload = {
        "symbol":    m["symbol"],
        "direction": direction or "N/A",
        "price":     r2(m["price"]),
        "change_24h":r2(m.get("change", 0)),
        "regime":    regime,
        "strategy":  strategy,
        "funding":   m.get("funding_rate"),
        "bot":       "SET3v1",
        "vol_data_missing": vol_data_missing,
        "tf": {
            "4h": {
                "ema9":  r2(t4h.get("ema_short")), "ema21": r2(t4h.get("ema_mid")),
                "rsi":   r2(t4h.get("rsi")),       "macd_h": r2(t4h.get("macd_hist")),
                "bb_pct":r2(t4h.get("bb_pct")),
            },
            "1h": {
                "ema9":  r2(t1h.get("ema_short")), "ema21": r2(t1h.get("ema_mid")),
                "rsi":   r2(t1h.get("rsi")),       "macd_h": r2(t1h.get("macd_hist")),
            },
            "2h": {
                "ema9":  r2(t2h.get("ema_short")), "ema21": r2(t2h.get("ema_mid")),
                "rsi":   r2(t2h.get("rsi")),       "macd_h": r2(t2h.get("macd_hist")),
            },
            "15m": {
                "ema9":  r2(t15.get("ema_short")), "ema21": r2(t15.get("ema_mid")),
                "ema200":r2(t15.get("ema_long")),  "rsi":   r2(t15.get("rsi")),
                "macd_h":r2(t15.get("macd_hist")), "bb_pct":r2(t15.get("bb_pct")),
                "vol_ratio": r2(t15_vr),
            },
        },
    }
    if strategy in ("REBOUND", "REBOUND_HTF") and rb_data:
        htf_mode = rb_data.get("htf_mode", False)
        payload["rebound"] = {
            "bb_pct":         rb_data.get("bb_pct"),
            "rsi_30m":        rb_data.get("rsi"),
            "macd_h_30m":     rb_data.get("macd_hist"),
            "vol_ratio":      rb_data.get("vol_ratio"),
            "long_score":     rb_data.get("long_score"),
            "short_score":    rb_data.get("short_score"),
            "max_score":      cfg.REBOUND_MAX_SCORE,
            "rsi_turn_up":    rb_data.get("rsi_turning_up", False),
            "rsi_turn_dn":    rb_data.get("rsi_turning_down", False),
            "swing_high":     r2(rb_data.get("swing_high")),
            "swing_low":      r2(rb_data.get("swing_low")),
            "htf_mode":       htf_mode,
            # HTF-specific fields
            "rsi_4h":         r2(rb_data.get("rsi_4h")),
            "rsi_1h":         r2(rb_data.get("rsi_1h")),
            "bb_pct_4h":      r2(rb_data.get("bb_pct_4h")),
            "margin":         rb_data.get("long_score",0) - rb_data.get("short_score",0)
                              if direction == "Long"
                              else rb_data.get("short_score",0) - rb_data.get("long_score",0),
        }
    if tf_bk:
        payload["tf_breakdown"] = tf_bk
    ctx = m.get("market_ctx") or {}
    if ctx:
        payload["ctx"] = {
            "ls_ratio":       ctx.get("ls_ratio"),
            "taker_buy_pct":  ctx.get("taker_buy_pct"),
            "taker_sell_pct": ctx.get("taker_sell_pct"),
            "oi_change_pct":  ctx.get("oi_change_pct"),
            "ctx_bonus":      m.get("ctx_bonus", 0),
            "ctx_aligned":    m.get("ctx_aligned", False),
            "ctx_reasons":    (m.get("ctx_reasons") or [])[:3],
        }
    if m.get("pre_conf") is not None:
        payload["pre_conf"] = m.get("pre_conf")
    if m.get("effective_claude_min") is not None:
        payload["effective_claude_min"] = m.get("effective_claude_min")
    if t15_vr is not None and not vol_data_missing and t15_vr < cfg.CTX_VOL_MIN_FOR_SIGNAL:
        payload["vol_warning"] = f"vol_ratio_15m={t15_vr:.3f} CRITICALLY_LOW"
    return json.dumps(payload, separators=(",", ":"))

def calc_fallback_levels(price, direction, atr=None):
    if direction.upper() == "LONG":
        ssl = round(price * 0.985, 2); hsl = round(price * 0.980, 2)
        tp1 = round(price * 1.015, 2); tp2 = round(price * 1.025, 2); tp3 = round(price * 1.040, 2)
    else:
        ssl = round(price * 1.015, 2); hsl = round(price * 1.020, 2)
        tp1 = round(price * 0.985, 2); tp2 = round(price * 0.975, 2); tp3 = round(price * 0.960, 2)
    return {"ssl": str(ssl), "hsl": str(hsl), "tp1": str(tp1), "tp2": str(tp2), "tp3": str(tp3)}

def normalize_trade_levels(entry, direction, levels):
    def sfloat(v):
        try: return float(str(v).replace(",", ""))
        except: return None
    entry_f   = sfloat(entry)
    direction = str(direction or "").upper()
    if not entry_f or direction not in ("LONG", "SHORT"):
        return levels
    fallback = calc_fallback_levels(entry_f, direction)
    def fix(key, fallback_key, condition_fn):
        v = sfloat(levels.get(key) or levels.get("suggested_"+key))
        return v if v and condition_fn(v) else sfloat(fallback[fallback_key])
    norm = dict(levels)
    norm["entry"] = str(round(entry_f, 2))
    if direction == "LONG":
        ssl = fix("ssl","ssl", lambda v: v < entry_f)
        hsl = fix("hsl","hsl", lambda v: v < entry_f)
        tp1 = fix("tp1","tp1", lambda v: v > entry_f)
        tp2 = fix("tp2","tp2", lambda v: v > entry_f)
        tp3 = fix("tp3","tp3", lambda v: v > entry_f)
    else:
        ssl = fix("ssl","ssl", lambda v: v > entry_f)
        hsl = fix("hsl","hsl", lambda v: v > entry_f)
        tp1 = fix("tp1","tp1", lambda v: v < entry_f)
        tp2 = fix("tp2","tp2", lambda v: v < entry_f)
        tp3 = fix("tp3","tp3", lambda v: v < entry_f)
    for k, v in [("ssl",ssl),("hsl",hsl),("tp1",tp1),("tp2",tp2),("tp3",tp3)]:
        norm[k] = str(round(v, 2)) if v else norm.get(k)
    return norm

def parse_ai(text):
    try:
        clean = text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"```[a-z]*\n?", "", clean).strip()
        start = clean.find("{"); end = clean.rfind("}") + 1
        if start >= 0 and end > start:
            obj = json.loads(clean[start:end])
            verdict    = str(obj.get("verdict", "REJECTED")).upper().replace("_", " ")
            verdict_map = {
                "WEAK APPROVAL": "WEAK APPROVAL", "WEAK_APPROVAL": "WEAK APPROVAL",
                "APPROVED": "APPROVED", "REJECTED": "REJECTED",
                "NO TRADE": "NO TRADE", "NO_TRADE": "NO TRADE",
            }
            verdict = verdict_map.get(verdict, "REJECTED")
            conf    = int(obj.get("confidence", 0) or 0)
            levels  = {
                "ssl": str(obj.get("ssl") or ""), "hsl": str(obj.get("hsl") or ""),
                "tp1": str(obj.get("tp1") or ""), "tp2": str(obj.get("tp2") or ""),
                "tp3": str(obj.get("tp3") or ""),
                "reason_code": obj.get("reason_code", ""),
                "risk_flags":  obj.get("risk_flags", []),
                "reason": " | ".join(obj.get("notes", [])) if obj.get("notes") else "",
            }
            return verdict, conf, levels
    except Exception as e:
        log(f"⚠️ parse_ai JSON failed: {e}")
    verdict = "REJECTED"
    for v in ["APPROVED", "WEAK APPROVAL", "NO TRADE", "REJECTED"]:
        if v in text.upper(): verdict = v; break
    conf_m = re.search(r'"confidence"[:\s]+(\d+)', text)
    conf   = int(conf_m.group(1)) if conf_m else 0
    return verdict, conf, {}

def load_log():
    log_file = getattr(cfg, "LOG_FILE", "/home/crypto_bot/signal_log.json")
    try:
        if os.path.exists(log_file):
            with open(log_file) as f:
                return json.load(f)
    except:
        pass
    return []

def save_log(logs):
    log_file = getattr(cfg, "LOG_FILE", "/home/crypto_bot/signal_log.json")
    try:
        with open(log_file, "w") as f:
            json.dump(logs[:5000], f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log(f"❌ save_log: {e}")

def fmt(v):
    try: return f"${float(v):,.2f}"
    except: return str(v or "-")

def _tg_rsi_line(ind):
    t15 = ind.get("15m", {}); t1h = ind.get("1h", {}); t4h = ind.get("4h", {})
    r15 = t15.get("rsi", "—"); r1h = t1h.get("rsi", "—"); r4h = t4h.get("rsi", "—")
    return f"📊 RSI 15m:{r15} | 1h:{r1h} | 4h:{r4h}\n"

def _tg_send(token, chat_id, msg):
    params = urllib.parse.urlencode({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"})
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage?{params}"),
            timeout=10
        ) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        log(f"Telegram send error: {e}")
        return False

def _telegram_targets():
    targets = []

    def add(token, chat_id, label):
        token = (token or "").strip()
        chat_id = str(chat_id or "").strip()
        if not token or not chat_id or "YOUR" in token:
            return
        pair = (token, chat_id, label)
        if pair not in targets:
            targets.append(pair)

    add(
        getattr(cfg, "TELEGRAM_TOKEN", None) or getattr(cfg, "TG_BOT_TOKEN", "") or os.getenv("TELEGRAM_TOKEN", ""),
        getattr(cfg, "TELEGRAM_CHAT_ID", None) or getattr(cfg, "TG_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", ""),
        "primary",
    )
    add(
        os.getenv("TELEGRAM_TOKEN2", "") or os.getenv("TELEGRAM_TOKEN", ""),
        os.getenv("TELEGRAM_CHAT2_ID", ""),
        "secondary",
    )
    return targets

def _tg_send_all(msg):
    targets = _telegram_targets()
    if not targets:
        log("  Telegram skipped: no configured targets")
        return False
    sent_any = False
    for token, chat_id, label in targets:
        ok = _tg_send(token, chat_id, msg)
        log(f"  Telegram {label}: {'sent' if ok else 'failed'}")
        sent_any = sent_any or ok
    return sent_any

def _fmt_regime(regime): return regime or "—"
def _tg_escape(value, limit=None):
    text = "" if value is None else str(value)
    if limit is not None: text = text[:limit]
    for ch in (chr(92), "_", "*", "`", "["): text = text.replace(ch, chr(92) + ch)
    return text
def _tg_pct_text(entry, target, direction):
    try:
        e = float(entry); t = float(target)
        if e == 0: return ""
        pct = ((t - e) / e * 100.0) if direction == "Long" else ((e - t) / e * 100.0)
        sign = "+" if pct > 0 else ""
        return f"({sign}{pct:.1f}%)"
    except: return ""
def _tg_level_line(icon, label, value, entry, direction, extra=""):
    rendered = fmt(value) if value is not None else "—"
    pct = _tg_pct_text(entry, value, direction) if value is not None else ""
    suffix = f" {extra}" if extra else ""
    details = " ".join([x for x in [pct, suffix.strip()] if x]).strip()
    return f"{icon} {label}: {rendered}" + (f" {details}" if details else "") + "\n"
def _tg_header(title, badge, subtitle=None):
    msg = f"🔷 *BOT Codex Trade*\n{title}\n🔹 *{badge}*\n"
    if subtitle: msg += f"_{subtitle}_\n"
    msg += "━━━━━━\n\n"
    return msg

def _bot_conf_value(sig):
    for key in ("t4_score", "pre_conf", "conf"):
        val = sig.get(key)
        if val is not None:
            try:
                return int(val)
            except Exception:
                pass
    return "—"
def _has_required_trade_fields(sig):
    return bool(sig.get("symbol") and sig.get("direction") and sig.get("entry")
                and sig.get("tp1") and (sig.get("hsl") or sig.get("ssl")))

def send_telegram(sig):
    if not _telegram_targets():
        log("  Telegram skipped: token/chat_id not configured"); return False
    verdict = sig.get("verdict", "")
    if not _has_required_trade_fields(sig):
        log(f"  Telegram blocked: missing trade levels for verdict={verdict}"); return False
    if verdict == "REJECTED":
        if (sig.get("conf") or 0) < 50: return False
        return _send_telegram_rejected(sig)
    if verdict not in ("APPROVED", "WEAK APPROVAL"):
        return False
    sym = sig["symbol"].replace("USDT", "")
    direction = sig["direction"]
    strat = sig.get("strategy_used", "—")
    title = ("🟦 LONG" if direction == "Long" else "🔵 SHORT") + f" {sym}/USDT | Bot Conf: {_bot_conf_value(sig)}/100"
    badge = "APPROVED SIGNAL" if verdict == "APPROVED" else "WEAK APPROVAL"
    subtitle = "trade ได้ แต่ควรลด size หรือรอ confirmation เพิ่ม" if verdict == "WEAK APPROVAL" else None
    ind = sig.get("indicators", {}); t15 = ind.get("15m", {})
    fr  = sig.get("funding_rate") or (sig.get("indicators") or {}).get("funding_rate")
    msg  = _tg_header(title, badge, subtitle)
    msg += f"🕐 {now_thai().strftime('%H:%M')} TH\n"
    msg += f"📍 {_tg_escape(_fmt_regime(sig.get('regime')))} | Strategy: {_tg_escape(strat)}\n"
    msg += f"🔀 Gate: {_tg_escape(sig.get('gate_path','—'))}\n\n"
    msg += _tg_level_line("💰", "Entry", sig.get("entry"), sig.get("entry"), direction, "(ตลาด)")
    msg += _tg_level_line("🎯", "TP1",   sig.get("tp1"),   sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP2",   sig.get("tp2"),   sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP3",   sig.get("tp3"),   sig.get("entry"), direction)
    msg += _tg_level_line("🛡", "Soft SL", sig.get("ssl"), sig.get("entry"), direction)
    msg += _tg_level_line("🛑", "Hard SL", sig.get("hsl"), sig.get("entry"), direction)
    msg += "\n"
    msg += _tg_rsi_line(ind)
    msg += f"📈 MACD hist 15m: {t15.get('macd_hist', '—')}\n"
    if t15.get("vol_ratio"):
        msg += f"📦 Volume: {t15['vol_ratio']:.1f}x avg\n"
    if fr is not None:
        msg += f"💸 Funding: {fr:+.4f}%\n"
    if sig.get("risk_flags"):
        msg += f"\n⚠️ ความเสี่ยง: {_tg_escape(sig['risk_flags'][0], 180)}\n"
    if sig.get("reason"):
        msg += f"\n💬 {_tg_escape(sig['reason'], 260)}\n"
    return _tg_send_all(msg)

def _send_telegram_rejected(sig):
    sym = sig["symbol"].replace("USDT", ""); direction = sig["direction"]
    title = ("🟦 LONG" if direction == "Long" else "🔵 SHORT") + f" {sym}/USDT | Bot Conf: {_bot_conf_value(sig)}/100"
    ind = sig.get("indicators", {}); t15 = ind.get("15m", {})
    fr  = sig.get("funding_rate") or (sig.get("indicators") or {}).get("funding_rate")
    reason = sig.get("reject_reason") or sig.get("reason") or "Rejected by validation"
    msg  = _tg_header(title, "REJECTED")
    msg += f"🕐 {now_thai().strftime('%H:%M')} TH\n"
    msg += f"📍 {_tg_escape(_fmt_regime(sig.get('regime')))} | Strategy: {_tg_escape(sig.get('strategy_used','—'))}\n"
    if sig.get("gate_path"): msg += f"🔀 Gate: {_tg_escape(sig['gate_path'])}\n"
    msg += "\n"
    msg += _tg_level_line("💰", "Entry", sig.get("entry"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP1",   sig.get("tp1"),   sig.get("entry"), direction)
    msg += _tg_level_line("🛑", "Hard SL", sig.get("hsl"), sig.get("entry"), direction)
    msg += "\n"
    if sig.get("reason_code") and sig["reason_code"] != "—":
        msg += f"📋 Code: {_tg_escape(sig['reason_code'])}\n"
    msg += _tg_rsi_line(ind)
    if t15.get("vol_ratio"): msg += f"📦 Volume: {t15['vol_ratio']:.1f}x avg\n"
    if fr is not None: msg += f"💸 Funding: {fr:+.4f}%\n"
    msg += f"\n💬 {_tg_escape(reason, 320)}\n\n━━━━━━"
    return _tg_send_all(msg)

def should_notify(verdict):
    n = getattr(cfg, "NOTIFY_ON", "approved_weak")
    if n == "all": return True
    if n == "approved_weak": return verdict in ("APPROVED", "WEAK APPROVAL", "REJECTED")
    if n == "approved_only": return verdict == "APPROVED"
    return False

# ═══════════════════════════════════════════════════════════
# [SET3-1] VOL_RATIO MISSING DATA FIX
# ═══════════════════════════════════════════════════════════
def low_liquidity_reason(tf_15m):
    """
    SET3 FIX: แยก vol_ratio=0.000 (missing data) ออกจาก low volume จริง

    Evidence: 22.6% ของทุก signals มี vol_ratio=0.000
    median vol_ratio ของ LOW_LIQUIDITY_BLOCK = 0.000
    → เกือบทั้งหมดเป็น data ที่ดึงไม่ได้ ไม่ใช่ volume ต่ำจริง

    Rule:
      vol_ratio == 0.0  → missing data → ไม่ block (return "")
      0 < vol_ratio < threshold → low vol จริง → block
      vol_ratio >= threshold → ปกติ
    """
    vol_15m = (tf_15m or {}).get("vol_ratio")
    if vol_15m is None:
        return ""
    # [SET3] exact 0.0 = missing data, ข้ามการ check
    if vol_15m == 0.0:
        return ""
    # ยังคง block สำหรับ confirmed low vol (0 < x < threshold)
    if vol_15m < cfg.CTX_VOL_MIN_FOR_SIGNAL:
        return f"LOW_LIQUIDITY_BLOCK: vol_ratio_15m={vol_15m:.3f} < {cfg.CTX_VOL_MIN_FOR_SIGNAL}"
    return ""

def is_vol_data_missing(tf_15m):
    """Returns True if vol_ratio=0.000 (missing data, not low volume)"""
    vol = (tf_15m or {}).get("vol_ratio")
    return vol is not None and vol == 0.0

def bot_gate_reason(pre_conf, passed=None, claude_gate=None):
    gate = claude_gate or cfg.CLAUDE_MIN_CONF
    if pre_conf < gate:
        filter_text = ""
        if passed is not None:
            filter_text = ", filter passed" if passed else ", filter not passed"
        return f"Bot: pre_conf={pre_conf}% below review gate {gate}%{filter_text}"
    return f"Bot: pre_conf={pre_conf}% blocked before bot review"

def main_score_note(verdict, conf, pre_conf=None, claude_called=False, gate_path=""):
    gate = str(gate_path or "")
    if gate.startswith("TIER4_BOT_REVIEW"): return "Main score=bot review score"
    if claude_called and gate.startswith("TIER4"): return "Main score=AI confidence"
    if claude_called and gate.startswith("MANUAL"): return "Main score=bot pre-score; AI score stored separately"
    if gate.startswith("TIER5"): return "Main score=bot auto score"
    if verdict in ("NO TRADE", "WEAK SIGNAL") or gate.startswith("TIER3") or "LOW_LIQUIDITY" in gate:
        return "Main score=capped bot score"
    if claude_called: return "Main score=AI confidence"
    return "Main score=bot score"

def with_main_score_note(reason, note):
    reason = str(reason or "").strip()
    if not note: return reason
    if note in reason: return reason
    return f"{note} | {reason}" if reason else note

def low_liquidity_block_reason(tf_15m, pre_conf, passed=None, claude_gate=None):
    reason = low_liquidity_reason(tf_15m)
    if not reason: return ""
    return f"{reason} | {bot_gate_reason(pre_conf, passed=passed, claude_gate=claude_gate)}"

# ═══════════════════════════════════════════════════════════
# PRE-FILTER (updated for SET3)
# ═══════════════════════════════════════════════════════════
def should_skip_claude(direction, rb_data, tf_1h, tf_4h, pre_conf, market_ctx=None, tf_15m=None):
    """
    SET3 Pre-filter ก่อนเรียก Claude
    [SET3 change]: vol_ratio=0.0 ไม่ถูก block อีกต่อไป (missing data fix)
    """
    long_score  = rb_data.get("long_score", 0)
    short_score = rb_data.get("short_score", 0)
    margin = (long_score - short_score) if direction == "Long" else (short_score - long_score)

    # [SET3] vol missing check ก่อน — ถ้า missing ข้ามทั้ง block
    low_liq = low_liquidity_block_reason(
        tf_15m if tf_15m else {"vol_ratio": rb_data.get("vol_ratio")},
        pre_conf,
    )
    if low_liq:
        return True, low_liq
    # (ถ้า vol_ratio=0.0 → low_liq="" → ไม่ block — per SET3 fix)

    if market_ctx:
        oi_ch  = market_ctx.get("oi_change_pct", 0) or 0
        t_buy  = market_ctx.get("taker_buy_pct",  50) or 50
        t_sell = market_ctx.get("taker_sell_pct", 50) or 50
        if direction == "Long" and oi_ch < cfg.CTX_OI_DROP_STRONG and t_sell >= cfg.CTX_TAKER_AGGRESSIVE:
            return True, f"CTX_BLOCK_LONG: OI={oi_ch:.2f}%+taker_sell={t_sell:.1f}% → momentum bearish"
        if direction == "Short" and oi_ch < cfg.CTX_OI_DROP_STRONG and t_buy >= cfg.CTX_TAKER_AGGRESSIVE:
            return True, f"CTX_BLOCK_SHORT: OI={oi_ch:.2f}%+taker_buy={t_buy:.1f}% → momentum bullish"

    # HTF mode: ใช้ margin threshold ต่ำกว่า (handled upstream in check_htf_exhaustion)
    htf_mode = rb_data.get("htf_mode", False)

    if direction == "Short":
        if htf_mode:
            return False, ""  # HTF short gate ผ่านแล้วใน check_htf_exhaustion
        blocked, reason = should_block_short_set2(tf_4h, tf_1h, rb_data)
        if blocked: return True, reason
        return False, ""

    if direction == "Long":
        if htf_mode:
            return False, ""  # HTF long gate ผ่านแล้วใน check_htf_exhaustion
        rsi_turn_up = rb_data.get("rsi_turning_up", False)
        score = long_score
        if not rsi_turn_up and score < cfg.REBOUND_MIN_SCORE_FOR_CLAUDE:
            return True, f"LONG PRE-FILTER: no rsi_turn_up + score={score}<{cfg.REBOUND_MIN_SCORE_FOR_CLAUDE}"
        if margin < cfg.REBOUND_MIN_MARGIN:
            return True, f"LONG PRE-FILTER: margin={margin}<{cfg.REBOUND_MIN_MARGIN}"

    return False, ""

def t4_bot_review(symbol, direction, strategy, regime, pre_conf, m, tf_bk, rb_data):
    t15 = m.get("tf_15m") or {}
    t1h = m.get("tf_1h") or {}
    t2h = m.get("tf_2h") or {}
    t4h = m.get("tf_4h") or {}
    ctx_bonus = m.get("ctx_bonus", 0) or 0
    vol_ratio = sfloat(t15.get("vol_ratio"))
    rsi_15 = sfloat(t15.get("rsi"))
    rsi_1h = sfloat(t1h.get("rsi"))
    macd_15 = sfloat(t15.get("macd_hist"))
    macd_1h = sfloat(t1h.get("macd_hist"))
    macd_4h = sfloat(t4h.get("macd_hist"))
    mtf_bucket, mtf_aligned, mtf_total, mtf_conflict = mtf_alignment_bucket(direction, t15=t15, t1h=t1h, t2h=t2h, t4h=t4h)
    pattern_key = build_pattern_key(strategy, regime, direction, pre_conf, ctx_bonus, mtf_bucket)
    pattern_adjust, pattern_entry, pattern_reasons = pattern_memory_adjustment(pattern_key)
    reasons = []
    risk_flags = []

    # 1. Hard blocks
    if strategy == "TREND_FOLLOW" and regime == "TRENDING" and direction == "Long" and ctx_bucket(ctx_bonus) == "neg":
        if not ((macd_15 or 0) > 10 and (macd_1h or 0) > 0):
            return {
                "verdict": "NO TRADE",
                "conf": clamp((pre_conf or 0) - 10),
                "levels": {"reason": "T4 Bot Review: negative market context without momentum support", "reason_code": "T4_NEG_CTX_BLOCK"},
                "review": {
                    "decision_source": "TIER4_BOT_REVIEW",
                    "t4_score": clamp((pre_conf or 0) - 10),
                    "trend_score": 0,
                    "momentum_score": 0,
                    "structure_score": 0,
                    "context_score": -8,
                    "risk_score": -4,
                    "pattern_adjustment_score": pattern_adjust,
                    "pattern_key_v1": pattern_key,
                    "pattern_memory": pattern_entry,
                },
            }
    if vol_ratio is not None and vol_ratio < 0.25:
        return {
            "verdict": "NO TRADE",
            "conf": clamp((pre_conf or 0) - 8),
            "levels": {"reason": f"T4 Bot Review: vol_ratio_15m={vol_ratio:.2f} too low for follow-through", "reason_code": "T4_LOW_VOL_BLOCK"},
            "review": {
                "decision_source": "TIER4_BOT_REVIEW",
                "t4_score": clamp((pre_conf or 0) - 8),
                "trend_score": 0,
                "momentum_score": 0,
                "structure_score": 0,
                "context_score": 0,
                "risk_score": -8,
                "pattern_adjustment_score": pattern_adjust,
                "pattern_key_v1": pattern_key,
                "pattern_memory": pattern_entry,
            },
        }
    if mtf_conflict and (pre_conf or 0) >= 75:
        return {
            "verdict": "NO TRADE",
            "conf": clamp((pre_conf or 0) - 12),
            "levels": {"reason": "T4 Bot Review: high score but timeframe conflict", "reason_code": "T4_MTF_CONFLICT_BLOCK"},
            "review": {
                "decision_source": "TIER4_BOT_REVIEW",
                "t4_score": clamp((pre_conf or 0) - 12),
                "trend_score": 0,
                "momentum_score": 0,
                "structure_score": 0,
                "context_score": 0,
                "risk_score": -6,
                "pattern_adjustment_score": pattern_adjust,
                "pattern_key_v1": pattern_key,
                "pattern_memory": pattern_entry,
            },
        }

    # 2. Subscores
    trend_score = 0
    if mtf_aligned >= 3: trend_score += 18
    elif mtf_aligned >= 2: trend_score += 12
    elif mtf_aligned >= 1: trend_score += 6
    if direction == "Long" and (macd_4h or 0) > 0: trend_score += 6
    if direction == "Short" and (macd_4h or 0) < 0: trend_score += 6

    momentum_hits = 0
    if direction == "Long":
        momentum_hits += 1 if (macd_15 or -999) > 10 else 0
        momentum_hits += 1 if (macd_1h or -999) > 0 else 0
        momentum_hits += 1 if (rsi_15 or 0) >= 55 and (rsi_1h or 0) >= 50 else 0
    else:
        momentum_hits += 1 if (macd_15 or 999) < -10 else 0
        momentum_hits += 1 if (macd_1h or 999) < 0 else 0
        momentum_hits += 1 if (rsi_15 or 100) <= 45 and (rsi_1h or 100) <= 50 else 0
    momentum_score = 6 + momentum_hits * 6
    if momentum_hits < 2:
        risk_flags.append("WEAK_FOLLOW_THROUGH")
        reasons.append(f"follow-through gate {momentum_hits}/3")
        momentum_score -= 6

    structure_score = 10
    if strategy == "TREND_FOLLOW" and direction == "Long" and (rsi_1h or 0) > 70:
        structure_score -= 4
        risk_flags.append("HTF_OVERBOUGHT_LONG")
    if strategy == "TREND_FOLLOW" and direction == "Short" and (rsi_1h or 100) < 30:
        structure_score -= 4
        risk_flags.append("HTF_OVERSOLD_SHORT")
    if tf_bk.get("bias_fallback"):
        structure_score -= 2
        risk_flags.append("BIAS_FALLBACK")

    context_score = 10 + max(-8, min(6, int(ctx_bonus * 2)))
    risk_score = 10
    if vol_ratio is not None and vol_ratio < 0.75:
        risk_score -= 3
        risk_flags.append("SOFT_LOW_VOLUME")
    if vol_ratio is not None and vol_ratio >= 1.5:
        risk_score += 2
    if mtf_bucket == "mid":
        risk_score -= 2
    elif mtf_bucket == "low":
        risk_score -= 5

    raw_score = int(round(
        0.25 * (pre_conf or 0)
        + trend_score
        + momentum_score
        + structure_score
        + context_score
        + risk_score
        + pattern_adjust
    ))
    t4_score = clamp(raw_score)

    if momentum_hits >= 2:
        reasons.append(f"follow-through gate {momentum_hits}/3 passed")
    reasons.extend(pattern_reasons)
    reasons.append(f"pattern={pattern_key}")

    if t4_score >= 72:
        verdict = "APPROVED"
    elif t4_score >= 62:
        verdict = "WEAK APPROVAL"
    elif momentum_hits >= 2 and t4_score >= 55:
        verdict = "WEAK SIGNAL"
    else:
        verdict = "NO TRADE"

    levels = calc_fallback_levels(m["price"], direction, m.get("atr", 0))
    levels["entry"] = str(round(m["price"], 2))
    levels["reason"] = "T4 Bot Review: " + " | ".join(reasons)
    levels["reason_code"] = "T4_BOT_REVIEW"
    levels["risk_flags"] = risk_flags
    levels["_beta_review"] = {
        "decision_source": "TIER4_BOT_REVIEW",
        "t4_score": t4_score,
        "trend_score": trend_score,
        "momentum_score": momentum_score,
        "structure_score": structure_score,
        "context_score": context_score,
        "risk_score": risk_score,
        "pattern_adjustment_score": pattern_adjust,
        "pattern_key_v1": pattern_key,
        "pattern_memory": pattern_entry,
    }
    return {
        "verdict": verdict,
        "conf": t4_score,
        "levels": levels,
        "review": levels["_beta_review"],
    }

# ═══════════════════════════════════════════════════════════
# BUILD SIGNAL (updated for SET3 — strategy_used=REBOUND_HTF)
# ═══════════════════════════════════════════════════════════
def build_signal(symbol, direction, m, verdict, conf, levels,
                 ai_text, regime, regime_conf, regime_reasons,
                 strategy, filter_reason, tf_bk, rb_data,
                 gate_path="UNKNOWN", pre_conf=None, claude_called=False):
    t15 = m.get("tf_15m") or {}; t30 = m.get("tf_30m") or {}
    t1h = m.get("tf_1h")  or {}; t2h = m.get("tf_2h")  or {}
    t4h = m.get("tf_4h")  or {}

    is_tradeable  = verdict in ("APPROVED", "WEAK APPROVAL")
    score_for_levels = max((conf or 0), (pre_conf or 0))
    allow_logged  = bool(
        score_for_levels >= 50 and
        any(levels.get(k) for k in ("tp1","tp2","ssl","hsl","suggested_tp1","suggested_ssl"))
    )
    entry_val = levels.get("entry") or str(round(m["price"], 2))
    levels    = normalize_trade_levels(entry_val, direction, levels)
    ssl_val   = levels.get("ssl")  if (is_tradeable or allow_logged) else None
    hsl_val   = levels.get("hsl")  if (is_tradeable or allow_logged) else None
    tp1_val   = levels.get("tp1")  if (is_tradeable or allow_logged) else None
    tp2_val   = levels.get("tp2")  if (is_tradeable or allow_logged) else None
    tp3_val   = levels.get("tp3")  if (is_tradeable or allow_logged) else None
    score_note  = main_score_note(verdict, conf, pre_conf=pre_conf, claude_called=claude_called, gate_path=gate_path)
    reason      = with_main_score_note(levels.get("reason") or "", score_note)
    reject_reason = "" if is_tradeable else with_main_score_note(levels.get("reason") or verdict, score_note)
    beta_review = levels.get("_beta_review") or {}

    return {
        "id":            f"{symbol}_{now_thai().strftime('%Y%m%d_%H%M')}",
        "time":          now_thai().isoformat(),
        "time_thai":     now_thai().strftime('%Y-%m-%d %H:%M TH'),
        "symbol":        symbol,
        "bot_version":   cfg.BOT_VERSION,
        "gate_path":     gate_path,
        "pre_conf":      pre_conf if pre_conf is not None else conf,
        "claude_called": claude_called,
        "indicator_set": {"name": S["name"], "version": S["version"],
                          "configured": S["configured"], "review_after": S["review_after"]},
        "regime":         regime, "regime_conf": regime_conf, "regime_reasons": regime_reasons,
        "strategy_used":  strategy,
        "filter_passed":  True, "filter_reason": filter_reason,
        "direction":      direction,
        "price":    m["price"], "change": m["change"],
        "funding_rate":   m.get("funding_rate"),
        # [SET3] vol_data_missing flag
        "vol_data_missing": is_vol_data_missing(m.get("tf_15m")),
        "decision_log": {
            "regime":     {"value": regime, "conf": regime_conf, "reasons": regime_reasons},
            "strategy":   strategy,
            "pre_conf":   pre_conf if pre_conf is not None else conf,
            "gate":       {"filter": cfg.FILTER_MIN_CONF, "weak": cfg.WEAK_MIN_CONF,
                           "claude": cfg.CLAUDE_MIN_CONF, "auto": cfg.AUTO_APPROVE_CONF},
            "tf_30m_rebound": rb_data if strategy in ("REBOUND", "REBOUND_HTF") else {},
            "tf_breakdown":   tf_bk,
            "market_ctx":     m.get("market_ctx", {}),
            "ctx_bonus":      m.get("ctx_bonus", 0),
            "ctx_reasons":    m.get("ctx_reasons", []),
        },
        "indicators": {
            "15m": {"rsi": t15.get("rsi"), "macd_hist": t15.get("macd_hist"),
                    "bb_pct": t15.get("bb_pct"), "ema_short": t15.get("ema_short"),
                    "ema_mid": t15.get("ema_mid"), "vol_ratio": t15.get("vol_ratio")},
            "30m": {"rsi": t30.get("rsi"), "macd_hist": t30.get("macd_hist"),
                    "bb_pct": t30.get("bb_pct"), "vol_ratio": t30.get("vol_ratio")} if t30 else {},
            "1h":  {"rsi": t1h.get("rsi"), "macd_hist": t1h.get("macd_hist"),
                    "ema_short": t1h.get("ema_short"), "ema_mid": t1h.get("ema_mid")},
            "2h":  {"rsi": t2h.get("rsi"), "macd_hist": t2h.get("macd_hist"),
                    "ema_short": t2h.get("ema_short"), "ema_mid": t2h.get("ema_mid")} if t2h else {},
            "4h":  {"rsi": t4h.get("rsi"), "macd_hist": t4h.get("macd_hist")},
        },
        "verdict": verdict, "conf": conf,
        "entry": entry_val if (is_tradeable or allow_logged) else None,
        "ssl": ssl_val, "hsl": hsl_val,
        "tp1": tp1_val, "tp2": tp2_val, "tp3": tp3_val,
        "reason_code":     levels.get("reason_code", ""),
        "main_score_note": score_note,
        "reason":          reason,
        "risk_flags":      levels.get("risk_flags", []),
        "reject_reason":   reject_reason,
        "ai_text":         ai_text,
        "decision_source": beta_review.get("decision_source") or ("TIER4_BOT_REVIEW" if gate_path.startswith("TIER4_BOT_REVIEW") else gate_path or "UNKNOWN"),
        "t4_score":        beta_review.get("t4_score"),
        "trend_score":     beta_review.get("trend_score"),
        "momentum_score":  beta_review.get("momentum_score"),
        "structure_score": beta_review.get("structure_score"),
        "context_score":   beta_review.get("context_score"),
        "risk_score":      beta_review.get("risk_score"),
        "pattern_adjustment_score": beta_review.get("pattern_adjustment_score"),
        "pattern_key_v1":  beta_review.get("pattern_key_v1"),
        "pattern_memory":  beta_review.get("pattern_memory"),
        "recheck":         None,
    }

# ═══════════════════════════════════════════════════════════
# MAIN — SET3 Flow
# ═══════════════════════════════════════════════════════════
def main():
    log("=" * 60)
    log(f"🚀 Crypto Signal Bot — SET3v1")
    log(f"   HTF Exhaustion Rebound + Vol Missing Fix")
    log(f"   Review: {S['review_after']}")
    log("=" * 60)

    logs         = load_log()
    claude_calls = 0
    skipped      = 0

    for symbol in getattr(cfg, "CRON_SYMBOLS", cfg.SYMBOLS):
        log(f"\n📊 {symbol}")
        m = fetch_market(symbol)
        if not m:
            log("  ❌ Failed to fetch"); continue

        t15 = m["tf_15m"] or {}; t1h = m["tf_1h"] or {}; t4h = m["tf_4h"] or {}

        regime, regime_conf, regime_reasons = detect_regime(t1h, t4h, m["price"])
        log(f"  📍 Regime: {regime} (conf:{regime_conf}%)")

        if regime == "VOLATILE":
            log(f"  ⛔ VOLATILE — งดเทรด")
            skipped += 1
            th_now = now_thai()
            logs.insert(0, {
                "id": f"{symbol}_{th_now.strftime('%Y%m%d_%H%M')}",
                "time": th_now.isoformat(), "time_thai": th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol": symbol, "price": m["price"], "change": m["change"],
                "bot_version": cfg.BOT_VERSION, "gate_path": "VOLATILE_BLOCK",
                "pre_conf": 0, "claude_called": False,
                "regime": regime, "regime_conf": regime_conf,
                "strategy_used": "NONE", "direction": "N/A",
                "verdict": "NO TRADE", "conf": 0,
                "reject_reason": "VOLATILE — งดเทรด", "recheck": None,
            })
            time.sleep(1); continue

        passed = False; direction = "N/A"
        strategy = "NONE"; filter_reason = ""
        tf_bk = {}; rb_data = {}
        pre_conf = 0; block_reason_code = ""

        if regime == "TRENDING":
            strategy = "TREND_FOLLOW"
            passed, direction, reasons, tf_bk = check_multi_tf(
                m["tf_4h"], m["tf_1h"], m["tf_15m"], m["price"],
                tf_2h=m.get("tf_2h"), market_ctx=m.get("market_ctx"))
            filter_reason = " | ".join(reasons)

            # ── [SET3-4] EXHAUSTION PENALTY ────────────────────────────
            # 4h RSI>72 + BB>0.90 + direction=Long → Long TREND_FOLLOW は overbought
            # ลด pre_conf เพื่อลด wasted Claude calls
            rsi_4h_val  = (m["tf_4h"] or {}).get("rsi") or 50
            bb_pct_4h   = (m["tf_4h"] or {}).get("bb_pct") or 0.5
            exhaustion_long = (
                rsi_4h_val > S["SET3_EXHAUSTION_RSI_OB"] and
                bb_pct_4h  > S["SET3_EXHAUSTION_BB_OB"] and
                direction == "Long"
            )
            base_pre_conf = 75 if passed else (55 if tf_bk.get("4h",{}).get("bias") != "NEUTRAL" else 20)

            if exhaustion_long:
                penalty = S["SET3_EXHAUSTION_PENALTY"]
                pre_conf = max(base_pre_conf - penalty, 0)
                block_reason_code = "HTF_EXHAUSTION_LONG"
                log(f"  ⚠️ [SET3] EXHAUSTION_PENALTY: 4h RSI={rsi_4h_val:.1f} + BB={bb_pct_4h:.2f} "
                    f"→ pre_conf {base_pre_conf}→{pre_conf}% (Long at overbought)")
            else:
                pre_conf = base_pre_conf

            log(f"  📈 Strategy: TREND_FOLLOW | {'✅ PASS' if passed else '⏭ SKIP'}: {filter_reason[:80]}")

            # ── [SET3] HTF Exhaustion check ─────────────────────────────
            # ถ้า TRENDING แต่ 4h RSI extreme → check HTF rebound เป็น fallback
            # SHORT rebound เมื่อ 4h RSI overbought extreme ใน TRENDING regime
            htf_passed, htf_dir, htf_reasons, htf_data = check_htf_exhaustion(
                m["tf_4h"], m["tf_1h"], m["tf_15m"], m["price"]
            )
            if htf_passed and htf_dir != "N/A":
                log(f"  🔄 [SET3] HTF Exhaustion trigger: {htf_dir} | {' | '.join(htf_reasons[:2])}")
                # Override ด้วย REBOUND_HTF strategy ถ้า stronger signal
                htf_score = max(htf_data.get("long_score",0), htf_data.get("short_score",0))
                htf_pre   = min(round(htf_score / 10 * 100), 75)  # scale /10
                # ใช้ HTF เฉพาะถ้า pre_conf ต่ำ (TREND_FOLLOW ไม่ confidence) หรือ exhaustion
                if exhaustion_long or (not passed) or htf_pre > pre_conf:
                    passed       = htf_passed
                    direction    = htf_dir
                    strategy     = "REBOUND_HTF"
                    filter_reason = " | ".join(htf_reasons)
                    rb_data      = htf_data
                    pre_conf     = htf_pre
                    tf_bk        = {}
                    log(f"  🔁 [SET3] Switched to REBOUND_HTF: {direction} pre_conf={pre_conf}%")

        elif regime in ("RANGING", "MIXED"):
            # [SET3] RANGING: ลอง HTF exhaustion ก่อน 30m rebound
            htf_passed, htf_dir, htf_reasons, htf_data = check_htf_exhaustion(
                m["tf_4h"], m["tf_1h"], m["tf_15m"], m["price"]
            )
            if htf_passed and htf_dir != "N/A":
                htf_score = max(htf_data.get("long_score",0), htf_data.get("short_score",0))
                htf_pre   = min(round(htf_score / 10 * 100), 75)
                strategy     = "REBOUND_HTF"
                passed       = True
                direction    = htf_dir
                filter_reason = " | ".join(htf_reasons)
                rb_data      = htf_data
                pre_conf     = htf_pre
                log(f"  ↔️ HTF Exhaustion in RANGING: {direction} pre_conf={pre_conf}%")
            else:
                # fallback: 30m REBOUND เดิม
                strategy = "REBOUND"
                passed, direction, reasons, rb_data = check_rebound_30m(
                    m["tf_30m"], m["tf_1h"], m["price"],
                    change_24h   = m.get("change", 0),
                    funding_rate = m.get("funding_rate"),
                    rsi_series   = m.get("rsi_series_30m"),
                    tf_5m        = m.get("tf_5m"),
                    tf_4h        = m.get("tf_4h"),
                    tf_15m       = m.get("tf_15m"),
                )
                filter_reason = " | ".join(reasons)
                long_score  = rb_data.get("long_score", 0)
                short_score = rb_data.get("short_score", 0)
                pre_conf = min(round(max(long_score, short_score) / cfg.REBOUND_MAX_SCORE * 100), 95)
                persist_bonus = get_persistence_bonus(symbol, direction, logs)
                if persist_bonus and passed:
                    pre_conf = min(pre_conf + persist_bonus, 95)
                    log(f"  🔄 Persistence bonus +{persist_bonus}% → pre_conf={pre_conf}%")
                log(f"  ↔️  Strategy: REBOUND | {'✅' if passed else '⏭'}: {filter_reason[:80]}")
                log(f"  📊 Score: L={long_score} S={short_score} /{cfg.REBOUND_MAX_SCORE} → pre_conf={pre_conf}%")

        # ── Market Context Bonus ──────────────────────────────────
        mkt_ctx     = m.get("market_ctx") or {}
        ctx_bonus   = 0
        ctx_reasons = []
        ctx_aligned = False

        if direction != "N/A":
            ctx_bonus, ctx_reasons = calc_market_context_bonus(mkt_ctx, direction)
            ctx_aligned            = is_market_context_aligned(mkt_ctx, direction)
            if ctx_bonus != 0:
                pre_conf_before = pre_conf
                pre_conf = max(0, min(95, pre_conf + ctx_bonus))
                if cfg.CTX_LOG_APPLIED:
                    log(f"  📡 ctx_bonus={ctx_bonus:+d} ({direction}) "
                        f"pre_conf {pre_conf_before}% → {pre_conf}% "
                        f"| {' | '.join(ctx_reasons)[:80]}")
            else:
                log(f"  📡 ctx_bonus=0 | {ctx_reasons[0] if ctx_reasons else 'neutral'}")

        m["ctx_bonus"]   = ctx_bonus
        m["ctx_reasons"] = ctx_reasons
        m["ctx_aligned"] = ctx_aligned

        # ── 5-Tier Gate ───────────────────────────────────────────
        def _base_log(verdict_val, gate_path_val, claude_called=False):
            th_now = now_thai()
            score_note = main_score_note(verdict_val, pre_conf, pre_conf=pre_conf,
                                         claude_called=claude_called, gate_path=gate_path_val)
            base_reject = filter_reason if verdict_val in ("FILTERED","NO TRADE","WEAK SIGNAL") else ""
            return {
                "id": f"{symbol}_{th_now.strftime('%Y%m%d_%H%M')}",
                "time": th_now.isoformat(), "time_thai": th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol": symbol, "price": m["price"], "change": m["change"],
                "bot_version": cfg.BOT_VERSION,
                "indicator_set": {"name": S["name"], "version": S["version"]},
                "regime": regime, "regime_conf": regime_conf,
                "strategy_used": strategy, "direction": direction,
                "pre_conf": pre_conf, "verdict": verdict_val, "conf": pre_conf,
                "gate_path": gate_path_val, "claude_called": claude_called,
                "filter_passed": passed, "filter_reason": filter_reason,
                "block_reason_code": block_reason_code or "",
                "main_score_note": score_note,
                "reason": score_note,
                "reject_reason": with_main_score_note(base_reject, score_note) if base_reject else "",
                "vol_data_missing": is_vol_data_missing(m.get("tf_15m")),
                "indicators": {
                    "15m": {"rsi": t15.get("rsi"), "macd_hist": t15.get("macd_hist"),
                            "bb_pct": t15.get("bb_pct"), "ema_short": t15.get("ema_short"),
                            "ema_mid": t15.get("ema_mid"), "vol_ratio": t15.get("vol_ratio")},
                    "1h":  {"rsi": t1h.get("rsi"), "macd_hist": t1h.get("macd_hist"),
                            "ema_short": t1h.get("ema_short"), "ema_mid": t1h.get("ema_mid")},
                    "4h":  {"rsi": t4h.get("rsi"), "macd_hist": t4h.get("macd_hist")},
                },
                "decision_log": {
                    "regime": {"value": regime, "conf": regime_conf, "reasons": regime_reasons},
                    "strategy": strategy, "pre_conf": pre_conf,
                    "gate": {"filter": cfg.FILTER_MIN_CONF, "weak": cfg.WEAK_MIN_CONF,
                             "claude": cfg.CLAUDE_MIN_CONF, "auto": cfg.AUTO_APPROVE_CONF},
                    "tf_30m_rebound": rb_data if strategy in ("REBOUND", "REBOUND_HTF") else {},
                    "tf_breakdown": tf_bk,
                    "market_ctx":  mkt_ctx,
                    "ctx_bonus":   m.get("ctx_bonus", 0),
                    "ctx_reasons": m.get("ctx_reasons", []),
                },
                "recheck": None,
            }

        # Tier 1
        if pre_conf < cfg.FILTER_MIN_CONF:
            skipped += 1
            log(f"  🗑 TIER1: pre_conf={pre_conf}% < {cfg.FILTER_MIN_CONF}% → FILTERED (no log)")
            time.sleep(1); continue

        # Tier 2
        if pre_conf < cfg.WEAK_MIN_CONF:
            skipped += 1
            log(f"  📋 TIER2: pre_conf={pre_conf}% → WEAK SIGNAL")
            logs.insert(0, _base_log("WEAK SIGNAL", "TIER2_WEAK"))
            time.sleep(1); continue

        # [SET3] Low liquidity check (vol=0.0 ไม่ block แล้ว)
        low_liq = low_liquidity_block_reason(m.get("tf_15m"), pre_conf, passed=passed)
        if low_liq:
            skipped += 1
            capped_conf = min(pre_conf, cfg.CLAUDE_MIN_CONF - 1)
            log(f"  🚫 LOW_LIQUIDITY_BLOCK: pre_conf={pre_conf}% capped={capped_conf}% → NO TRADE | {low_liq}")
            rec = _base_log("NO TRADE", "TIER3_LOW_LIQUIDITY_BLOCK")
            rec["conf"] = capped_conf
            rec["pre_conf_before_block"] = pre_conf
            rec["block_reason_code"] = "LOW_LIQUIDITY_BLOCK"
            rec["main_score_note"] = main_score_note("NO TRADE", capped_conf, pre_conf=pre_conf, gate_path=rec["gate_path"])
            rec["reason"] = rec["main_score_note"]
            rec["reject_reason"] = with_main_score_note(low_liq, rec["main_score_note"])
            logs.insert(0, rec)
            time.sleep(1); continue

        # Tier 3
        if pre_conf < cfg.CLAUDE_MIN_CONF:
            if not passed:
                skipped += 1
                log(f"  🤖 TIER3: pre_conf={pre_conf}% + filter fail → NO TRADE")
                rec = _base_log("NO TRADE", "TIER3_BOT_REJECT")
                rec["reject_reason"] = with_main_score_note(
                    f"Bot: pre_conf={pre_conf}% below review gate, filter not passed",
                    rec.get("main_score_note"),
                )
                logs.insert(0, rec)
                time.sleep(1); continue
            else:
                skipped += 1
                log(f"  📋 TIER3: pre_conf={pre_conf}% passed filter but below review gate → WEAK SIGNAL")
                logs.insert(0, _base_log("WEAK SIGNAL", "TIER3_WEAK_PASSED"))
                time.sleep(1); continue

        # Pre-filter ก่อน Tier 4
        if passed and strategy in ("REBOUND", "REBOUND_HTF"):
            skip, skip_reason = should_skip_claude(
                direction, rb_data, t1h, t4h, pre_conf,
                market_ctx=mkt_ctx,
                tf_15m=m.get("tf_15m"),
            )
            if skip:
                skipped += 1
                log(f"  🚫 SET3 PRE-FILTER: {skip_reason}")
                rec = _base_log("NO TRADE", "SET3_PREFILTER_BLOCK")
                rec["reject_reason"] = with_main_score_note(
                    f"SET3 pre-filter: {skip_reason}", rec.get("main_score_note"),
                )
                logs.insert(0, rec)
                time.sleep(1); continue

        # Tier 5: AUTO APPROVE
        auto_approved = False
        auto_approve_reason = ""

        if passed and direction != "N/A":
            ema_15m_bull = bool(t15.get("ema_short",0) and t15.get("ema_mid",0) and
                                t15.get("ema_short") > t15.get("ema_mid"))
            ema_1h_bull  = bool(t1h.get("ema_short",0) and t1h.get("ema_mid",0) and
                                t1h.get("ema_short") > t1h.get("ema_mid"))

            if strategy == "TREND_FOLLOW":
                if pre_conf >= cfg.AUTO_APPROVE_CONF and not tf_bk.get("bias_fallback"):
                    if direction == "Long" and ema_15m_bull and ema_1h_bull:
                        auto_approved = True
                        auto_approve_reason = f"TREND_FOLLOW: pre={pre_conf}% + EMA bull aligned"
                    elif direction == "Short" and not ema_15m_bull and not ema_1h_bull:
                        auto_approved = True
                        auto_approve_reason = f"TREND_FOLLOW: pre={pre_conf}% + EMA bear aligned"

            elif strategy in ("REBOUND", "REBOUND_HTF"):
                rb_ls     = rb_data.get("long_score", 0)
                rb_ss     = rb_data.get("short_score", 0)
                rb_turn_u = rb_data.get("rsi_turning_up", False)
                rb_turn_d = rb_data.get("rsi_turning_down", False)
                rb_margin = (rb_ls - rb_ss) if direction == "Long" else (rb_ss - rb_ls)
                rb_score  = rb_ls if direction == "Long" else rb_ss
                htf_mode  = rb_data.get("htf_mode", False)

                t5a_pre    = cfg.REBOUND_AUTO_APPROVE_CONF
                t5a_score  = cfg.REBOUND_TIER5_MIN_SCORE
                t5a_margin = cfg.REBOUND_TIER5_MIN_MARGIN if not htf_mode else S["SET3_HTF_MARGIN_MIN"]

                if pre_conf >= t5a_pre:
                    if direction == "Long" and rb_turn_u and rb_score >= t5a_score and rb_margin >= t5a_margin:
                        auto_approved = True
                        auto_approve_reason = (f"{'HTF_' if htf_mode else ''}REBOUND LONG TIER5-A: pre={pre_conf}% + "
                                               f"rsi_turn_up + score={rb_score} + margin={rb_margin}")
                    elif direction == "Short" and rb_turn_d and rb_score >= t5a_score and rb_margin >= t5a_margin:
                        auto_approved = True
                        auto_approve_reason = (f"{'HTF_' if htf_mode else ''}REBOUND SHORT TIER5-A: pre={pre_conf}% + "
                                               f"rsi_turn_dn + score={rb_score} + margin={rb_margin}")

        if auto_approved:
            log(f"  ✅ TIER5: {auto_approve_reason}")
            fallback = calc_fallback_levels(m["price"], direction, m.get("atr", 0))
            levels   = {**fallback, "entry": str(round(m["price"], 2)),
                        "reason": auto_approve_reason, "reason_code": "AUTO_APPROVED"}
            sig = build_signal(symbol, direction, m, "APPROVED", pre_conf, levels,
                               f"BOT_AUTO:{pre_conf}", regime, regime_conf, regime_reasons,
                               strategy, filter_reason, tf_bk, rb_data,
                               gate_path="TIER5_AUTO_APPROVED", pre_conf=pre_conf, claude_called=False)
            logs.insert(0, sig)
            if should_notify("APPROVED"):
                tg_sent = send_telegram(sig)
                log(f"  📱 Telegram: {'sent' if tg_sent else 'not sent'} (AUTO APPROVED)")
            time.sleep(1); continue

        # Tier 4: Bot Review gate
        effective_claude_min = cfg.CLAUDE_MIN_CONF
        if ctx_aligned and cfg.CTX_CLAUDE_GATE_REDUCTION > 0:
            effective_claude_min = cfg.CLAUDE_MIN_CONF - cfg.CTX_CLAUDE_GATE_REDUCTION
            log(f"  📡 ctx_aligned=True → review gate {cfg.CLAUDE_MIN_CONF}→{effective_claude_min}%")

        if strategy in ("REBOUND", "REBOUND_HTF") and direction != "N/A":
            rb_ls5b = rb_data.get("long_score", 0)
            rb_ss5b = rb_data.get("short_score", 0)
            rb_tu5b = rb_data.get("rsi_turning_up", False)
            rb_td5b = rb_data.get("rsi_turning_down", False)
            rb_sc5b = rb_ls5b if direction == "Long" else rb_ss5b
            rb_mg5b = (rb_ls5b - rb_ss5b) if direction == "Long" else (rb_ss5b - rb_ls5b)
            htf_mode5b = rb_data.get("htf_mode", False)
            mg_threshold = S["SET3_HTF_MARGIN_MIN"] if htf_mode5b else cfg.REBOUND_TIER5B_MARGIN
            t5b_ok = (
                (direction == "Long"  and rb_tu5b and rb_sc5b >= cfg.REBOUND_TIER5B_SCORE and rb_mg5b >= mg_threshold) or
                (direction == "Short" and rb_td5b and rb_sc5b >= cfg.REBOUND_TIER5B_SCORE and rb_mg5b >= mg_threshold)
            )
            if t5b_ok:
                gate_before = effective_claude_min
                effective_claude_min = min(effective_claude_min, cfg.REBOUND_TIER5B_GATE)
                if effective_claude_min < gate_before:
                    log(f"  📈 TIER5-B: quality REBOUND ({direction} score={rb_sc5b} margin={rb_mg5b} htf={htf_mode5b}) "
                        f"→ review gate {gate_before}→{effective_claude_min}%")

        if pre_conf < effective_claude_min:
            skipped += 1
            log(f"  📋 TIER3-CTX: pre_conf={pre_conf}% < {effective_claude_min}% → WEAK SIGNAL")
            logs.insert(0, _base_log("WEAK SIGNAL", "TIER3_CTX_WEAK"))
            time.sleep(1); continue

        if direction not in ("Long", "Short"):
            skipped += 1
            reason = "Direction=N/A: no valid long/short signal to validate"
            capped_conf = min(pre_conf, cfg.CLAUDE_MIN_CONF - 1)
            log(f"  🚫 NO_DIRECTION: pre_conf={pre_conf}% capped={capped_conf}% → NO TRADE | {reason}")
            pre_conf_before_no_direction = pre_conf
            pre_conf = capped_conf
            rec = _base_log("NO TRADE", "TIER3_NO_DIRECTION")
            rec["pre_conf_before_no_direction"] = pre_conf_before_no_direction
            rec["reject_reason"] = with_main_score_note(reason, rec.get("main_score_note"))
            logs.insert(0, rec)
            time.sleep(1); continue

        log(f"  🤖 TIER4_BOT_REVIEW: pre_conf={pre_conf}% | dir={direction} | {strategy}"
            f"{' (ctx gate '+str(effective_claude_min)+'%)' if ctx_aligned else ''}")
        try:
            review = t4_bot_review(symbol, direction, strategy, regime, pre_conf, m, tf_bk, rb_data)
            verdict = review["verdict"]
            conf = review["conf"]
            levels = review["levels"]
            ai_text = f"BOT_REVIEW:{conf}"
            claude_calls += 1

            sig = build_signal(symbol, direction, m, verdict, conf, levels,
                               ai_text, regime, regime_conf, regime_reasons,
                               strategy, filter_reason, tf_bk, rb_data,
                               gate_path="TIER4_BOT_REVIEW", pre_conf=pre_conf, claude_called=False)
            log(f"  → {verdict} | Conf:{conf} | Entry:{fmt(sig.get('entry'))} | TP1:{fmt(sig.get('tp1'))} | SL:{fmt(sig.get('ssl'))}")
            logs.insert(0, sig)

            if should_notify(verdict):
                tg_sent = send_telegram(sig)
                log(f"  📱 Telegram: {'sent' if tg_sent else 'not sent'} ({verdict})")
        except Exception as e:
            log(f"  ❌ Error: {e}")
            error_type, error_desc = classify_runtime_error(e)
            th_now = now_thai()
            logs.insert(0, {
                "id": f"{symbol}_{th_now.strftime('%Y%m%d_%H%M')}",
                "time": th_now.isoformat(), "time_thai": th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol": symbol, "price": m["price"], "direction": direction,
                "bot_version": cfg.BOT_VERSION, "regime": regime, "strategy_used": strategy,
                "gate_path": "TIER4_BOT_REVIEW", "pre_conf": pre_conf, "claude_called": False,
                "conf": 0, "verdict": "ERROR",
                "error_type": error_type, "error_msg": str(e),
                "reject_reason": f"ERROR[{error_type}]: {error_desc}",
                "recheck": None,
            })
        time.sleep(2)

    save_log(logs)
    log(f"\n{'='*60}")
    log(f"✅ Done — T4 reviews: {claude_calls} | Skipped: {skipped}")
    log(f"{'='*60}")

if __name__ == "__main__":
    main()
