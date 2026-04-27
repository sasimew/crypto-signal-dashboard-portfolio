#!/usr/bin/env python3
"""
Crypto Signal Bot — SET2 v1
════════════════════════════════════════════════════════════
การเปลี่ยนแปลงจาก SET1v4 (อิงจากการวิเคราะห์ log 683 signals):

[1] REGIME DETECTION — Bug-007 fix: MACD4h weight 3-tier (+1→+3/+5)
    RANGING ต้องการ |MACD4h| < 5 จึงจะ count (ไม่ใช่แค่ ATR ต่ำ)
    → ตลาด 4h bearish แรงจะกลายเป็น TRENDING ถูกต้อง

[2] REBOUND SCORING — calibrated จาก data จริง
    rsi_turning_up/down weight +2→+3 (differentiator ที่แรงที่สุด)
    เพิ่ม pre-filter: margin<4 หรือ score<7 → block ก่อน Claude
    → ลด wasted Claude calls ~60%

[3] SHORT CONDITIONS — เปิดทาง short อย่างสมเหตุสมผล
    ต้องผ่าน: 4h bearish + rsi_turning_down + short_score≥7
    block ถ้า 1h MACD > 50 (strong uptrend → Claude will reject)

[4] TREND STRATEGY — TRENDING regime trigger ได้จริง
    check_multi_tf: RSI threshold ผ่อนลง, pullback entry mode
    TRENDING: 4h MACD strong → ใช้ pullback ถึง 1h EMA21

Data basis: signal_log_20260424_20260426.json (683 signals, SET1v4)
  - rsi_turning_up + score≥7 → 61% approval rate
  - score margin ≤3 → 0.8% approval (waste)
  - Short: 0/89 Claude approved (100% rejected)
  - RANGING 100% / TRENDING 0% (regime blind to strong trends)
════════════════════════════════════════════════════════════
"""
import json, os, sys, time, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── ใช้ config_set2 แทน config เดิม ──────────────────────────
try:
    import config_set2 as cfg
except ImportError:
    import config as cfg   # fallback

# ── Copy utilities จาก bot.py เดิม (ไม่เปลี่ยน) ──────────────
TZ_THAI = timezone(timedelta(hours=7))

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
# INDICATOR SET 2
# ═══════════════════════════════════════════════════════════
INDICATOR_SET = {
    "name":             f"{cfg.BOT_VERSION} — MACD4h Regime + Calibrated Scoring + Short Gate + Market Context",
    "version":          "2.2",
    "configured":       "2026-04-27",
    "review_after":     "2026-05-03",
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
    # Rebound
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
    # Regime (SET2 MACD tiers)
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
# INDICATORS (ไม่เปลี่ยน — copy จาก SET1)
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
    ema_fast   = calc_ema(closes, fast)
    ema_slow   = calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_line  = ema_fast - ema_slow
    # simple approx for signal line
    macd_hist  = macd_line  # simplified — real signal needs series
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
# [1] REGIME DETECTION — SET2: MACD4h weighted 3-tier
# ═══════════════════════════════════════════════════════════
def detect_regime(tf_1h, tf_4h, price):
    """
    SET2 — แก้ Bug-007: MACD4h น้ำหนักจริงตาม magnitude

    SET1 problem: MACD4h ได้ +1 ทุกกรณีที่ ≠ 0
    → ตลาดที่ 4h MACD = -130 (bearish แรง) ยัง "RANGING" 100%

    SET2 solution:
      |MACD4h| > STRONG(80)  → +4 TRENDING (ชัดเจนมาก)
      |MACD4h| > MEDIUM(30)  → +2 TRENDING
      |MACD4h| > WEAK(5)     → +1 TRENDING
      |MACD4h| < WEAK        → 0 (flat → count เป็น RANGING ได้)

    RANGING ต้อง pair กับ flat MACD4h:
      ATR ต่ำ + |MACD4h| < WEAK → +2 RANGING (ยืนยันแน่)
      ATR ต่ำ + |MACD4h| > MEDIUM → +1 RANGING only (สงสัย)
    """
    S       = INDICATOR_SET
    reasons = []
    scores  = {"TRENDING": 0, "RANGING": 0, "VOLATILE": 0}

    if not tf_1h or not tf_4h:
        return "MIXED", 50, ["ข้อมูลไม่ครบ"]

    # ── ATR % ────────────────────────────────────────────────
    atr_pct_1h = (tf_1h["atr"] / price * 100) if tf_1h.get("atr") else 0
    atr_pct_4h = (tf_4h["atr"] / price * 100) if tf_4h.get("atr") else 0
    avg_atr_pct = (atr_pct_1h + atr_pct_4h) / 2

    # ── MACD4h magnitude (ก่อน ATR เพราะต้องใช้ pair) ────────
    mh4     = tf_4h.get("macd_hist") or 0
    mh4_abs = abs(mh4)
    mh4_dir = "BULL" if mh4 > 0 else "BEAR" if mh4 < 0 else "FLAT"

    if mh4_abs > S["REGIME_MACD4H_STRONG"]:        # > 80
        scores["TRENDING"] += 4
        reasons.append(f"MACD4h hist={mh4:.1f} ({mh4_dir}) — STRONG TREND (+4)")
    elif mh4_abs > S["REGIME_MACD4H_MEDIUM"]:      # > 30
        scores["TRENDING"] += 2
        reasons.append(f"MACD4h hist={mh4:.1f} ({mh4_dir}) — MEDIUM TREND (+2)")
    elif mh4_abs > S["REGIME_MACD4H_WEAK"]:        # > 5
        scores["TRENDING"] += 1
        reasons.append(f"MACD4h hist={mh4:.1f} ({mh4_dir}) — WEAK TREND (+1)")
    else:
        reasons.append(f"MACD4h hist={mh4:.1f} — FLAT (no TRENDING vote)")

    macd_flat = mh4_abs <= S["REGIME_MACD4H_WEAK"]  # สำหรับ RANGING pair

    # ── ATR → RANGING หรือ TRENDING ──────────────────────────
    if avg_atr_pct > S["REGIME_ATR_VOLATILE"]:
        scores["VOLATILE"] += 3
        reasons.append(f"ATR สูงมาก {avg_atr_pct:.2f}% → VOLATILE")
    elif avg_atr_pct > S["REGIME_ATR_TREND"]:
        scores["TRENDING"] += 2
        reasons.append(f"ATR ปกติ {avg_atr_pct:.2f}% → TRENDING")
    else:
        # ATR ต่ำ — แต่ต้องดู MACD ด้วย
        if macd_flat:
            scores["RANGING"] += 2   # ATR ต่ำ + MACD flat → RANGING ชัด
            reasons.append(f"ATR ต่ำ {avg_atr_pct:.2f}% + MACD flat → RANGING (+2)")
        else:
            scores["RANGING"] += 1   # ATR ต่ำ แต่ MACD trending → RANGING อ่อน
            reasons.append(f"ATR ต่ำ {avg_atr_pct:.2f}% แต่ MACD trending → RANGING อ่อน (+1)")

    # ── BB Width ─────────────────────────────────────────────
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

    # ── EMA Alignment 4h ─────────────────────────────────────
    e_s4 = tf_4h.get("ema_short"); e_m4 = tf_4h.get("ema_mid")
    if e_s4 and e_m4:
        ema_diff_4h = abs(e_s4 - e_m4) / e_m4 * 100
        if ema_diff_4h > S["REGIME_EMA_DIFF"]:
            scores["TRENDING"] += 2
            reasons.append(f"EMA4h ห่างกัน {ema_diff_4h:.2f}% → TRENDING (+2)")
        else:
            scores["RANGING"] += 1
            reasons.append(f"EMA4h ใกล้กัน {ema_diff_4h:.2f}% → RANGING (+1)")

    # ── EMA Alignment 1h ─────────────────────────────────────
    e_s1 = tf_1h.get("ema_short"); e_m1 = tf_1h.get("ema_mid")
    if e_s1 and e_m1:
        ema_diff_1h = abs(e_s1 - e_m1) / e_m1 * 100
        if ema_diff_1h > S["REGIME_EMA_DIFF"]:
            scores["TRENDING"] += 1
            reasons.append(f"EMA1h ห่างกัน {ema_diff_1h:.2f}% → TRENDING (+1)")

    # ── ตัดสิน ───────────────────────────────────────────────
    total = sum(scores.values()) or 1
    conf  = min(round(max(scores.values()) / total * 100), 95)

    if scores["VOLATILE"] >= 3:
        return "VOLATILE", conf, reasons
    if scores["TRENDING"] > scores["RANGING"]:
        return "TRENDING", conf, reasons
    if scores["RANGING"] >= scores["TRENDING"]:
        return "RANGING", conf, reasons
    return "MIXED", 50, reasons

# ═══════════════════════════════════════════════════════════
# [4] TREND STRATEGY — TRENDING regime (pullback mode)
# ═══════════════════════════════════════════════════════════
def check_multi_tf(tf_4h, tf_1h, tf_15m, price):
    """
    SET2 — TREND FOLLOW strategy (ปรับ threshold ให้ trigger ได้)

    SET1 problem: MTF_4H_RSI_BULL=55, BEAR=45 → ต้อง RSI ขีดสูงมาก
    SET2: ผ่อน RSI threshold (50/50) + MACD4h direction เป็น primary bias
    Pullback mode: entry ที่ pullback ถึง 1h EMA21 ไม่ใช่ EMA cross
    """
    S       = INDICATOR_SET
    reasons = []
    tf_bk   = {}

    if not tf_4h or not tf_1h or not tf_15m:
        return False, "N/A", ["ข้อมูล TF ไม่ครบ"], {}

    # ── 4h Bias — ใช้ MACD4h + EMA เป็นหลัก (ไม่ใช่แค่ RSI) ──
    e_s4  = tf_4h.get("ema_short"); e_m4 = tf_4h.get("ema_mid")
    rsi_4 = tf_4h.get("rsi")
    mh_4  = tf_4h.get("macd_hist") or 0
    bias_4h = "NEUTRAL"

    if e_s4 and e_m4 and rsi_4:
        # BULLISH: EMA bull + RSI > 50 (ผ่อนจาก 55) + MACD4h positive
        bull_conditions = [
            e_s4 > e_m4,                            # EMA aligned
            rsi_4 > S["MTF_4H_RSI_BULL"],           # RSI > 50
            price > e_m4,                            # price above mid
        ]
        # BEARISH: EMA bear + RSI < 50 + MACD4h negative
        bear_conditions = [
            e_s4 < e_m4,
            rsi_4 < S["MTF_4H_RSI_BEAR"],           # RSI < 50
            price < e_m4,
        ]

        # [SET2] MACD4h direction เพิ่ม confidence
        macd4_bull = mh_4 > 0
        macd4_bear = mh_4 < 0

        if sum(bull_conditions) >= 2 and macd4_bull:
            bias_4h = "BULLISH"
            reasons.append(f"4h Bias: BULLISH (EMA9>EMA21, RSI={rsi_4:.1f}, MACD4h={mh_4:.1f}+)")
        elif sum(bear_conditions) >= 2 and macd4_bear:
            bias_4h = "BEARISH"
            reasons.append(f"4h Bias: BEARISH (EMA9<EMA21, RSI={rsi_4:.1f}, MACD4h={mh_4:.1f}-)")
        elif sum(bull_conditions) >= 2:
            bias_4h = "BULLISH"  # EMA+RSI align แต่ MACD flat → ยังนับ
            reasons.append(f"4h Bias: BULLISH (EMA+RSI aligned, MACD4h={mh_4:.1f} weak)")
        elif sum(bear_conditions) >= 2:
            bias_4h = "BEARISH"
            reasons.append(f"4h Bias: BEARISH (EMA+RSI aligned, MACD4h={mh_4:.1f} weak)")
        else:
            reasons.append(f"4h Bias: NEUTRAL (RSI={rsi_4:.1f}, MACD4h={mh_4:.1f})")

    tf_bk["4h"] = {"bias": bias_4h, "ema_short": e_s4, "ema_mid": e_m4,
                   "rsi": rsi_4, "macd_hist": mh_4}

    if bias_4h == "NEUTRAL":
        return False, "N/A", reasons, tf_bk

    # ── 1h Direction — RSI threshold ผ่อนลง ─────────────────
    e_s1  = tf_1h.get("ema_short"); e_m1 = tf_1h.get("ema_mid")
    rsi_1 = tf_1h.get("rsi"); mh_1 = tf_1h.get("macd_hist")
    dir_1h = "NEUTRAL"

    if e_s1 and e_m1 and rsi_1:
        if bias_4h == "BULLISH":
            # SET2: ผ่อน RSI threshold เป็น 45 (จาก 50)
            # Pullback mode: ยอมรับ RSI ต่ำหน่อยถ้า EMA ยังถือ
            if e_s1 > e_m1 and rsi_1 > S["MTF_1H_RSI_CONFIRM"]:
                dir_1h = "LONG"
                reasons.append(f"1h Direction: LONG (EMA9>{e_m1:.0f}, RSI={rsi_1:.1f})")
            elif cfg.TREND_USE_PULLBACK_ENTRY and e_s1 > e_m1 * 0.998 and rsi_1 > 40:
                # Pullback mode: price ถอยมาใกล้ EMA21 แต่ยังไม่ cross
                dir_1h = "LONG"
                reasons.append(f"1h Direction: LONG pullback (price near EMA21, RSI={rsi_1:.1f})")
            else:
                reasons.append(f"1h Direction: ไม่ confirm (RSI={rsi_1:.1f}, EMA9/21 ratio={(e_s1/e_m1):.4f})")
        elif bias_4h == "BEARISH":
            if e_s1 < e_m1 and rsi_1 < (100 - S["MTF_1H_RSI_CONFIRM"]):
                dir_1h = "SHORT"
                reasons.append(f"1h Direction: SHORT (EMA9<{e_m1:.0f}, RSI={rsi_1:.1f})")
            elif cfg.TREND_USE_PULLBACK_ENTRY and e_s1 < e_m1 * 1.002 and rsi_1 < 60:
                dir_1h = "SHORT"
                reasons.append(f"1h Direction: SHORT pullback (price near EMA21, RSI={rsi_1:.1f})")
            else:
                reasons.append(f"1h Direction: ไม่ confirm (RSI={rsi_1:.1f})")

    tf_bk["1h"] = {"direction": dir_1h, "ema_short": e_s1, "ema_mid": e_m1,
                   "rsi": rsi_1, "macd_hist": mh_1}

    if dir_1h == "NEUTRAL":
        return False, "N/A", reasons, tf_bk

    # ── 15m Entry Trigger ────────────────────────────────────
    e_s15  = tf_15m.get("ema_short"); e_m15 = tf_15m.get("ema_mid")
    rsi_15 = tf_15m.get("rsi"); mh_15 = tf_15m.get("macd_hist")
    bbp_15 = tf_15m.get("bb_pct"); vr_15 = tf_15m.get("vol_ratio")
    entry_ok = False
    buf = S["EMA_CROSS_BUFFER"]

    if dir_1h == "LONG":
        ema_ok  = bool(e_s15 and e_m15 and e_s15 > e_m15 * (1 + buf))
        rsi_ok  = bool(rsi_15 and rsi_15 < cfg.RSI_OVERBOUGHT)
        if ema_ok and rsi_ok:
            entry_ok = True
            reasons.append(f"15m Entry: LONG ✅ EMA aligned | RSI={rsi_15:.1f} | MACD={mh_15:.2f}")
        else:
            fail = []
            if not ema_ok: fail.append(f"EMA9({e_s15:.0f})<EMA21({e_m15:.0f})" if e_s15 and e_m15 else "EMA missing")
            if not rsi_ok: fail.append(f"RSI={rsi_15:.1f} overbought")
            reasons.append(f"15m Entry: ไม่ผ่าน [{' | '.join(fail)}]")
    elif dir_1h == "SHORT":
        ema_ok  = bool(e_s15 and e_m15 and e_s15 < e_m15 * (1 - buf))
        rsi_ok  = bool(rsi_15 and rsi_15 > cfg.RSI_OVERSOLD)
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
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════
def is_bullish_stack(tf):
    es = tf.get("ema_short") or 0
    em = tf.get("ema_mid")   or 0
    return es > em and es > 0 and em > 0

def is_bearish_stack(tf):
    es = tf.get("ema_short") or 0
    em = tf.get("ema_mid")   or 0
    return es < em and es > 0 and em > 0

def should_block_countertrend_rebound(direction, tf_1h, tf_15m):
    if not cfg.BLOCK_COUNTERTREND:
        return False, ""
    if direction == "Short":
        bull_1h  = is_bullish_stack(tf_1h  or {})
        bull_15m = is_bullish_stack(tf_15m or {})
        if bull_1h and bull_15m:
            r1h = (tf_1h or {}).get("rsi", 50)
            return True, f"COUNTERTREND BLOCK: SHORT แต่ 1h+15m EMA bullish (1h RSI={r1h:.0f})"
    if direction == "Long":
        bear_1h  = is_bearish_stack(tf_1h  or {})
        bear_15m = is_bearish_stack(tf_15m or {})
        if bear_1h and bear_15m:
            r1h = (tf_1h or {}).get("rsi", 50)
            return True, f"COUNTERTREND BLOCK: LONG แต่ 1h+15m EMA bearish (1h RSI={r1h:.0f})"
    return False, ""

# ═══════════════════════════════════════════════════════════
# [3] SHORT PRE-FILTER — เปิดทาง short อย่างสมเหตุสมผล
# ═══════════════════════════════════════════════════════════
def should_block_short_set2(tf_4h, tf_1h, rb_data):
    """
    SET2 Short Gate — กัน false short ที่ Claude reject ทุกครั้ง

    ผ่านได้ต้องตรง ALL:
      1. 4h MACD < 0 (ตลาดโดยรวมลง)
      2. rsi_turning_down = True (momentum กำลัง roll over)
      3. short_score >= REBOUND_MIN_SCORE_FOR_CLAUDE

    Block ถ้า:
      - 1h MACD > SHORT_BLOCK_1H_MACD_STRONG (strong uptrend → Claude reject แน่)

    Returns: (should_block: bool, reason: str)
    """
    mh_4  = (tf_4h or {}).get("macd_hist") or 0
    mh_1  = (tf_1h or {}).get("macd_hist") or 0
    rsi_1 = (tf_1h or {}).get("rsi") or 50

    rsi_turn_dn  = rb_data.get("rsi_turning_down", False)
    short_score  = rb_data.get("short_score", 0)

    # Block: 4h ยังไม่ bearish
    if cfg.SHORT_REQUIRE_4H_BEARISH and mh_4 >= 0:
        return True, f"SHORT BLOCK: 4h MACD={mh_4:.1f} ≥0 (ยังไม่ bearish)"

    # Block: 1h MACD แรงมาก (Claude จะบอก "uptrend structure" แน่)
    if mh_1 > cfg.SHORT_BLOCK_1H_MACD_STRONG:
        return True, f"SHORT BLOCK: 1h MACD={mh_1:.1f} > {cfg.SHORT_BLOCK_1H_MACD_STRONG} (strong uptrend)"

    # Block: ไม่มี RSI turning down
    if cfg.SHORT_REQUIRE_RSI_TURN_DOWN and not rsi_turn_dn:
        return True, f"SHORT BLOCK: rsi_turning_down=False (ต้องการ momentum reversal)"

    # Block: score ต่ำเกิน
    if short_score < cfg.REBOUND_MIN_SCORE_FOR_CLAUDE:
        return True, f"SHORT BLOCK: short_score={short_score} < {cfg.REBOUND_MIN_SCORE_FOR_CLAUDE}"

    # Block: 1h RSI สูงเกิน threshold (overbought แต่ยังไม่ roll)
    if rsi_1 > cfg.SHORT_REQUIRE_1H_RSI_MAX:
        return True, f"SHORT BLOCK: 1h RSI={rsi_1:.1f} > {cfg.SHORT_REQUIRE_1H_RSI_MAX} (overbought แต่ EMA ยัง bull)"

    return False, ""

# ═══════════════════════════════════════════════════════════
# [2] REBOUND SCORING — calibrated weights
# ═══════════════════════════════════════════════════════════
def check_rebound_30m(tf_30m, tf_1h, price, change_24h=0,
                      funding_rate=None, rsi_series=None, tf_5m=None, tf_4h=None, tf_15m=None):
    """
    SET2 — Rebound Hunt พร้อม calibrated weights

    Changes from SET1:
      - rsi_turning_up/down weight: +2 → +3 (top differentiator)
      - เพิ่ม pre-filter: margin<REBOUND_MIN_MARGIN → return False (no Claude call)
      - REBOUND_MAX_SCORE = 16 (ปรับให้สอดคล้อง)
    """
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

    # ── 1h Bias check ─────────────────────────────────────────
    rsi_1h = tf_1h.get("rsi") if tf_1h else None
    if rsi_1h:
        guard = cfg.REBOUND_1H_RSI_GUARD
        if rsi_1h > 75 or rsi_1h < guard:
            reasons.append(f"1h RSI extreme ({rsi_1h:.1f}) — งด Rebound")
            return False, "N/A", reasons, {}

    # ── 4h RSI Direction Guard ────────────────────────────────
    rsi_4h = tf_4h.get("rsi") if tf_4h else None
    block_short_4h = rsi_4h and rsi_4h < 35
    block_long_4h  = rsi_4h and rsi_4h > 65

    # ── RSI Momentum Turn Detection ───────────────────────────
    rsi_turning_up   = False
    rsi_turning_down = False
    if rsi_series and len(rsi_series) >= 3:
        r_now  = rsi_series[-1]
        r_prev = rsi_series[-3]
        if r_now and r_prev:
            # [SET2] เพิ่ม threshold เล็กน้อย (+1.5 แทน +1 เพื่อลด noise)
            if r_now > r_prev + 1.5 and r_now < 52:
                rsi_turning_up = True
            if r_now < r_prev - 1.5 and r_now > 48:
                rsi_turning_down = True

    # ── Funding Rate Bonus ────────────────────────────────────
    funding_bonus = 0
    if funding_rate is not None:
        if funding_rate < cfg.FUNDING_BONUS_HIGH:
            funding_bonus = 2
        elif funding_rate < cfg.FUNDING_BONUS_MED:
            funding_bonus = 1

    # ── Large Drop Bonus ──────────────────────────────────────
    drop_bonus = 0
    if change_24h < cfg.DROP_BONUS_HIGH:
        drop_bonus = 2
    elif change_24h < cfg.DROP_BONUS_MED:
        drop_bonus = 1

    # ── 5m Micro Entry Check ──────────────────────────────────
    micro_long_ok  = False
    micro_short_ok = False
    if tf_5m:
        rsi_5m = tf_5m.get("rsi", 50)
        mh_5m  = tf_5m.get("macd_hist", 0) or 0
        if rsi_5m and rsi_5m > 20 and mh_5m > -0.001:
            micro_long_ok = True
        if rsi_5m and rsi_5m < 80 and mh_5m < 0.001:
            micro_short_ok = True

    RSI_TURN_W = cfg.REBOUND_RSI_TURN_WEIGHT   # = 3 (จาก 2)

    # ══ LONG Rebound Scoring ══════════════════════════════════
    long_score = 0; long_reasons = []

    if bbp is not None and bbp < cfg.REBOUND_BB_LOW:      # < 0.30
        long_score += 2
        long_reasons.append(f"BB%B={bbp:.2f} ใกล้ lower band (+2)")

    if rsi and rsi < cfg.RSI_OVERSOLD:                     # < 32
        long_score += 2
        long_reasons.append(f"RSI={rsi:.1f} Oversold (+2)")

    if sl_val and abs(price - sl_val) / sl_val < buf:
        long_score += 2
        long_reasons.append(f"ใกล้ Swing Low ${sl_val:.2f} (+2)")

    if mh is not None and mh > -0.0001:
        long_score += 1
        long_reasons.append(f"MACD hist={mh:.4f} turning up (+1)")

    if vr and vr > cfg.REBOUND_VOL_MIN:
        long_score += 1
        long_reasons.append(f"Volume={vr:.1f}x avg (+1)")

    # [SET2] rsi_turning_up weight = 3 (จาก 2) — top predictor
    if rsi_turning_up:
        long_score += RSI_TURN_W
        long_reasons.append(f"RSI momentum turning up (+{RSI_TURN_W}) ★")

    if funding_bonus > 0:
        long_score += funding_bonus
        long_reasons.append(f"Funding={funding_rate:+.4f}% → squeeze risk (+{funding_bonus})")

    if drop_bonus > 0:
        long_score += drop_bonus
        long_reasons.append(f"24h drop={change_24h:.1f}% → flush (+{drop_bonus})")

    if micro_long_ok:
        long_score += 1
        long_reasons.append(f"5m micro entry OK (+1)")

    # ══ SHORT Rebound Scoring ═════════════════════════════════
    short_score = 0; short_reasons = []

    if bbp is not None and bbp > cfg.REBOUND_BB_HIGH:     # > 0.70
        short_score += 2
        short_reasons.append(f"BB%B={bbp:.2f} ใกล้ upper band (+2)")

    if rsi and rsi > cfg.RSI_OVERBOUGHT:                   # > 68
        short_score += 2
        short_reasons.append(f"RSI={rsi:.1f} Overbought (+2)")

    if sh and abs(price - sh) / sh < buf:
        short_score += 2
        short_reasons.append(f"ใกล้ Swing High ${sh:.2f} (+2)")

    if mh is not None and mh < 0.0001:
        short_score += 1
        short_reasons.append(f"MACD hist={mh:.4f} turning down (+1)")

    if vr and vr > cfg.REBOUND_VOL_MIN:
        short_score += 1
        short_reasons.append(f"Volume={vr:.1f}x avg (+1)")

    # [SET2] rsi_turning_down weight = 3 (จาก 2)
    if rsi_turning_down:
        short_score += RSI_TURN_W
        short_reasons.append(f"RSI momentum turning down (+{RSI_TURN_W}) ★")

    if funding_rate is not None and funding_rate > 0.03:
        short_score += 2
        short_reasons.append(f"Funding={funding_rate:+.4f}% → long squeeze risk (+2)")
    elif funding_rate is not None and funding_rate > 0.01:
        short_score += 1
        short_reasons.append(f"Funding={funding_rate:+.4f}% → longs dominant (+1)")

    if change_24h > 5.0:
        short_score += 2
        short_reasons.append(f"24h rise={change_24h:.1f}% → overextended (+2)")
    elif change_24h > 3.0:
        short_score += 1
        short_reasons.append(f"24h rise={change_24h:.1f}% → possible fade (+1)")

    if micro_short_ok:
        short_score += 1
        short_reasons.append(f"5m micro entry OK (+1)")

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
    }

    min_score = cfg.REBOUND_MIN_SCORE  # = 3

    # ── LONG gate ─────────────────────────────────────────────
    if long_score >= min_score and long_score > short_score:
        if block_long_4h:
            reasons.append(f"⚠️ 4h RSI={rsi_4h:.1f} overbought — งด LONG rebound")
            return False, "N/A", reasons, rb_data

        # [SET2] Margin filter ก่อน return — margin ต่ำ → weak signal
        margin = long_score - short_score
        if margin < cfg.REBOUND_MIN_MARGIN:
            reasons.append(
                f"LONG margin={margin} < {cfg.REBOUND_MIN_MARGIN} (long={long_score}, short={short_score})"
                f" — WEAK_MARGIN_BLOCK"
            )
            return False, "N/A", reasons, rb_data

        reasons = [f"REBOUND LONG: {r}" for r in long_reasons]
        return True, "Long", reasons, rb_data

    # ── SHORT gate ────────────────────────────────────────────
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
              and s.get("strategy_used") == "REBOUND"
              and s.get("verdict") in ("WEAK SIGNAL", "FILTERED")]
    if len(recent) >= window:
        return cfg.PERSISTENCE_BONUS
    return 0

# ═══════════════════════════════════════════════════════════
# FETCH MARKET (เหมือน SET1 — ไม่เปลี่ยน)
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# MARKET CONTEXT — Binance Futures microstructure (SET2v2)
# ═══════════════════════════════════════════════════════════
# Conservative phase design:
#   - Negative penalty → apply เสมอ (safe: ลด noise signals)
#   - Positive bonus Short → apply (เปิดทาง short path)
#   - Positive bonus Long → กักไว้ (รอ 50+ signals validate)
#   - Volume filter → hard block ก่อน Claude ถ้า vol < 0.10
#   - OI ลด → negative only (ไม่รู้ direction จาก OI ขึ้น)
#   - ทุก fetch wrapped try/except → fallback {} ถ้าล้มเหลว

def fetch_market_context(symbol):
    """
    ดึง Binance Futures microstructure data:
      - L/S Account Ratio (crowd positioning)
      - Taker Buy/Sell Volume (aggressive order flow)
      - Open Interest Change (position building/closing)

    Returns dict หรือ {} ถ้า API ล้มเหลวทั้งหมด
    Timeout: 8s ต่อ endpoint, ไม่ block main flow
    """
    ctx = {}

    # ── L/S Account Ratio ──────────────────────────────────
    try:
        ls_data = http_get(
            f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
            f"?symbol={symbol}&period=5m&limit=2",
            timeout=8
        )
        if ls_data and isinstance(ls_data, list) and len(ls_data) >= 1:
            latest = ls_data[-1]
            ls_ratio   = float(latest.get("longShortRatio", 1.0))
            long_pct   = float(latest.get("longAccount",    0.5)) * 100
            short_pct  = 100 - long_pct
            ctx.update({
                "ls_ratio":    round(ls_ratio, 4),
                "ls_long_pct": round(long_pct, 2),
                "ls_short_pct":round(short_pct, 2),
            })
    except Exception as e:
        log(f"  ⚠️ market_ctx L/S fetch failed: {e}")

    # ── Taker Buy/Sell Ratio ───────────────────────────────
    try:
        tk_data = http_get(
            f"https://fapi.binance.com/futures/data/takerlongshortRatio"
            f"?symbol={symbol}&period=5m&limit=3",
            timeout=8
        )
        if tk_data and isinstance(tk_data, list) and len(tk_data) >= 1:
            # ใช้ rolling avg 3 periods ลด noise จาก spike
            buy_pcts  = [float(d.get("buySellRatio", 0.5)) for d in tk_data]
            buy_ratio = sum(buy_pcts) / len(buy_pcts)
            buy_pct   = buy_ratio / (1 + buy_ratio) * 100
            sell_pct  = 100 - buy_pct
            ctx.update({
                "taker_buy_pct":  round(buy_pct, 2),
                "taker_sell_pct": round(sell_pct, 2),
                "taker_ratio":    round(buy_ratio, 4),
            })
    except Exception as e:
        log(f"  ⚠️ market_ctx Taker fetch failed: {e}")

    # ── Open Interest History ──────────────────────────────
    try:
        oi_data = http_get(
            f"https://fapi.binance.com/futures/data/openInterestHist"
            f"?symbol={symbol}&period=5m&limit=6",
            timeout=8
        )
        if oi_data and isinstance(oi_data, list) and len(oi_data) >= 2:
            oi_now   = float(oi_data[-1].get("sumOpenInterest", 0))
            oi_old   = float(oi_data[0].get("sumOpenInterest",  0))
            oi_chg   = ((oi_now - oi_old) / oi_old * 100) if oi_old > 0 else 0
            ctx.update({
                "oi_now":        round(oi_now, 2),
                "oi_change_pct": round(oi_chg, 4),
            })
    except Exception as e:
        log(f"  ⚠️ market_ctx OI fetch failed: {e}")

    if cfg.CTX_LOG_UNAVAILABLE and not ctx:
        log(f"  ⚠️ market_ctx: all endpoints failed — scoring without context")
    return ctx


def calc_market_context_bonus(ctx, direction):
    """
    คำนวณ ctx_bonus จาก market microstructure
    Returns: (bonus: int, reasons: list[str])

    Conservative phase policy (config-driven):
      CTX_APPLY_NEGATIVE_LONG  = True  → ลด Long ที่ crowded
      CTX_APPLY_NEGATIVE_SHORT = True  → ลด Short ที่ shorts crowded
      CTX_APPLY_POSITIVE_SHORT = True  → เพิ่ม Short ที่มี squeeze setup
      CTX_APPLY_POSITIVE_LONG  = False → รอ data validate ก่อน
    """
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

    # ── L/S Ratio ─────────────────────────────────────────
    if ls is not None:
        if dir_up == "LONG":
            if ls < S["CTX_LS_SHORTS_EXTREME"]:       # < 0.75
                raw = +3
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} shorts extreme ({S['CTX_LS_SHORTS_EXTREME']}) → squeeze +3")
                else:
                    reasons.append(f"L/S={ls:.3f} shorts extreme (positive bonus Long deferred)")
            elif ls < S["CTX_LS_SHORTS_HIGH"]:         # < 0.90
                raw = +2
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} shorts high → bounce +2")
                else:
                    reasons.append(f"L/S={ls:.3f} shorts high (positive bonus Long deferred)")
            elif ls > S["CTX_LS_LONGS_EXTREME"]:       # > 1.40
                raw = -3
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} longs extreme → liquidation risk {raw}")
            elif ls > S["CTX_LS_LONGS_HIGH"]:          # > 1.20
                raw = -2
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} longs crowded → weak long {raw}")

        elif dir_up == "SHORT":
            if ls > S["CTX_LS_LONGS_EXTREME"]:         # > 1.40
                raw = +3
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} longs extreme → short squeeze +3")
            elif ls > S["CTX_LS_LONGS_HIGH"]:          # > 1.20
                raw = +2
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} longs high → short bounce +2")
            elif ls < S["CTX_LS_SHORTS_EXTREME"]:      # < 0.75
                raw = -2
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} shorts extreme → risky short {raw}")
            elif ls < S["CTX_LS_SHORTS_HIGH"]:         # < 0.90
                raw = -1
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus += raw
                    reasons.append(f"L/S={ls:.3f} shorts crowded → weak short {raw}")

    # ── Taker Volume (rolling avg — น้อย noise กว่า spot) ─
    if t_buy is not None and t_sell is not None:
        if dir_up == "LONG":
            if t_buy >= S["CTX_TAKER_AGGRESSIVE"]:     # >= 58
                raw = +2
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += raw
                    reasons.append(f"Taker buy={t_buy:.1f}% aggressive → Long +2")
                else:
                    reasons.append(f"Taker buy={t_buy:.1f}% aggressive (positive Long deferred)")
            elif t_buy >= S["CTX_TAKER_MODERATE"]:     # >= 53
                raw = +1
                if cfg.CTX_APPLY_POSITIVE_LONG:
                    bonus += raw
                    reasons.append(f"Taker buy={t_buy:.1f}% moderate → Long +1")
                else:
                    reasons.append(f"Taker buy={t_buy:.1f}% moderate (positive Long deferred)")
            elif t_sell >= S["CTX_TAKER_AGGRESSIVE"]:  # >= 58
                raw = -2
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus += raw
                    reasons.append(f"Taker sell={t_sell:.1f}% aggressive → Long {raw}")
            elif t_sell >= S["CTX_TAKER_MODERATE"]:    # >= 53
                raw = -1
                if cfg.CTX_APPLY_NEGATIVE_LONG:
                    bonus += raw
                    reasons.append(f"Taker sell={t_sell:.1f}% moderate → Long {raw}")

        elif dir_up == "SHORT":
            if t_sell >= S["CTX_TAKER_AGGRESSIVE"]:
                raw = +2
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += raw
                    reasons.append(f"Taker sell={t_sell:.1f}% aggressive → Short +2")
            elif t_sell >= S["CTX_TAKER_MODERATE"]:
                raw = +1
                if cfg.CTX_APPLY_POSITIVE_SHORT:
                    bonus += raw
                    reasons.append(f"Taker sell={t_sell:.1f}% moderate → Short +1")
            elif t_buy >= S["CTX_TAKER_AGGRESSIVE"]:
                raw = -2
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus += raw
                    reasons.append(f"Taker buy={t_buy:.1f}% aggressive → Short {raw}")
            elif t_buy >= S["CTX_TAKER_MODERATE"]:
                raw = -1
                if cfg.CTX_APPLY_NEGATIVE_SHORT:
                    bonus += raw
                    reasons.append(f"Taker buy={t_buy:.1f}% moderate → Short {raw}")

    # ── OI Change — negative only (direction-agnostic) ────
    if oi_ch is not None:
        if oi_ch < S["CTX_OI_DROP_STRONG"]:            # < -2.0%
            bonus  += -2
            reasons.append(f"OI drop={oi_ch:.2f}% strong close-out → momentum weak -2")
        elif oi_ch < S["CTX_OI_DROP_MILD"]:            # < -1.0%
            bonus  += -1
            reasons.append(f"OI drop={oi_ch:.2f}% mild close-out → caution -1")
        elif oi_ch > 1.0:
            reasons.append(f"OI rise={oi_ch:.2f}% — direction unknown, neutral")

    # ── Cap ───────────────────────────────────────────────
    bonus = max(-S["CTX_BONUS_MAX"], min(S["CTX_BONUS_MAX"], bonus))

    if not reasons:
        reasons.append("market_ctx neutral — no bonus")
    return bonus, reasons


def is_market_context_aligned(ctx, direction):
    """
    ตรวจว่า market context สนับสนุน direction หรือขัดแย้ง
    Returns True ถ้า: support >= 1 AND oppose == 0

    ใช้สำหรับ: Claude gate reduction (55→52) เมื่อ ctx aligned
    ไม่ใช้สำหรับ: auto-approve (เสี่ยงเกิน)
    """
    if not ctx:
        return False

    ls    = ctx.get("ls_ratio")
    t_buy = ctx.get("taker_buy_pct")
    t_sell= ctx.get("taker_sell_pct")
    dir_up= direction.upper()

    support = 0
    oppose  = 0

    if ls is not None:
        if dir_up == "LONG":
            if ls < 0.90:  support += 1
            if ls > 1.20:  oppose  += 1
        elif dir_up == "SHORT":
            if ls > 1.10:  support += 1
            if ls < 0.80:  oppose  += 1

    if t_buy is not None and t_sell is not None:
        if dir_up == "LONG":
            if t_buy  >= 53: support += 1
            if t_buy  <= 45: oppose  += 1
        elif dir_up == "SHORT":
            if t_sell >= 53: support += 1
            if t_buy  >= 55: oppose  += 1

    return support >= 1 and oppose == 0


def fetch_market(symbol):
    ticker = None
    for attempt in range(3):
        ticker = http_get(
            f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}",
            timeout=20
        )
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
    k4h = get_klines(symbol, "4h",  150)

    tf_5m  = calc_tf_data(k5)  if len(k5)  >= 30 else None
    tf_15m = calc_tf_data(k15) if len(k15) >= 30 else None
    tf_30m = calc_tf_data(k30) if len(k30) >= 30 else None
    tf_1h  = calc_tf_data(k1h) if len(k1h) >= 30 else None
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
        fd = http_get(
            f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
            timeout=8
        )
        if fd and "lastFundingRate" in fd:
            funding_rate = round(float(fd["lastFundingRate"]) * 100, 4)
    except:
        pass

    rsi_series_30m = None
    if len(k30) >= 35:
        closes = [float(k[4]) for k in k30]
        try:
            rsi_series_30m = [
                calc_rsi(closes[:i+1]) for i in range(len(closes)-3, len(closes))
            ]
        except:
            pass

    log(f"✅ fetch_market: {symbol} ${price:,.2f} | 5m={bool(tf_5m)} 1h={bool(tf_1h)} 4h={bool(tf_4h)} | funding={funding_rate}%")

    # ── Market Context (SET2v2) ───────────────────────────────
    market_ctx = fetch_market_context(symbol)
    if market_ctx:
        log(f"  📡 market_ctx: L/S={market_ctx.get('ls_ratio','?')} "
            f"taker_buy={market_ctx.get('taker_buy_pct','?')}% "
            f"OI_chg={market_ctx.get('oi_change_pct','?')}%")

    return {
        "symbol": symbol, "price": price,
        "high": high, "low": low, "change": change,
        "volume": volume, "atr": atr,
        "tf_5m":  tf_5m,
        "tf_15m": tf_15m,
        "tf_30m": tf_30m,
        "tf_1h":  tf_1h,
        "tf_4h":  tf_4h,
        "entry_tf":       entry_tf_used,
        "funding_rate":   funding_rate,
        "rsi_series_30m": rsi_series_30m,
        "market_ctx":     market_ctx,       # SET2v2: microstructure data
    }

# ═══════════════════════════════════════════════════════════
# CLAUDE PROMPT — SET2 (อัพเดต context)
# ═══════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are a compact crypto futures signal validator (SET2v2).

Bot has filtered and scored signals. Your job: validate gray-zone setups ONLY.

RULES:
- EMA 9/21/200 | Binance Futures | 15m entry, 1h+4h confirmation
- Min R:R 1.5 | Hard SL risk <= 8% | Reject if > 12%
- VOLATILE regime → always NO_TRADE
- For REBOUND LONG: accept bearish 4h MACD if rsi_turn_up=true (counter-trend rebound is valid)
- For REBOUND SHORT: require 4h bearish + rsi_turn_down=true + short_score>=7
- For TREND_FOLLOW: confirm HTF alignment before entry
- Countertrend without rejection confirmation → REJECTED

MARKET CONTEXT (use as supporting evidence, not primary signal):
- ctx.ls_ratio < 0.85 → shorts overcrowded → bounce potential supports Long
- ctx.ls_ratio > 1.15 → longs overcrowded → squeeze potential supports Short
- ctx.taker_buy_pct > 55 → aggressive buyers → favors Long momentum
- ctx.taker_sell_pct > 55 → aggressive sellers → favors Short momentum
- ctx.oi_change_pct < -1.0 → positions closing → momentum weakening, raise caution
- ctx_bonus shown in payload = net microstructure score already applied to pre_conf

VOLUME WARNING: if vol_ratio_15m < 0.10, flag as liquidity_risk in risk_flags.

Respond ONLY with valid JSON, no markdown, no preamble:
{
  "verdict": "APPROVED|WEAK_APPROVAL|REJECTED|NO_TRADE",
  "confidence": 0-100,
  "reason_code": "TREND_ALIGNED|MOMENTUM_CONFIRM|STRUCTURE_VALID|RR_OK|COUNTERTREND|WEAK_MOMENTUM|INVALID_STRUCTURE|RR_FAIL|HTF_CONFLICT|VOLATILE_REGIME|FUNDING_RISK|CTX_CONFLICT|INSUFFICIENT_DATA",
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
        if delay:
            time.sleep(delay)
        result = http_post("https://api.anthropic.com/v1/messages", headers, body)
        if not result:
            last_error = RuntimeError(f"Claude transport error (attempt {attempt}/3)")
            continue
        if "error" in result:
            msg  = result["error"].get("message") or "Unknown Claude error"
            last_error = RuntimeError(f"Claude: {msg}")
            low  = msg.lower()
            if any(x in low for x in ("rate limit","overloaded","timeout","tempor","try again","529","502","503","504")):
                if attempt < 3:
                    continue
            raise last_error
        try:
            return result["content"][0]["text"]
        except Exception:
            last_error = RuntimeError(f"Claude malformed response (attempt {attempt}/3)")
    raise last_error or RuntimeError("No response from Claude")

def build_summary(m, regime, strategy, tf_bk, rb_data=None, direction=None):
    t15 = m["tf_15m"] or {}
    t1h = m["tf_1h"]  or {}
    t4h = m["tf_4h"]  or {}

    def r2(v): return round(v, 2) if v is not None else None

    payload = {
        "symbol":    m["symbol"],
        "direction": direction or "N/A",
        "price":     r2(m["price"]),
        "change_24h":r2(m.get("change", 0)),
        "regime":    regime,
        "strategy":  strategy,
        "funding":   m.get("funding_rate"),
        "bot":       "SET2v1",
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
            "15m": {
                "ema9":  r2(t15.get("ema_short")), "ema21": r2(t15.get("ema_mid")),
                "ema200":r2(t15.get("ema_long")),  "rsi":   r2(t15.get("rsi")),
                "macd_h":r2(t15.get("macd_hist")), "bb_pct":r2(t15.get("bb_pct")),
                "vol_ratio": r2(t15.get("vol_ratio")),
            },
        },
    }
    if strategy == "REBOUND" and rb_data:
        payload["rebound"] = {
            "bb_pct":      rb_data.get("bb_pct"),
            "rsi_30m":     rb_data.get("rsi"),
            "macd_h_30m":  rb_data.get("macd_hist"),
            "vol_ratio":   rb_data.get("vol_ratio"),
            "long_score":  rb_data.get("long_score"),
            "short_score": rb_data.get("short_score"),
            "max_score":   cfg.REBOUND_MAX_SCORE,
            "rsi_turn_up": rb_data.get("rsi_turning_up", False),
            "rsi_turn_dn": rb_data.get("rsi_turning_down", False),
            "swing_high":  r2(rb_data.get("swing_high")),
            "swing_low":   r2(rb_data.get("swing_low")),
            "margin":      rb_data.get("long_score",0) - rb_data.get("short_score",0)
                           if direction == "Long"
                           else rb_data.get("short_score",0) - rb_data.get("long_score",0),
        }
    if tf_bk:
        payload["tf_breakdown"] = tf_bk

    # ── Market Context (SET2v2) — compact subset ลด tokens ──
    ctx = m.get("market_ctx") or {}
    if ctx:
        payload["ctx"] = {
            "ls_ratio":       ctx.get("ls_ratio"),
            "taker_buy_pct":  ctx.get("taker_buy_pct"),
            "taker_sell_pct": ctx.get("taker_sell_pct"),
            "oi_change_pct":  ctx.get("oi_change_pct"),
            "ctx_bonus":      m.get("ctx_bonus", 0),       # bonus ที่ apply แล้ว
            "ctx_aligned":    m.get("ctx_aligned", False),
            "ctx_reasons":    (m.get("ctx_reasons") or [])[:3],
        }
    if m.get("pre_conf") is not None:
        payload["pre_conf"] = m.get("pre_conf")
    if m.get("effective_claude_min") is not None:
        payload["effective_claude_min"] = m.get("effective_claude_min")
    # ── Volume warning ────────────────────────────────────────
    t15_vr = (m.get("tf_15m") or {}).get("vol_ratio")
    if t15_vr is not None and t15_vr < cfg.CTX_VOL_MIN_FOR_SIGNAL:
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
                "APPROVED": "APPROVED", "REJECTED": "REJECTED", "NO TRADE": "NO TRADE",
                "NO_TRADE": "NO TRADE",
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
    # Fallback regex
    verdict = "REJECTED"
    for v in ["APPROVED", "WEAK APPROVAL", "NO TRADE", "REJECTED"]:
        if v in text.upper():
            verdict = v; break
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

def _fmt_regime(regime):
    return regime or "—"

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
    rendered = fmt(value) if value is not None else "—"
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

def _has_required_trade_fields(sig):
    return bool(
        sig.get("symbol")
        and sig.get("direction")
        and sig.get("entry")
        and sig.get("tp1")
        and (sig.get("hsl") or sig.get("ssl"))
    )

def send_telegram(sig):
    token   = getattr(cfg, "TELEGRAM_TOKEN", None) or getattr(cfg, "TG_BOT_TOKEN", "")
    chat_id = getattr(cfg, "TELEGRAM_CHAT_ID", None) or getattr(cfg, "TG_CHAT_ID", "")
    if not token or not chat_id or "YOUR" in token:
        log("  Telegram skipped: token/chat_id not configured")
        return False

    verdict = sig.get("verdict", "")
    conf_for_alert = sig.get("conf") or 0
    if not _has_required_trade_fields(sig):
        log(f"  Telegram blocked: missing trade levels for verdict={verdict}")
        return False

    if verdict == "REJECTED":
        if conf_for_alert < 50:
            log(f"  Telegram blocked: rejected conf={conf_for_alert}<50")
            return False
        return _send_telegram_rejected(sig, token, chat_id)

    if verdict not in ("APPROVED", "WEAK APPROVAL"):
        log(f"  Telegram blocked: verdict={verdict}")
        return False

    verdict = sig["verdict"]
    sym = sig["symbol"].replace("USDT", "")
    direction = sig["direction"]
    title = ("🟢 LONG" if direction == "Long" else "🔴 SHORT") + f" {sym}/USDT | Conf: {sig.get('conf', '—')}/100"
    gate = sig.get("gate_path", "—")
    regime = sig.get("regime", "—")
    strat = sig.get("strategy_used", "—")
    ind = sig.get("indicators", {})
    t15 = ind.get("15m", {})
    fr = sig.get("funding_rate") or (sig.get("indicators") or {}).get("funding_rate")
    badge = "APPROVED SIGNAL" if verdict == "APPROVED" else "WEAK APPROVAL"
    subtitle = "trade ได้ แต่ควรลด size หรือรอ confirmation เพิ่ม" if verdict == "WEAK APPROVAL" else None

    msg  = _tg_header(title, badge, subtitle)
    msg += f"🕐 {now_thai().strftime('%H:%M')} TH\n"
    msg += f"📍 {_tg_escape(_fmt_regime(regime))} | Strategy: {_tg_escape(strat)}\n"
    msg += f"🔀 Gate: {_tg_escape(gate)}\n\n"
    msg += _tg_level_line("💰", "Entry", sig.get("entry"), sig.get("entry"), direction, "(ตลาด)")
    msg += _tg_level_line("🎯", "TP1", sig.get("tp1"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP2", sig.get("tp2"), sig.get("entry"), direction)
    msg += _tg_level_line("🎯", "TP3", sig.get("tp3"), sig.get("entry"), direction)
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
    return _tg_send(token, chat_id, msg)

def _send_telegram_rejected(sig, token, chat_id):
    sym = sig["symbol"].replace("USDT", "")
    direction = sig["direction"]
    title = ("🟢 LONG" if direction == "Long" else "🔴 SHORT") + f" {sym}/USDT | Conf: {sig.get('conf', '—')}/100"
    gate = sig.get("gate_path", "—")
    regime = sig.get("regime", "—")
    strat = sig.get("strategy_used", "—")
    rc = sig.get("reason_code") or "—"
    reason = sig.get("reject_reason") or sig.get("reason") or "Rejected by validation"
    ind = sig.get("indicators", {})
    t15 = ind.get("15m", {})
    fr = sig.get("funding_rate") or (sig.get("indicators") or {}).get("funding_rate")

    msg  = _tg_header(title, "REJECTED")
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
    msg += _tg_rsi_line(ind)
    msg += f"📈 MACD hist 15m: {t15.get('macd_hist', '—')}\n"
    if t15.get("vol_ratio"):
        msg += f"📦 Volume: {t15['vol_ratio']:.1f}x avg\n"
    if fr is not None:
        msg += f"💸 Funding: {fr:+.4f}%\n"
    msg += f"\n💬 {_tg_escape(reason, 320)}\n"
    msg += "\n━━━━━━"
    return _tg_send(token, chat_id, msg)

def should_notify(verdict):
    n = getattr(cfg, "NOTIFY_ON", "approved_weak")
    if n == "all": return True
    if n == "approved_weak": return verdict in ("APPROVED", "WEAK APPROVAL", "REJECTED")
    if n == "approved_only": return verdict == "APPROVED"
    return False

# ═══════════════════════════════════════════════════════════
# [2] PRE-FILTER — กัน wasted Claude calls ก่อน Tier 4
# ═══════════════════════════════════════════════════════════
def should_skip_claude(direction, rb_data, tf_1h, tf_4h, pre_conf, market_ctx=None, tf_15m=None):
    """
    SET2v2 Pre-filter ก่อนเรียก Claude
    Returns (should_skip: bool, reason: str)

    เพิ่มจาก SET2v1:
      - Volume filter: vol_ratio < 0.10 → block (liquidity risk)
      - Market context hard block: OI drop strong + taker ขัด direction
    """
    long_score  = rb_data.get("long_score", 0)
    short_score = rb_data.get("short_score", 0)
    margin = (long_score - short_score) if direction == "Long" else (short_score - long_score)

    # ── [NEW SET2v2] Volume filter ─────────────────────────
    # จากภาพ signal 1: vol_ratio=0.01 → liquidity risk สูงมาก แต่ยังส่ง Claude
    vol_15m = (tf_15m or {}).get("vol_ratio") if tf_15m else rb_data.get("vol_ratio")
    if vol_15m is not None and vol_15m < cfg.CTX_VOL_MIN_FOR_SIGNAL:
        return True, f"VOL_CRITICALLY_LOW: vol_ratio={vol_15m:.3f} < {cfg.CTX_VOL_MIN_FOR_SIGNAL}"

    # ── [NEW SET2v2] Market context hard block ─────────────
    # ถ้า OI ลงแรง + taker ขัด direction อย่างชัดเจน → momentum ไม่มี
    if market_ctx:
        oi_ch  = market_ctx.get("oi_change_pct", 0) or 0
        t_buy  = market_ctx.get("taker_buy_pct",  50) or 50
        t_sell = market_ctx.get("taker_sell_pct", 50) or 50
        # Long + OI drop strong + aggressive sellers → momentum ตรงข้าม
        if direction == "Long" and oi_ch < cfg.CTX_OI_DROP_STRONG and t_sell >= cfg.CTX_TAKER_AGGRESSIVE:
            return True, f"CTX_BLOCK_LONG: OI={oi_ch:.2f}%+taker_sell={t_sell:.1f}% → momentum bearish"
        # Short + OI drop strong + aggressive buyers → momentum ตรงข้าม
        if direction == "Short" and oi_ch < cfg.CTX_OI_DROP_STRONG and t_buy >= cfg.CTX_TAKER_AGGRESSIVE:
            return True, f"CTX_BLOCK_SHORT: OI={oi_ch:.2f}%+taker_buy={t_buy:.1f}% → momentum bullish"

    # Short — ใช้ gate เฉพาะ
    if direction == "Short":
        blocked, reason = should_block_short_set2(tf_4h, tf_1h, rb_data)
        if blocked:
            return True, reason
        return False, ""

    # Long
    if direction == "Long":
        rsi_turn_up = rb_data.get("rsi_turning_up", False)
        score = long_score
        if not rsi_turn_up and score < cfg.REBOUND_MIN_SCORE_FOR_CLAUDE:
            return True, f"LONG PRE-FILTER: no rsi_turn_up + score={score}<{cfg.REBOUND_MIN_SCORE_FOR_CLAUDE}"
        if margin < cfg.REBOUND_MIN_MARGIN:
            return True, f"LONG PRE-FILTER: margin={margin}<{cfg.REBOUND_MIN_MARGIN}"

    return False, ""

# ═══════════════════════════════════════════════════════════
# MAIN — SET2 Flow
# ═══════════════════════════════════════════════════════════
def build_signal(symbol, direction, m, verdict, conf, levels,
                 ai_text, regime, regime_conf, regime_reasons,
                 strategy, filter_reason, tf_bk, rb_data,
                 gate_path="UNKNOWN", pre_conf=None, claude_called=False):
    t15 = m.get("tf_15m") or {}
    t30 = m.get("tf_30m") or {}
    t1h = m.get("tf_1h")  or {}
    t4h = m.get("tf_4h")  or {}

    is_tradeable  = verdict in ("APPROVED", "WEAK APPROVAL")
    score_for_levels = max((conf or 0), (pre_conf or 0))
    allow_logged  = bool(
        score_for_levels >= 50 and
        any(levels.get(k) for k in ("tp1","tp2","ssl","hsl","suggested_tp1","suggested_ssl"))
    )

    entry_val = levels.get("entry") or str(round(m["price"], 2))
    levels    = normalize_trade_levels(entry_val, direction, levels)
    ssl_val   = levels.get("ssl")   if (is_tradeable or allow_logged) else None
    hsl_val   = levels.get("hsl")   if (is_tradeable or allow_logged) else None
    tp1_val   = levels.get("tp1")   if (is_tradeable or allow_logged) else None
    tp2_val   = levels.get("tp2")   if (is_tradeable or allow_logged) else None
    tp3_val   = levels.get("tp3")   if (is_tradeable or allow_logged) else None
    reject_reason = "" if is_tradeable else (levels.get("reason") or verdict)

    return {
        "id":          f"{symbol}_{now_thai().strftime('%Y%m%d_%H%M')}",
        "time":        now_thai().isoformat(),
        "time_thai":   now_thai().strftime('%Y-%m-%d %H:%M TH'),
        "symbol":      symbol,
        "bot_version": cfg.BOT_VERSION,
        "gate_path":   gate_path,
        "pre_conf":    pre_conf if pre_conf is not None else conf,
        "claude_called": claude_called,
        "indicator_set": {"name": S["name"], "version": S["version"],
                          "configured": S["configured"], "review_after": S["review_after"]},
        "regime":         regime,
        "regime_conf":    regime_conf,
        "regime_reasons": regime_reasons,
        "strategy_used":  strategy,
        "filter_passed":  True,
        "filter_reason":  filter_reason,
        "direction":      direction,
        "price":   m["price"],
        "change":  m["change"],
        "funding_rate": m.get("funding_rate"),
        "decision_log": {
            "regime":   {"value": regime, "conf": regime_conf, "reasons": regime_reasons},
            "strategy": strategy,
            "pre_conf": pre_conf if pre_conf is not None else conf,
            "gate": {"filter": cfg.FILTER_MIN_CONF, "weak": cfg.WEAK_MIN_CONF,
                     "claude": cfg.CLAUDE_MIN_CONF, "auto": cfg.AUTO_APPROVE_CONF},
            "tf_30m_rebound": rb_data if strategy == "REBOUND" else {},
            "tf_breakdown":   tf_bk,
            "market_ctx":     m.get("market_ctx", {}),     # SET2v2
            "ctx_bonus":      m.get("ctx_bonus", 0),        # SET2v2
            "ctx_reasons":    m.get("ctx_reasons", []),     # SET2v2
        },
        "indicators": {
            "15m": {"rsi": t15.get("rsi"), "macd_hist": t15.get("macd_hist"),
                    "bb_pct": t15.get("bb_pct"), "ema_short": t15.get("ema_short"),
                    "ema_mid": t15.get("ema_mid"), "vol_ratio": t15.get("vol_ratio")},
            "30m": {"rsi": t30.get("rsi"), "macd_hist": t30.get("macd_hist"),
                    "bb_pct": t30.get("bb_pct"), "vol_ratio": t30.get("vol_ratio")} if t30 else {},
            "1h":  {"rsi": t1h.get("rsi"), "macd_hist": t1h.get("macd_hist"),
                    "ema_short": t1h.get("ema_short"), "ema_mid": t1h.get("ema_mid")},
            "4h":  {"rsi": t4h.get("rsi"), "macd_hist": t4h.get("macd_hist")},
        },
        "verdict": verdict, "conf": conf,
        "entry": entry_val if (is_tradeable or allow_logged) else None,
        "ssl": ssl_val, "hsl": hsl_val,
        "tp1": tp1_val, "tp2": tp2_val, "tp3": tp3_val,
        "reason_code":   levels.get("reason_code", ""),
        "risk_flags":    levels.get("risk_flags", []),
        "reject_reason": reject_reason,
        "ai_text":       ai_text,
        "recheck":       None,
    }

def main():
    log("=" * 60)
    log(f"🚀 Crypto Signal Bot — {cfg.BOT_VERSION}")
    log(f"   MACD4h Regime + Calibrated Scoring + Short Gate + Market Context")
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

        # ── Step 1: Detect Regime (SET2 — MACD4h weighted) ───
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

        # ── Step 2: เลือก Strategy ────────────────────────────
        passed = False; direction = "N/A"
        strategy = "NONE"; filter_reason = ""
        tf_bk = {}; rb_data = {}
        pre_conf = 0; block_reason_code = ""

        if regime == "TRENDING":
            strategy = "TREND_FOLLOW"
            passed, direction, reasons, tf_bk = check_multi_tf(
                m["tf_4h"], m["tf_1h"], m["tf_15m"], m["price"])
            filter_reason = " | ".join(reasons)
            pre_conf = 75 if passed else (55 if tf_bk.get("4h",{}).get("bias") != "NEUTRAL" else 20)
            log(f"  📈 Strategy: TREND_FOLLOW | {'✅ PASS' if passed else '⏭ SKIP'}: {filter_reason[:80]}")

        elif regime in ("RANGING", "MIXED"):
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
            log(f"  📊 Score: L={long_score} S={short_score} /{cfg.REBOUND_MAX_SCORE} → pre_conf={pre_conf}% | turn_up={rb_data.get('rsi_turning_up')} turn_dn={rb_data.get('rsi_turning_down')}")

        # ── Step 3.5: Market Context Bonus (SET2v2) ───────────
        # Conservative phase:
        #   - Fetch + log เสมอ (เก็บ data สำหรับ validate ภายหลัง)
        #   - Apply negative penalty → ทุก direction (ลด false signal)
        #   - Apply positive Short → เปิดทาง short path
        #   - Defer positive Long → รอ 50+ signals validate
        #   - Claude gate reduction → ถ้า ctx aligned (-3 pts)
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

        # store back ใน m สำหรับ build_summary + build_signal
        m["ctx_bonus"]   = ctx_bonus
        m["ctx_reasons"] = ctx_reasons

        # ── 5-Tier Gate ────────────────────────────────────────
        def _base_log(verdict_val, gate_path, claude_called=False):
            th_now = now_thai()
            return {
                "id": f"{symbol}_{th_now.strftime('%Y%m%d_%H%M')}",
                "time": th_now.isoformat(), "time_thai": th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol": symbol, "price": m["price"], "change": m["change"],
                "bot_version": cfg.BOT_VERSION,
                "indicator_set": {"name": S["name"], "version": S["version"]},
                "regime": regime, "regime_conf": regime_conf,
                "strategy_used": strategy, "direction": direction,
                "pre_conf": pre_conf, "verdict": verdict_val, "conf": pre_conf,
                "gate_path": gate_path, "claude_called": claude_called,
                "filter_passed": passed, "filter_reason": filter_reason,
                "block_reason_code": block_reason_code or "",
                "reject_reason": filter_reason if verdict_val in ("FILTERED","NO TRADE","WEAK SIGNAL") else "",
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
                    "tf_30m_rebound": rb_data if strategy == "REBOUND" else {},
                    "tf_breakdown":   tf_bk,
                    "market_ctx":  mkt_ctx,    # SET2v2
                    "ctx_bonus":   m.get("ctx_bonus", 0),
                    "ctx_reasons": m.get("ctx_reasons", []),
                },
                "recheck": None,
            }

        # Tier 1: FILTERED — ห้าม insert log เด็ดขาด (Bug-003 fix)
        if pre_conf < cfg.FILTER_MIN_CONF:
            skipped += 1
            log(f"  🗑 TIER1: pre_conf={pre_conf}% < {cfg.FILTER_MIN_CONF}% → FILTERED (no log)")
            time.sleep(1); continue

        # Tier 2: WEAK (log only)
        if pre_conf < cfg.WEAK_MIN_CONF:
            skipped += 1
            log(f"  📋 TIER2: pre_conf={pre_conf}% → WEAK SIGNAL")
            logs.insert(0, _base_log("WEAK SIGNAL", "TIER2_WEAK"))
            time.sleep(1); continue

        # Tier 3: Bot-only (35-54%)
        if pre_conf < cfg.CLAUDE_MIN_CONF:
            if not passed:
                skipped += 1
                log(f"  🤖 TIER3: pre_conf={pre_conf}% + filter fail → NO TRADE")
                rec = _base_log("NO TRADE", "TIER3_BOT_REJECT")
                rec["reject_reason"] = f"Bot: pre_conf={pre_conf}% below Claude gate, filter not passed"
                logs.insert(0, rec)
                time.sleep(1); continue
            else:
                skipped += 1
                log(f"  📋 TIER3: pre_conf={pre_conf}% passed filter but below Claude gate → WEAK SIGNAL")
                logs.insert(0, _base_log("WEAK SIGNAL", "TIER3_WEAK_PASSED"))
                time.sleep(1); continue

        # [SET2v2] Pre-filter ก่อน Tier 4 — กัน wasted Claude calls
        if passed and strategy == "REBOUND":
            skip, skip_reason = should_skip_claude(
                direction, rb_data, t1h, t4h, pre_conf,
                market_ctx=mkt_ctx,
                tf_15m=m.get("tf_15m"),
            )
            if skip:
                skipped += 1
                log(f"  🚫 SET2 PRE-FILTER: {skip_reason}")
                rec = _base_log("NO TRADE", "SET2_PREFILTER_BLOCK")
                rec["reject_reason"] = f"SET2 pre-filter: {skip_reason}"
                logs.insert(0, rec)
                time.sleep(1); continue

        # ── Tier 5: AUTO APPROVE — SET2v2 redesign ──────────
        # Bug-001 fix: แยก logic ระหว่าง TREND_FOLLOW และ REBOUND
        #   TREND_FOLLOW: EMA aligned เป็น primary confirmation (ถูกต้อง)
        #   REBOUND:      counter-trend = EMA ตรงข้าม direction เป็นเรื่องปกติ!
        #                 ใช้ score + rsi_turn + margin แทน
        # Bug-002 fix: ลด REBOUND Tier5 threshold เป็น REBOUND_AUTO_APPROVE_CONF
        #   เพราะ scoring scale /16 → 70% = score 11.2 ซึ่งแทบไม่มีใน data
        auto_approved = False
        auto_approve_reason = ""

        if passed and direction != "N/A":
            ema_15m_bull = bool(t15.get("ema_short",0) and t15.get("ema_mid",0) and
                                t15.get("ema_short") > t15.get("ema_mid"))
            ema_1h_bull  = bool(t1h.get("ema_short",0) and t1h.get("ema_mid",0) and
                                t1h.get("ema_short") > t1h.get("ema_mid"))

            if strategy == "TREND_FOLLOW":
                # TREND_FOLLOW: EMA aligned ถูกต้อง — คงเดิม
                if pre_conf >= cfg.AUTO_APPROVE_CONF:
                    if direction == "Long" and ema_15m_bull and ema_1h_bull:
                        auto_approved = True
                        auto_approve_reason = f"TREND_FOLLOW: pre={pre_conf}% + EMA bull aligned"
                    elif direction == "Short" and not ema_15m_bull and not ema_1h_bull:
                        auto_approved = True
                        auto_approve_reason = f"TREND_FOLLOW: pre={pre_conf}% + EMA bear aligned"

            elif strategy == "REBOUND":
                # REBOUND: ใช้ score quality แทน EMA alignment
                # Data analysis (1031 signals):
                #   score≥8 + margin≥6 + rsi_turn_up + pre≥68 → 4/11 (36%) auto ok
                #   score≥7 + margin≥5 + rsi_turn_up → 36 signals, 21 approved (58%)
                # TIER5-A: true auto-approve (bar สูง — confident)
                # TIER5-B: reduced Claude gate (bar ต่ำกว่า — ส่ง Claude แต่ gate ลด)
                rb_ls     = rb_data.get("long_score", 0)
                rb_ss     = rb_data.get("short_score", 0)
                rb_turn_u = rb_data.get("rsi_turning_up", False)
                rb_turn_d = rb_data.get("rsi_turning_down", False)
                rb_margin = (rb_ls - rb_ss) if direction == "Long" else (rb_ss - rb_ls)
                rb_score  = rb_ls if direction == "Long" else rb_ss

                # TIER5-A (true auto-approve): score≥8, margin≥6, pre≥68
                t5a_pre    = cfg.REBOUND_AUTO_APPROVE_CONF   # 68
                t5a_score  = cfg.REBOUND_TIER5_MIN_SCORE      # 8
                t5a_margin = cfg.REBOUND_TIER5_MIN_MARGIN     # 6

                if pre_conf >= t5a_pre:
                    if direction == "Long" and rb_turn_u and rb_score >= t5a_score and rb_margin >= t5a_margin:
                        auto_approved = True
                        auto_approve_reason = (f"REBOUND LONG TIER5-A: pre={pre_conf}% + "
                                               f"rsi_turn_up + score={rb_score} + margin={rb_margin}")
                    elif direction == "Short" and rb_turn_d and rb_score >= t5a_score and rb_margin >= t5a_margin:
                        auto_approved = True
                        auto_approve_reason = (f"REBOUND SHORT TIER5-A: pre={pre_conf}% + "
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

        # Tier 4: Claude validates (55-79%, หรือ 52+ ถ้า ctx_aligned)
        effective_claude_min = cfg.CLAUDE_MIN_CONF
        if ctx_aligned and cfg.CTX_CLAUDE_GATE_REDUCTION > 0:
            effective_claude_min = cfg.CLAUDE_MIN_CONF - cfg.CTX_CLAUDE_GATE_REDUCTION
            log(f"  📡 ctx_aligned=True → Claude gate {cfg.CLAUDE_MIN_CONF}→{effective_claude_min}%")

        # [TIER5-B] REBOUND quality signal → ลด Claude gate เพิ่มอีก
        # Data: score≥7 margin≥5 rsi_turn_up → 58% approval → คุ้มที่จะเรียก Claude ง่ายขึ้น
        if strategy == "REBOUND" and direction != "N/A":
            rb_ls5b = rb_data.get("long_score", 0)
            rb_ss5b = rb_data.get("short_score", 0)
            rb_tu5b = rb_data.get("rsi_turning_up", False)
            rb_td5b = rb_data.get("rsi_turning_down", False)
            rb_sc5b = rb_ls5b if direction == "Long" else rb_ss5b
            rb_mg5b = (rb_ls5b - rb_ss5b) if direction == "Long" else (rb_ss5b - rb_ls5b)
            t5b_ok  = (
                (direction == "Long"  and rb_tu5b and rb_sc5b >= cfg.REBOUND_TIER5B_SCORE and rb_mg5b >= cfg.REBOUND_TIER5B_MARGIN) or
                (direction == "Short" and rb_td5b and rb_sc5b >= cfg.REBOUND_TIER5B_SCORE and rb_mg5b >= cfg.REBOUND_TIER5B_MARGIN)
            )
            if t5b_ok:
                gate_before = effective_claude_min
                effective_claude_min = min(effective_claude_min, cfg.REBOUND_TIER5B_GATE)
                if effective_claude_min < gate_before:
                    log(f"  📈 TIER5-B: quality REBOUND ({direction} score={rb_sc5b} margin={rb_mg5b}) "
                        f"→ Claude gate {gate_before}→{effective_claude_min}%")

        if pre_conf < effective_claude_min:
            # อยู่ใน Tier 3 แต่ ctx_aligned ดึงลงมา → WEAK SIGNAL ยังไม่เรียก Claude
            skipped += 1
            log(f"  📋 TIER3-CTX: pre_conf={pre_conf}% < {effective_claude_min}% → WEAK SIGNAL")
            logs.insert(0, _base_log("WEAK SIGNAL", "TIER3_CTX_WEAK"))
            time.sleep(1); continue

        if direction not in ("Long", "Short"):
            skipped += 1
            reason = "Direction=N/A: no valid long/short signal to validate"
            log(f"  🚫 NO_DIRECTION: pre_conf={pre_conf}% → NO TRADE | {reason}")
            rec = _base_log("NO TRADE", "TIER4_NO_DIRECTION")
            rec["reject_reason"] = reason
            logs.insert(0, rec)
            time.sleep(1); continue

        log(f"  🤖 TIER4: pre_conf={pre_conf}% | dir={direction} | {strategy} → Claude"
            f"{' (ctx gate '+str(effective_claude_min)+'%)' if ctx_aligned else ''}")
        try:
            summary  = build_summary(m, regime, strategy, tf_bk, rb_data, direction=direction)
            ai_text  = call_claude(symbol, direction, strategy, summary)
            verdict, conf, levels = parse_ai(ai_text)
            claude_calls += 1

            if not levels.get("entry"):
                levels["entry"] = str(round(m["price"], 2))

            if verdict in ("APPROVED", "WEAK APPROVAL"):
                fallback = calc_fallback_levels(m["price"], direction, m.get("atr", 0))
                for k in ("ssl","hsl","tp1","tp2","tp3"):
                    if not levels.get(k): levels[k] = fallback[k]
            elif (max(pre_conf, conf) or 0) >= 50:
                fallback = calc_fallback_levels(m["price"], direction, m.get("atr", 0))
                for k in ("ssl","hsl","tp1","tp2","tp3"):
                    if not levels.get("suggested_"+k): levels["suggested_"+k] = fallback[k]
                levels["suggested_entry"] = levels.get("entry") or str(round(m["price"], 2))

            sig = build_signal(symbol, direction, m, verdict, conf, levels,
                               ai_text, regime, regime_conf, regime_reasons,
                               strategy, filter_reason, tf_bk, rb_data,
                               gate_path="TIER4_CLAUDE", pre_conf=pre_conf, claude_called=True)
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
                "gate_path": "TIER4_CLAUDE", "pre_conf": pre_conf, "claude_called": True,
                "conf": 0, "verdict": "ERROR",
                "error_type": error_type, "error_msg": str(e),
                "reject_reason": f"ERROR[{error_type}]: {error_desc}",
                "recheck": None,
            })
        time.sleep(2)

    save_log(logs)
    log(f"\n{'='*60}")
    log(f"✅ Done — Claude calls: {claude_calls} | Skipped: {skipped}")
    log(f"{'='*60}")

if __name__ == "__main__":
    main()
