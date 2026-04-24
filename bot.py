#!/usr/bin/env python3
"""
Crypto Signal Bot — SET1 v2
════════════════════════════════════════════════════════════
INDICATOR SET 1 v2 — Multi-TF + Rebound + Market Regime
Date: 2026-04-19 | Review: 2026-04-26

NEW in v2:
  + 4h Timeframe (Bias)
  + 30m Timeframe (Rebound Hunt)
  + Market Regime Detection (TRENDING/RANGING/VOLATILE)
  + Auto Strategy Selection:
      TRENDING  → Multi-TF 4h→1h→15m (Trend Following)
      RANGING   → Rebound Hunt 30m (Counter-trend)
      VOLATILE  → NO TRADE (งดเทรด)
  + Decision Log (บันทึกทุกขั้นตอนการตัดสินใจ)

Log Fields เพิ่ม:
  regime, regime_reason, strategy_used
  tf_4h_bias, tf_1h_direction, tf_15m_entry
  tf_30m_rebound (สำหรับ RANGING)
  decision_log (ทุก step ที่ bot คิด)
════════════════════════════════════════════════════════════
"""
import json, os, sys, time, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfg

# ── Timezone ไทย (UTC+7) ──────────────────────────────────────
TZ_THAI = timezone(timedelta(hours=7))

def now_thai():
    """datetime ปัจจุบันเวลาไทย"""
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

# ═══════════════════════════════════════════════════════════
# INDICATOR SET 1 v2
# ═══════════════════════════════════════════════════════════
INDICATOR_SET = {
    "name":             f"{cfg.BOT_VERSION} — Rebound Enhanced + Funding + 5m Micro",
    "version":          "4",
    "configured":       "2026-04-21",
    "review_after":     "2026-04-27",
    "RSI_OVERBOUGHT":   cfg.RSI_OVERBOUGHT,
    "RSI_OVERSOLD":     cfg.RSI_OVERSOLD,
    "EMA_SHORT":        cfg.EMA_SHORT,
    "EMA_MID":          cfg.EMA_MID,
    "EMA_LONG":         cfg.EMA_LONG,
    "EMA_CROSS_BUFFER": cfg.EMA_CROSS_BUFFER,
    "MIN_RR":           cfg.MIN_RR,
    # MACD — both string label AND individual keys (used by calc_tf_data)
    "MACD":             f"{cfg.MACD_FAST}/{cfg.MACD_SLOW}/{cfg.MACD_SIGNAL}",
    "MACD_FAST":        cfg.MACD_FAST,
    "MACD_SLOW":        cfg.MACD_SLOW,
    "MACD_SIGNAL":      cfg.MACD_SIGNAL,
    # BB — both string label AND individual keys (used by calc_tf_data)
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
    "REBOUND_BB_LOW":        cfg.REBOUND_BB_LOW,
    "REBOUND_BB_HIGH":       cfg.REBOUND_BB_HIGH,
    "REBOUND_VOL_MIN":       cfg.REBOUND_VOL_MIN,
    "REBOUND_SWING_BUFFER":  cfg.REBOUND_SWING_BUFFER,
    "REBOUND_MIN_SCORE":     cfg.REBOUND_MIN_SCORE,
    "REBOUND_1H_RSI_GUARD":  cfg.REBOUND_1H_RSI_GUARD,
    # Regime thresholds (used by detect_regime)
    "REGIME_ATR_TREND":     cfg.REGIME_ATR_TREND,
    "REGIME_ATR_VOLATILE":  cfg.REGIME_ATR_VOLATILE,
    "REGIME_BB_RANGING":    cfg.REGIME_BB_RANGING,
    "REGIME_EMA_DIFF":      cfg.REGIME_EMA_DIFF,
    # Multi-TF thresholds (used by check_multi_tf)
    "MTF_4H_RSI_BULL":     cfg.MTF_4H_RSI_BULL,
    "MTF_4H_RSI_BEAR":     cfg.MTF_4H_RSI_BEAR,
    "MTF_1H_RSI_CONFIRM":  cfg.MTF_1H_RSI_CONFIRM,
    # Confidence gates
    "FILTER_MIN_CONF":  cfg.FILTER_MIN_CONF,
    "WEAK_MIN_CONF":    cfg.WEAK_MIN_CONF,
    "CLAUDE_MIN_CONF":  cfg.CLAUDE_MIN_CONF,
    "AUTO_APPROVE_CONF":cfg.AUTO_APPROVE_CONF,
}

# ─── INDICATORS ───────────────────────────────────────────
def get_klines(symbol, interval, limit=150):
    """ดึง klines พร้อม retry 3 รอบ"""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    for attempt in range(3):
        data = http_get(url, timeout=20)
        if not data:
            log(f"⚠️ get_klines attempt {attempt+1}/3 failed: {symbol} {interval}")
            time.sleep(1)
            continue
        # ตรวจ Binance error response เช่น {"code":-1121,"msg":"Invalid symbol."}
        if isinstance(data, dict) and "code" in data:
            log(f"❌ Binance error {symbol} {interval}: {data}")
            return []
        if not isinstance(data, list) or len(data) < 10:
            log(f"⚠️ klines too short: {symbol} {interval} got {len(data) if isinstance(data,list) else type(data)}")
            time.sleep(1)
            continue
        return data
    return []

def calc_ema(closes, period):
    if len(closes) < period: return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]: ema = c * k + ema * (1 - k)
    return round(ema, 6)

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return round(100 - 100/(1+ag/al), 2) if al else 100.0

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal: return None, None, None
    series = []
    for i in range(slow-1, len(closes)):
        ef = calc_ema(closes[:i+1], fast)
        es = calc_ema(closes[:i+1], slow)
        if ef and es: series.append(ef - es)
    if len(series) < signal: return None, None, None
    ml  = series[-1]
    ms  = calc_ema(series, signal)
    mh  = round(ml - ms, 6) if ms else None
    return round(ml, 6), round(ms, 6) if ms else None, mh

def calc_bollinger(closes, period=20, std_mult=2):
    if len(closes) < period: return None, None, None, None, None
    recent = closes[-period:]
    mid    = sum(recent) / period
    std    = (sum((c-mid)**2 for c in recent) / period) ** 0.5
    upper  = mid + std_mult * std
    lower  = mid - std_mult * std
    price  = closes[-1]
    pct_b  = (price - lower) / (upper - lower) if (upper - lower) != 0 else 0.5
    width  = (upper - lower) / mid * 100  # BB width %
    return round(upper,4), round(mid,4), round(lower,4), round(pct_b,4), round(width,4)

def calc_volume_ratio(volumes, avg_period=20):
    if len(volumes) < avg_period + 1: return None
    avg = sum(volumes[-avg_period-1:-1]) / avg_period
    return round(volumes[-1] / avg, 2) if avg else None

def calc_swing_levels(highs, lows, lookback=20):
    rh = highs[-lookback:]; rl = lows[-lookback:]
    ph = []; pl = []
    for i in range(2, len(rh)-2):
        if rh[i] > rh[i-1] and rh[i] > rh[i+1] and rh[i] > rh[i-2] and rh[i] > rh[i+2]:
            ph.append(round(rh[i], 2))
        if rl[i] < rl[i-1] and rl[i] < rl[i+1] and rl[i] < rl[i-2] and rl[i] < rl[i+2]:
            pl.append(round(rl[i], 2))
    return {
        "swing_high":  round(max(rh), 2),
        "swing_low":   round(min(rl), 2),
        "pivot_highs": sorted(set(ph), reverse=True)[:3],
        "pivot_lows":  sorted(set(pl))[:3],
    }

def calc_tf_data(klines):
    """คำนวณ indicators จาก klines"""
    if not klines or len(klines) < 30: return None
    c = [float(k[4]) for k in klines]
    h = [float(k[2]) for k in klines]
    l = [float(k[3]) for k in klines]
    v = [float(k[5]) for k in klines]
    S = INDICATOR_SET  # BUG FIX: was comment-only → NameError crash
    ml, ms, mh = calc_macd(c, S["MACD_FAST"], S["MACD_SLOW"], S["MACD_SIGNAL"])
    bbu, bbm, bbl, bbp, bbw = calc_bollinger(c, S["BB_PERIOD"], S["BB_STD"])
    return {
        "close":      c[-1],
        "ema_short":  calc_ema(c, S["EMA_SHORT"]),
        "ema_mid":    calc_ema(c, S["EMA_MID"]),
        "ema_long":   calc_ema(c, S["EMA_LONG"]),
        "rsi":        calc_rsi(c, cfg.RSI_PERIOD),
        "macd_line":  ml, "macd_sig": ms, "macd_hist": mh,
        "bb_upper":   bbu, "bb_mid": bbm, "bb_lower": bbl,
        "bb_pct":     bbp, "bb_width": bbw,
        "vol_ratio":  calc_volume_ratio(v, S["VOLUME_AVG"]),
        "swings":     calc_swing_levels(h, l, S["SWING_LOOKBACK"]),
        "atr":        (max(h[-14:]) - min(l[-14:])) / 14 if len(h) >= 14 else None,
    }

# ═══════════════════════════════════════════════════════════
# MARKET REGIME DETECTION
# ═══════════════════════════════════════════════════════════
def detect_regime(tf_1h, tf_4h, price):
    """
    ตรวจสอบ Market Regime จาก 1h + 4h
    Returns: (regime, confidence, reasons)
    TRENDING / RANGING / VOLATILE / MIXED
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

    if avg_atr_pct > S["REGIME_ATR_VOLATILE"]:
        scores["VOLATILE"] += 3
        reasons.append(f"ATR สูงมาก {avg_atr_pct:.2f}% → VOLATILE")
    elif avg_atr_pct > S["REGIME_ATR_TREND"]:
        scores["TRENDING"] += 2
        reasons.append(f"ATR ปกติ {avg_atr_pct:.2f}% → TRENDING")
    else:
        scores["RANGING"] += 2
        reasons.append(f"ATR ต่ำ {avg_atr_pct:.2f}% → RANGING")

    # ── BB Width ──────────────────────────────────────────────
    bbw = tf_1h.get("bb_width", 5)
    if bbw < S["REGIME_BB_RANGING"]:
        scores["RANGING"] += 2
        reasons.append(f"BB width แคบ {bbw:.1f}% → RANGING")
    elif bbw > 6:
        scores["TRENDING"] += 1
        reasons.append(f"BB width กว้าง {bbw:.1f}% → TRENDING")

    # ── EMA Alignment 4h ─────────────────────────────────────
    e_s4 = tf_4h.get("ema_short"); e_m4 = tf_4h.get("ema_mid")
    if e_s4 and e_m4:
        ema_diff_4h = abs(e_s4 - e_m4) / e_m4 * 100
        if ema_diff_4h > S["REGIME_EMA_DIFF"]:
            scores["TRENDING"] += 2
            reasons.append(f"EMA4h ห่างกัน {ema_diff_4h:.2f}% → TRENDING")
        else:
            scores["RANGING"] += 1
            reasons.append(f"EMA4h ใกล้กัน {ema_diff_4h:.2f}% → RANGING")

    # ── EMA Alignment 1h ─────────────────────────────────────
    e_s1 = tf_1h.get("ema_short"); e_m1 = tf_1h.get("ema_mid")
    if e_s1 and e_m1:
        ema_diff_1h = abs(e_s1 - e_m1) / e_m1 * 100
        if ema_diff_1h > S["REGIME_EMA_DIFF"]:
            scores["TRENDING"] += 1
            reasons.append(f"EMA1h ห่างกัน {ema_diff_1h:.2f}% → TRENDING")

    # ── MACD 4h ──────────────────────────────────────────────
    mh4 = tf_4h.get("macd_hist")
    if mh4 and abs(mh4) > 0.0001:
        scores["TRENDING"] += 1
        reasons.append(f"MACD4h histogram={mh4:.4f} → TRENDING")

    # ── ตัดสิน ───────────────────────────────────────────────
    max_score = max(scores.values())
    winner    = max(scores, key=scores.get)
    conf      = min(round(max_score / sum(scores.values()) * 100), 95) if sum(scores.values()) else 50

    if scores["VOLATILE"] >= 3:
        return "VOLATILE", conf, reasons
    if scores["TRENDING"] > scores["RANGING"]:
        return "TRENDING", conf, reasons
    if scores["RANGING"] >= scores["TRENDING"]:
        return "RANGING", conf, reasons
    return "MIXED", 50, reasons

# ═══════════════════════════════════════════════════════════
# STRATEGY 1: Multi-TF 4h → 1h → 15m (Trend Following)
# ═══════════════════════════════════════════════════════════
def check_multi_tf(tf_4h, tf_1h, tf_15m, price):
    """
    ตรวจสอบ Multi-TF alignment
    Returns: (passed, direction, reasons, tf_breakdown)
    """
    S       = INDICATOR_SET
    reasons = []
    tf_bk   = {}  # breakdown ของแต่ละ TF สำหรับ log

    if not tf_4h or not tf_1h or not tf_15m:
        return False, "N/A", ["ข้อมูล TF ไม่ครบ"], {}

    # ── 4h Bias ──────────────────────────────────────────────
    e_s4  = tf_4h.get("ema_short"); e_m4 = tf_4h.get("ema_mid")
    rsi_4 = tf_4h.get("rsi")
    bias_4h = "NEUTRAL"

    if e_s4 and e_m4 and rsi_4:
        if e_s4 > e_m4 and rsi_4 > S["MTF_4H_RSI_BULL"] and price > e_m4:
            bias_4h = "BULLISH"
            reasons.append(f"4h Bias: BULLISH (EMA9>EMA21, RSI={rsi_4})")
        elif e_s4 < e_m4 and rsi_4 < S["MTF_4H_RSI_BEAR"] and price < e_m4:
            bias_4h = "BEARISH"
            reasons.append(f"4h Bias: BEARISH (EMA9<EMA21, RSI={rsi_4})")
        else:
            reasons.append(f"4h Bias: NEUTRAL (RSI={rsi_4})")

    tf_bk["4h"] = {"bias": bias_4h, "ema_short": e_s4, "ema_mid": e_m4, "rsi": rsi_4}

    if bias_4h == "NEUTRAL":
        return False, "N/A", reasons, tf_bk

    # ── 1h Direction ─────────────────────────────────────────
    e_s1  = tf_1h.get("ema_short"); e_m1 = tf_1h.get("ema_mid")
    rsi_1 = tf_1h.get("rsi"); mh_1 = tf_1h.get("macd_hist")
    dir_1h = "NEUTRAL"

    if e_s1 and e_m1 and rsi_1:
        if bias_4h == "BULLISH" and e_s1 > e_m1 and rsi_1 > S["MTF_1H_RSI_CONFIRM"]:
            dir_1h = "LONG"
            reasons.append(f"1h Direction: LONG confirmed (RSI={rsi_1}, MACD hist={mh_1})")
        elif bias_4h == "BEARISH" and e_s1 < e_m1 and rsi_1 < S["MTF_1H_RSI_CONFIRM"]:
            dir_1h = "SHORT"
            reasons.append(f"1h Direction: SHORT confirmed (RSI={rsi_1}, MACD hist={mh_1})")
        else:
            reasons.append(f"1h Direction: ไม่ confirm กับ 4h (RSI={rsi_1})")

    tf_bk["1h"] = {"direction": dir_1h, "ema_short": e_s1, "ema_mid": e_m1,
                   "rsi": rsi_1, "macd_hist": mh_1}

    if dir_1h == "NEUTRAL":
        return False, "N/A", reasons, tf_bk

    # ── 15m Entry Trigger ────────────────────────────────────
    # ใช้ 15m เหมือนเดิม — แต่ผ่อน MACD เป็น bonus (ไม่ required)
    e_s15  = tf_15m.get("ema_short"); e_m15 = tf_15m.get("ema_mid")
    rsi_15 = tf_15m.get("rsi"); mh_15 = tf_15m.get("macd_hist")
    bbp_15 = tf_15m.get("bb_pct"); vr_15 = tf_15m.get("vol_ratio")
    entry_ok = False
    buf = S["EMA_CROSS_BUFFER"]

    if dir_1h == "LONG":
        ema_ok  = bool(e_s15 and e_m15 and e_s15 > e_m15 * (1 + buf))
        rsi_ok  = bool(rsi_15 and rsi_15 < cfg.RSI_OVERBOUGHT)
        macd_ok = bool(mh_15 and mh_15 > 0)  # bonus — ไม่ required

        if ema_ok and rsi_ok:   # ✅ ผ่านแค่ EMA + RSI
            entry_ok = True
            macd_note = f"MACD={mh_15:.4f} ✅" if macd_ok else f"MACD={mh_15} (neutral)"
            reasons.append(f"15m Entry: LONG ✅ EMA aligned | RSI={rsi_15} | {macd_note}")
        else:
            fail = []
            if not ema_ok:  fail.append(f"EMA9({e_s15:.2f})<EMA21({e_m15:.2f})" if e_s15 and e_m15 else "EMA missing")
            if not rsi_ok:  fail.append(f"RSI={rsi_15} ≥ {S['RSI_OVERBOUGHT']} (overbought)")
            reasons.append(f"15m Entry: ไม่ผ่าน [{' | '.join(fail)}]")

    elif dir_1h == "SHORT":
        ema_ok  = bool(e_s15 and e_m15 and e_s15 < e_m15 * (1 - buf))
        rsi_ok  = bool(rsi_15 and rsi_15 > cfg.RSI_OVERSOLD)
        macd_ok = bool(mh_15 and mh_15 < 0)

        if ema_ok and rsi_ok:
            entry_ok = True
            macd_note = f"MACD={mh_15:.4f} ✅" if macd_ok else f"MACD={mh_15} (neutral)"
            reasons.append(f"15m Entry: SHORT ✅ EMA aligned | RSI={rsi_15} | {macd_note}")
        else:
            fail = []
            if not ema_ok: fail.append(f"EMA9({e_s15:.2f})>EMA21({e_m15:.2f})" if e_s15 and e_m15 else "EMA missing")
            if not rsi_ok: fail.append(f"RSI={rsi_15} ≤ {S['RSI_OVERSOLD']} (oversold)")
            reasons.append(f"15m Entry: ไม่ผ่าน [{' | '.join(fail)}]")

    tf_bk["15m"] = {"entry_ok": entry_ok, "ema_short": e_s15, "ema_mid": e_m15,
                    "rsi": rsi_15, "macd_hist": mh_15, "bb_pct": bbp_15, "vol_ratio": vr_15}

    direction = "Long" if dir_1h == "LONG" else "Short"
    return entry_ok, direction, reasons, tf_bk

# ═══════════════════════════════════════════════════════════
# STRATEGY 2: Rebound Hunt 30m (Counter-trend)
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# HELPER FUNCTIONS — Countertrend + Persistence
# ═══════════════════════════════════════════════════════════

def is_bullish_stack(tf):
    """EMA short > mid → bullish structure"""
    es = tf.get("ema_short") or 0
    em = tf.get("ema_mid")   or 0
    return es > em and es > 0 and em > 0

def is_bearish_stack(tf):
    """EMA short < mid → bearish structure"""
    es = tf.get("ema_short") or 0
    em = tf.get("ema_mid")   or 0
    return es < em and es > 0 and em > 0

def should_block_countertrend_rebound(direction, tf_1h, tf_15m):
    """
    Block REBOUND SHORT ถ้า 1h + 15m ยัง bullish (สวน trend)
    Block REBOUND LONG  ถ้า 1h + 15m ยัง bearish (สวน trend)
    """
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

def compute_pre_conf(long_score, short_score, direction):
    """
    คำนวณ pre_conf จาก score/REBOUND_MAX_SCORE
    ใช้ denominator เดียว (13) ทั้ง passed และ not passed
    """
    best = long_score if direction == "Long" else short_score if direction == "Short" \
           else max(long_score, short_score)
    return min(round(best / cfg.REBOUND_MAX_SCORE * 100), 95)

def get_persistence_bonus(symbol, direction, logs, window=3):
    """
    ถ้า signal เดิม (symbol + direction) ซ้ำกัน >= window รอบติดกัน
    → return PERSISTENCE_BONUS เพื่อเพิ่ม confidence
    ใช้เฉพาะ REBOUND signals
    """
    recent = [s for s in logs[:window*3]
              if s.get("symbol") == symbol
              and s.get("direction") == direction
              and s.get("strategy_used") == "REBOUND"
              and s.get("verdict") in ("WEAK SIGNAL","FILTERED")]
    if len(recent) >= window:
        return cfg.PERSISTENCE_BONUS
    return 0

def build_reject_reason(block_reason, pre_conf, min_conf, strategy):
    """สร้าง reject_reason ที่มีข้อมูลครบ"""
    return (f"{block_reason} | pre_conf={pre_conf}% < {min_conf}% "
            f"(gate={min_conf}%) strategy={strategy}")


def check_rebound_30m(tf_30m, tf_1h, price, change_24h=0,
                      funding_rate=None, rsi_series=None, tf_5m=None, tf_4h=None, tf_15m=None):
    """
    SET1v3: Rebound Hunt — ใช้ cfg เป็น single source of truth
    Scoring max = cfg.REBOUND_MAX_SCORE (13), threshold >= cfg.REBOUND_MIN_SCORE (3)
    Returns: (passed, direction, reasons, rebound_data)
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
        guard = cfg.REBOUND_1H_RSI_GUARD  # = 25
        if rsi_1h > 75 or rsi_1h < guard:
            reasons.append(f"1h RSI extreme ({rsi_1h:.1f}) — งด Rebound")
            return False, "N/A", reasons, {}

    # ── 4h RSI Direction Guard (NEW) ──────────────────────────
    # ถ้า 4h oversold (<35) → ห้าม SHORT rebound (ลงต่อไม่ได้แล้ว)
    # ถ้า 4h overbought (>65) → ห้าม LONG rebound (ขึ้นต่อไม่ได้แล้ว)
    rsi_4h = tf_4h.get("rsi") if tf_4h else None
    block_short = rsi_4h and rsi_4h < 35  # 4h oversold → ห้าม SHORT
    block_long  = rsi_4h and rsi_4h > 65  # 4h overbought → ห้าม LONG

    # ── RSI Momentum Turn Detection (NEW v3) ──────────────────
    rsi_turning_up   = False
    rsi_turning_down = False
    if rsi_series and len(rsi_series) >= 3:
        r_now  = rsi_series[-1]
        r_prev = rsi_series[-3]
        if r_now and r_prev:
            if r_now > r_prev + 1 and r_now < 50:  # RSI กำลัง turn up จาก oversold
                rsi_turning_up = True
            if r_now < r_prev - 1 and r_now > 50:  # RSI กำลัง turn down จาก overbought
                rsi_turning_down = True

    # ── Funding Rate Bonus (NEW v3 — จากงานวิจัย Nagel 2012) ─
    funding_bonus = 0
    if funding_rate is not None:
        if funding_rate < cfg.FUNDING_BONUS_HIGH:  # < -0.03%
            funding_bonus = 2
        elif funding_rate < cfg.FUNDING_BONUS_MED: # < -0.01%
            funding_bonus = 1

    # ── Large Drop Bonus (NEW v3 — Gutierrez 2016) ────────────
    drop_bonus = 0
    if change_24h < cfg.DROP_BONUS_HIGH:  # < -5%
        drop_bonus = 2
    elif change_24h < cfg.DROP_BONUS_MED: # < -3%
        drop_bonus = 1

    # ── 5m Micro Entry Check (NEW v3) ─────────────────────────
    micro_long_ok  = False
    micro_short_ok = False
    if tf_5m:
        rsi_5m = tf_5m.get("rsi", 50)
        mh_5m  = tf_5m.get("macd_hist", 0) or 0
        es_5m  = tf_5m.get("ema_short")
        em_5m  = tf_5m.get("ema_mid")
        if rsi_5m and rsi_5m > 20 and mh_5m > -0.001:
            micro_long_ok = True   # 5m ไม่ oversold เกิน + MACD ดีขึ้น
        if rsi_5m and rsi_5m < 80 and mh_5m < 0.001:
            micro_short_ok = True

    # ══ LONG Rebound Scoring ══════════════════════════════════
    long_score = 0; long_reasons = []

    # Core signals (เหมือนเดิม แต่ threshold ผ่อนลง)
    if bbp is not None and bbp < cfg.REBOUND_BB_LOW:   # < 0.30
        long_score += 2
        long_reasons.append(f"BB%B={bbp:.2f} ใกล้ lower band")

    if rsi and rsi < cfg.RSI_OVERSOLD:                  # < 32
        long_score += 2
        long_reasons.append(f"RSI={rsi} Oversold")

    if sl_val and abs(price - sl_val) / sl_val < buf:
        long_score += 2
        long_reasons.append(f"ใกล้ Swing Low ${sl_val:.2f}")

    if mh is not None and mh > -0.0001:
        long_score += 1
        long_reasons.append(f"MACD hist={mh:.4f} turning up")

    if vr and vr > cfg.REBOUND_VOL_MIN:                 # > 0.5x
        long_score += 1
        long_reasons.append(f"Volume={vr:.1f}x avg")

    # NEW v3 bonus signals
    if rsi_turning_up:
        long_score += 2
        long_reasons.append(f"RSI momentum turning up (จาก oversold)")

    if funding_bonus > 0:
        long_score += funding_bonus
        long_reasons.append(f"Funding={funding_rate:+.4f}% → Short squeeze risk (+{funding_bonus})")

    if drop_bonus > 0:
        long_score += drop_bonus
        long_reasons.append(f"24h drop={change_24h:.1f}% → Liquidity flush (+{drop_bonus})")

    if micro_long_ok:
        long_score += 1
        long_reasons.append(f"5m micro entry OK (RSI+MACD)")

    # ══ SHORT Rebound Scoring ═════════════════════════════════
    short_score = 0; short_reasons = []

    if bbp is not None and bbp > cfg.REBOUND_BB_HIGH:  # > 0.70
        short_score += 2
        short_reasons.append(f"BB%B={bbp:.2f} ใกล้ upper band")

    if rsi and rsi > cfg.RSI_OVERBOUGHT:                # > 68
        short_score += 2
        short_reasons.append(f"RSI={rsi} Overbought")

    if sh and abs(price - sh) / sh < buf:
        short_score += 2
        short_reasons.append(f"ใกล้ Swing High ${sh:.2f}")

    if mh is not None and mh < 0.0001:
        short_score += 1
        short_reasons.append(f"MACD hist={mh:.4f} turning down")

    if vr and vr > cfg.REBOUND_VOL_MIN:
        short_score += 1
        short_reasons.append(f"Volume={vr:.1f}x avg")

    # NEW v3 bonus signals (Short)
    if rsi_turning_down:
        short_score += 2
        short_reasons.append(f"RSI momentum turning down (จาก overbought)")

    if funding_rate is not None and funding_rate > 0.03:
        short_score += 2
        short_reasons.append(f"Funding={funding_rate:+.4f}% → Long squeeze risk (+2)")
    elif funding_rate is not None and funding_rate > 0.01:
        short_score += 1
        short_reasons.append(f"Funding={funding_rate:+.4f}% → Longs dominant (+1)")

    if change_24h > 5.0:
        short_score += 2
        short_reasons.append(f"24h rise={change_24h:.1f}% → Overextended (+2)")
    elif change_24h > 3.0:
        short_score += 1
        short_reasons.append(f"24h rise={change_24h:.1f}% → Possible fade (+1)")

    if micro_short_ok:
        short_score += 1
        short_reasons.append(f"5m micro entry OK")

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

    if long_score >= min_score and long_score > short_score:
        if block_long:
            reasons.append(f"⚠️ 4h RSI={rsi_4h:.1f} overbought — งด LONG rebound")
            return False, "N/A", reasons, rb_data
        reasons = [f"REBOUND LONG: {r}" for r in long_reasons]
        return True, "Long", reasons, rb_data

    if short_score >= min_score and short_score > long_score:
        if block_short:
            reasons.append(f"⚠️ 4h RSI={rsi_4h:.1f} oversold — งด SHORT rebound (ลงต่อไม่ได้แล้ว)")
            return False, "N/A", reasons, rb_data
        reasons = [f"REBOUND SHORT: {r}" for r in short_reasons]
        return True, "Short", reasons, rb_data

    reasons.append(
        f"Rebound score ไม่ผ่าน: Long={long_score} Short={short_score} "
        f"(ต้องการ>={min_score}) | "
        f"Funding={funding_rate:+.4f}% " if funding_rate else ""
        f"RSI_turn={'↑' if rsi_turning_up else '↓' if rsi_turning_down else '-'}"
    )
    return False, "N/A", reasons, rb_data

# ═══════════════════════════════════════════════════════════
# FETCH ALL TIMEFRAMES
# ═══════════════════════════════════════════════════════════
def fetch_market(symbol):
    """
    ดึงข้อมูลทุก TF: 5m, 15m, 30m, 1h, 4h + Funding Rate
    SET1v3: เพิ่ม 5m micro TF + Funding Rate bonus
    """
    # ── Ticker (retry 3x) ──────────────────────────────────────
    ticker = None
    for attempt in range(3):
        ticker = http_get(
            f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}",
            timeout=20
        )
        if ticker and isinstance(ticker, dict) and "lastPrice" in ticker:
            break
        log(f"⚠️ ticker attempt {attempt+1}/3: {symbol} → {type(ticker)}")
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

    # ── Klines (เพิ่ม 5m) ─────────────────────────────────────
    k5  = get_klines(symbol, "5m",  100)  # NEW v3
    k15 = get_klines(symbol, "15m", 200)
    k30 = get_klines(symbol, "30m", 150)
    k1h = get_klines(symbol, "1h",  150)
    k4h = get_klines(symbol, "4h",  150)

    tf_5m  = calc_tf_data(k5)  if len(k5)  >= 30 else None  # NEW v3
    tf_15m = calc_tf_data(k15) if len(k15) >= 30 else None
    tf_30m = calc_tf_data(k30) if len(k30) >= 30 else None
    tf_1h  = calc_tf_data(k1h) if len(k1h) >= 30 else None
    tf_4h  = calc_tf_data(k4h) if len(k4h) >= 30 else None

    # ── Fallback: ถ้า 15m fail → ใช้ 30m แทน ─────────────────
    entry_tf_used = "15m"
    if not tf_15m and tf_30m:
        log(f"⚠️ fetch_market: 15m failed — using 30m as fallback: {symbol}")
        tf_15m = tf_30m
        entry_tf_used = "30m (fallback)"

    if not tf_15m:
        log(f"❌ fetch_market: both 15m and 30m failed: {symbol}")
        return None

    # ── Funding Rate (NEW v3) ──────────────────────────────────
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

    # ── RSI series สำหรับ momentum detection ──────────────────
    rsi_series_30m = None
    if len(k30) >= 35:
        closes = [float(k[4]) for k in k30]
        # คำนวณ RSI 3 จุดล่าสุด
        try:
            rsi_series_30m = [
                calc_rsi(closes[:i+1]) for i in range(len(closes)-3, len(closes))
            ]
        except: pass

    log(f"✅ fetch_market: {symbol} ${price:.2f} entry={entry_tf_used} "
        f"5m={bool(tf_5m)} 1h={bool(tf_1h)} 4h={bool(tf_4h)} "
        f"funding={funding_rate}%")

    return {
        "symbol": symbol, "price": price,
        "high": high, "low": low, "change": change,
        "volume": volume, "atr": atr,
        "tf_5m":  tf_5m,   # NEW v3
        "tf_15m": tf_15m,
        "tf_30m": tf_30m,
        "tf_1h":  tf_1h,
        "tf_4h":  tf_4h,
        "entry_tf":       entry_tf_used,
        "funding_rate":   funding_rate,
        "rsi_series_30m": rsi_series_30m,
    }

def build_summary(m, regime, strategy, tf_bk, rb_data=None, direction=None):
    """สร้าง compact JSON payload ส่งให้ Claude (SET1v4 — ลด token 70%)
    BUG-08 FIX: เพิ่ม direction parameter เพื่อให้ Claude รู้ว่ากำลัง validate Long หรือ Short
    """
    t15 = m["tf_15m"] or {}
    t1h = m["tf_1h"]  or {}
    t4h = m["tf_4h"]  or {}

    def r2(v): return round(v, 2) if v is not None else None

    payload = {
        "symbol":    m["symbol"],
        "direction": direction or "N/A",   # BUG-08 FIX: Claude ต้องรู้ direction เพื่อ validate ถูก
        "price":     r2(m["price"]),
        "change_24h":r2(m.get("change", 0)),
        "regime":    regime,
        "strategy":  strategy,
        "funding":   m.get("funding_rate"),
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
        }

    if tf_bk:
        payload["tf_breakdown"] = tf_bk

    return json.dumps(payload, separators=(",", ":"))


# ═══════════════════════════════════════════════════════════
# CLAUDE AI
# ═══════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are a compact crypto futures signal validator (SET1v4).

The bot has already filtered, scored, and selected strategy. Your job: validate gray-zone setups only.

RULES:
- EMA 9/21/200 | Binance Futures | 15m entry, 1h+4h confirmation
- Min R:R 1.5 | Hard SL risk <= 8% | Reject if > 12%
- VOLATILE regime → always NO_TRADE
- Countertrend without rejection confirmation → REJECTED

Respond ONLY with valid JSON, no markdown, no explanation outside JSON:
{
  "verdict": "APPROVED|WEAK_APPROVAL|REJECTED|NO_TRADE",
  "confidence": 0-100,
  "reason_code": "TREND_ALIGNED|MOMENTUM_CONFIRM|STRUCTURE_VALID|RR_OK|COUNTERTREND|WEAK_MOMENTUM|INVALID_STRUCTURE|RR_FAIL|HTF_CONFLICT|VOLATILE_REGIME|FUNDING_RISK|INSUFFICIENT_DATA",
  "ssl": "price_or_null",
  "hsl": "price_or_null",
  "tp1": "price_or_null",
  "tp2": "price_or_null",
  "tp3": "price_or_null",
  "notes": ["max 3 bullets"],
  "risk_flags": []
}"""

def call_claude(symbol, direction, strategy, summary):
    """SET1v4 — compact JSON payload, ลด token ~70%"""
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
        "max_tokens": 400,   # ลดจาก 1000+ → 400 (JSON response เล็กมาก)
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}]
    }
    result = http_post("https://api.anthropic.com/v1/messages", headers, body)
    if not result: raise RuntimeError("No response from Claude")
    if "error" in result: raise RuntimeError(f"Claude: {result['error'].get('message')}")
    return result["content"][0]["text"]

def calc_fallback_levels(price, direction, atr=None):
    """
    Generate fallback TP/SL ถ้า Claude ไม่ provide
    ใช้ % ของราคา — ATR raw ไม่น่าเชื่อถือ (อาจน้อยเกิน)
    LONG:  SSL -1.5% / HSL -2.0% / TP1 +1.5% / TP2 +2.5% / TP3 +4.0%
    SHORT: SSL +1.5% / HSL +2.0% / TP1 -1.5% / TP2 -2.5% / TP3 -4.0%
    """
    if direction.upper() == "LONG":
        ssl = round(price * 0.985, 2)
        hsl = round(price * 0.980, 2)
        tp1 = round(price * 1.015, 2)
        tp2 = round(price * 1.025, 2)
        tp3 = round(price * 1.040, 2)
    else:
        ssl = round(price * 1.015, 2)
        hsl = round(price * 1.020, 2)
        tp1 = round(price * 0.985, 2)
        tp2 = round(price * 0.975, 2)
        tp3 = round(price * 0.960, 2)

    return {
        "ssl": str(ssl), "hsl": str(hsl),
        "tp1": str(tp1), "tp2": str(tp2), "tp3": str(tp3)
    }

def parse_ai(text):
    """SET1v4: JSON-first parser, regex fallback สำหรับ backward compat"""
    # ── Try JSON first (compact response format) ──────────────
    try:
        clean = text.strip()
        # strip markdown fences if any
        if clean.startswith("```"):
            clean = re.sub(r"```[a-z]*\n?", "", clean).strip()
        data = json.loads(clean)

        verdict_raw = data.get("verdict", "NO_TRADE").upper()
        # normalize underscore variants
        verdict_map = {
            "APPROVED":      "APPROVED",
            "WEAK_APPROVAL": "WEAK APPROVAL",
            "WEAK APPROVAL": "WEAK APPROVAL",
            "REJECTED":      "REJECTED",
            "NO_TRADE":      "NO TRADE",
            "NO TRADE":      "NO TRADE",
            "MICRO_BOUNCE":  "MICRO BOUNCE",
        }
        verdict = verdict_map.get(verdict_raw, "NO TRADE")
        conf    = int(data.get("confidence", 50))
        notes   = data.get("notes", [])
        reason  = " | ".join(notes) if notes else data.get("reason_code", "")

        return verdict, conf, {
            "entry":       None,  # bot uses market price
            "ssl":         data.get("ssl"),
            "hsl":         data.get("hsl"),
            "tp1":         data.get("tp1"),
            "tp2":         data.get("tp2"),
            "tp3":         data.get("tp3"),
            "reason":      reason,
            "reason_code": data.get("reason_code", ""),
            "risk_flags":  data.get("risk_flags", []),
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    # ── Regex fallback (legacy prose format) ─────────────────
    def find(pats):
        for p in pats:
            m = re.search(p, text, re.I | re.DOTALL)
            if m:
                v = m.group(1).replace(",","").strip()
                try:
                    float(v)
                    return v
                except:
                    return v
        return None

    verdict  = find([r"(?:RECHECK )?DECISION:\s*(APPROVED|WEAK APPROVAL|REJECTED|NO TRADE)",
                     r"Recheck Decision:\s*(APPROVED|WEAK APPROVAL|REJECTED|NO TRADE)"])
    verdict  = verdict.upper() if verdict else "NO TRADE"
    conf_str = find([r"Confidence Score:\s*(\d+)"])
    conf     = int(conf_str) if conf_str else 50

    return verdict, conf, {
        "entry":  find([r"Better Entry Zone:\s*\$?([\d,]+\.?\d*)",
                        r"Proposed Entry:\s*\$?([\d,]+\.?\d*)"]),
        "ssl":    find([r"Better Soft Stop Loss:\s*\$?([\d,]+\.?\d*)",
                        r"Suggested Soft Stop Loss:\s*\$?([\d,]+\.?\d*)"]),
        "hsl":    find([r"Better Hard Stop Loss:\s*\$?([\d,]+\.?\d*)",
                        r"Suggested Hard Stop Loss:\s*\$?([\d,]+\.?\d*)"]),
        "tp1":    find([r"Better TP1:\s*\$?([\d,]+\.?\d*)",
                        r"TP1:\s*(?:\[.*?\])?\s*\$?([\d,]+\.?\d*)"]),
        "tp2":    find([r"Better TP2:\s*\$?([\d,]+\.?\d*)",
                        r"TP2:\s*(?:\[.*?\])?\s*\$?([\d,]+\.?\d*)"]),
        "tp3":    find([r"Better TP3:\s*\$?([\d,]+\.?\d*)",
                        r"TP3:\s*(?:\[.*?\])?\s*\$?([\d,]+\.?\d*)"]),
        "reason": find([r"Final Verdict:\s*[-–]?\s*\w[\w\s]*[-–]?\s*(.+?)(?:\n|$)"]),
        "reason_code": "LEGACY_PARSE",
        "risk_flags":  [],
    }

# ═══════════════════════════════════════════════════════════
# BUILD SIGNAL — เก็บทุก field สำหรับ analysis
# ═══════════════════════════════════════════════════════════
def build_signal(symbol, direction, m, verdict, conf, levels,
                 ai_text, regime, regime_conf, regime_reasons,
                 strategy, filter_reason, tf_bk, rb_data,
                 gate_path="UNKNOWN", pre_conf=None, claude_called=False):
    """
    SET1v4 — build the canonical signal record.

    gate_path, pre_conf, claude_called are now REQUIRED params so they
    are always present in every log record — no more null values.

    Verdict rules enforced here:
      APPROVED / WEAK APPROVAL → trade fields populated
      REJECTED / NO TRADE      → trade fields forced to None
    """
    S   = INDICATOR_SET
    t15 = m.get("tf_15m") or {}
    t30 = m.get("tf_30m") or {}
    t1h = m.get("tf_1h")  or {}
    t4h = m.get("tf_4h")  or {}

    # ── Tradeable vs blocked ───────────────────────────────────
    is_tradeable = verdict in ("APPROVED", "WEAK APPROVAL")

    entry_val = levels.get("entry") or str(round(m["price"], 2))
    ssl_val   = levels.get("ssl")   if is_tradeable else None
    hsl_val   = levels.get("hsl")   if is_tradeable else None
    tp1_val   = levels.get("tp1")   if is_tradeable else None
    tp2_val   = levels.get("tp2")   if is_tradeable else None
    tp3_val   = levels.get("tp3")   if is_tradeable else None

    reject_reason = ""
    if not is_tradeable:
        reject_reason = levels.get("reason") or f"{verdict}"

    return {
        # ── Identity ──────────────────────────────────────────
        "id":          f"{symbol}_{now_thai().strftime('%Y%m%d_%H%M')}",
        "time":        now_thai().isoformat(),
        "time_thai":   now_thai().strftime('%Y-%m-%d %H:%M TH'),
        "symbol":      symbol,
        "bot_version": cfg.BOT_VERSION,

        # ── Gate tracking (never null) ─────────────────────────
        "gate_path":    gate_path,
        "pre_conf":     pre_conf if pre_conf is not None else conf,
        "claude_called":claude_called,

        # ── Indicator Set ─────────────────────────────────────
        "indicator_set": {
            "name": S["name"], "version": S["version"],
            "configured": S["configured"], "review_after": S["review_after"],
            "RSI_OVERBOUGHT": cfg.RSI_OVERBOUGHT, "RSI_OVERSOLD": cfg.RSI_OVERSOLD,
            "EMA_SHORT": S["EMA_SHORT"], "EMA_MID": S["EMA_MID"], "EMA_LONG": S["EMA_LONG"],
            "EMA_CROSS_BUFFER": S["EMA_CROSS_BUFFER"], "MIN_RR": S["MIN_RR"],
            "MACD": f"{S['MACD_FAST']}/{S['MACD_SLOW']}/{S['MACD_SIGNAL']}",
            "BB": f"{S['BB_PERIOD']} period {S['BB_STD']}SD",
            "VOLUME_AVG": S["VOLUME_AVG"],
            "TF_BIAS": S["TF_BIAS"], "TF_DIRECTION": S["TF_DIRECTION"],
            "TF_ENTRY": S["TF_ENTRY"], "TF_REBOUND": S["TF_REBOUND"],
        },

        # ── Market Regime ─────────────────────────────────────
        "regime":         regime,
        "regime_conf":    regime_conf,
        "regime_reasons": regime_reasons,

        # ── Strategy ──────────────────────────────────────────
        "strategy_used": strategy,
        "filter_passed": True,
        "filter_reason": filter_reason,
        "direction":     direction,

        # ── Price Snapshot ────────────────────────────────────
        "price":  m["price"],
        "change": m["change"],
        "atr":    m.get("atr"),
        "funding_rate": m.get("funding_rate"),

        # ── Decision Log (TF breakdown) ───────────────────────
        "decision_log": {
            "regime":          {"value": regime, "conf": regime_conf, "reasons": regime_reasons},
            "strategy":        strategy,
            "pre_conf":        pre_conf if pre_conf is not None else conf,
            "gate":            {
                "filter": cfg.FILTER_MIN_CONF, "weak": cfg.WEAK_MIN_CONF,
                "claude": cfg.CLAUDE_MIN_CONF, "auto": cfg.AUTO_APPROVE_CONF,
            },
            "tf_4h_bias":      tf_bk.get("4h", {}),
            "tf_1h_direction": tf_bk.get("1h", {}),
            "tf_15m_entry":    tf_bk.get("15m", {}),
            "tf_30m_rebound":  rb_data if strategy == "REBOUND" else {},
            "filter_signals":  filter_reason.split(" | ") if filter_reason else [],
        },

        # ── Indicators Snapshot ───────────────────────────────
        "indicators": {
            "15m": {
                "rsi": t15.get("rsi"), "macd_hist": t15.get("macd_hist"),
                "bb_pct": t15.get("bb_pct"), "bb_width": t15.get("bb_width"),
                "vol_ratio": t15.get("vol_ratio"),
                "ema_short": t15.get("ema_short"), "ema_mid": t15.get("ema_mid"),
                "ema_long":  t15.get("ema_long"),
                "swing_high": (t15.get("swings") or {}).get("swing_high"),
                "swing_low":  (t15.get("swings") or {}).get("swing_low"),
            },
            "30m": {
                "rsi": t30.get("rsi"), "macd_hist": t30.get("macd_hist"),
                "bb_pct": t30.get("bb_pct"), "vol_ratio": t30.get("vol_ratio"),
                "ema_short": t30.get("ema_short"), "ema_mid": t30.get("ema_mid"),
            } if t30 else {},
            "1h": {
                "rsi": t1h.get("rsi"), "macd_hist": t1h.get("macd_hist"),
                "bb_pct": t1h.get("bb_pct"), "bb_width": t1h.get("bb_width"),
                "ema_short": t1h.get("ema_short"), "ema_mid": t1h.get("ema_mid"),
            },
            "4h": {
                "rsi": t4h.get("rsi"), "macd_hist": t4h.get("macd_hist"),
                "bb_pct": t4h.get("bb_pct"), "bb_width": t4h.get("bb_width"),
                "ema_short": t4h.get("ema_short"), "ema_mid": t4h.get("ema_mid"),
            },
        },

        # ── Verdict + Trade levels ─────────────────────────────
        # CRITICAL: trade fields (entry/ssl/tp*) only present for tradeable verdicts.
        # REJECTED / NO TRADE always have None here — enforced above.
        "verdict":       verdict,
        "conf":          conf,
        "entry":         entry_val  if is_tradeable else None,
        "ssl":           ssl_val,
        "hsl":           hsl_val,
        "tp1":           tp1_val,
        "tp2":           tp2_val,
        "tp3":           tp3_val,
        "reason":        levels.get("reason"),
        "reason_code":   levels.get("reason_code", ""),
        "risk_flags":    levels.get("risk_flags", []),
        "reject_reason": reject_reason,
        "ai_text":       ai_text,

        # ── Recheck ───────────────────────────────────────────
        "recheck": None,
    }

# ─── LOG ──────────────────────────────────────────────────
def load_log():
    try:
        if os.path.exists(cfg.LOG_FILE):
            with open(cfg.LOG_FILE) as f: return json.load(f)
    except: pass
    return []

def save_log(logs):
    try:
        with open(cfg.LOG_FILE, "w") as f:
            json.dump(logs[:2000], f, indent=2, ensure_ascii=False)
    except Exception as e: log(f"Save error: {e}")

# ─── TELEGRAM ─────────────────────────────────────────────
def fmt(v):
    if not v: return "—"
    try: return f"${float(v):,.2f}"
    except: return "—"

def check_micro_bounce(m, regime):
    """
    ตรวจ Micro Bounce — ราคา bounce ระยะสั้น (15m/1h) แต่ 4h ยังไม่ confirm
    เงื่อนไข:
      - RSI 15m กำลัง turn up จาก oversold (< 45)
      - RSI 1h กำลัง turn up (< 55)
      - RSI 4h ยังต่ำ (< 40) — ยังไม่ confirm
      - MACD 15m histogram เพิ่งเป็นบวก
    Returns: (found, direction, tp1, tp2, ssl, expire_thai, reason)
    """
    t15 = m.get("tf_15m") or {}
    t1h = m.get("tf_1h")  or {}
    t4h = m.get("tf_4h")  or {}
    t5m = m.get("tf_5m")  or {}

    rsi_15 = t15.get("rsi") or 50
    rsi_1h = t1h.get("rsi") or 50
    rsi_4h = t4h.get("rsi") or 50
    mh_15  = t15.get("macd_hist") or 0
    mh_1h  = t1h.get("macd_hist") or 0
    price  = m["price"]

    # ── LONG Micro Bounce ─────────────────────────────────────
    # 15m/1h bounce ขึ้น แต่ 4h ยังต่ำ = โอกาสสั้น
    if (rsi_15 > 30 and rsi_15 < 55 and      # 15m ออกจาก oversold แล้ว
        rsi_1h  > 28 and rsi_1h  < 55 and    # 1h กำลัง bounce
        rsi_4h  < 42 and                      # 4h ยังต่ำ (ไม่ confirm)
        mh_15   > 0 and                       # MACD 15m เป็นบวก
        mh_1h   > 0):                         # MACD 1h เป็นบวก

        # คำนวณ TP/SL จาก swing levels + ATR
        sw = t15.get("swings") or {}
        sh = sw.get("swing_high") or price * 1.015
        sl = sw.get("swing_low")  or price * 0.985
        atr_pct = 0.008  # 0.8% สำหรับ micro bounce

        tp1 = round(price * (1 + atr_pct * 1.0), 2)   # +0.8%
        tp2 = round(price * (1 + atr_pct * 1.8), 2)   # +1.4%
        ssl = round(price * (1 - atr_pct * 0.8), 2)   # -0.6%

        # หมดอายุใน 2 ชั่วโมง (micro bounce ไม่กินเวลา)
        expire = now_thai() + timedelta(hours=2)
        expire_str = expire.strftime('%H:%M TH')

        reason = (f"RSI 15m={rsi_15:.0f} bounce จาก oversold | "
                  f"RSI 1h={rsi_1h:.0f} ฟื้น | "
                  f"4h RSI={rsi_4h:.0f} ยังต่ำ (ยังไม่ confirm reversal) | "
                  f"MACD 15m/1h เป็นบวก")
        return True, "Long", tp1, tp2, ssl, expire_str, reason

    # ── SHORT Micro Bounce ────────────────────────────────────
    if (rsi_15 < 70 and rsi_15 > 45 and
        rsi_1h  < 72 and rsi_1h  > 45 and
        rsi_4h  > 58 and                      # 4h ยังสูง
        mh_15   < 0 and
        mh_1h   < 0):

        atr_pct = 0.008
        tp1 = round(price * (1 - atr_pct * 1.0), 2)
        tp2 = round(price * (1 - atr_pct * 1.8), 2)
        ssl = round(price * (1 + atr_pct * 0.8), 2)

        expire = now_thai() + timedelta(hours=2)
        expire_str = expire.strftime('%H:%M TH')

        reason = (f"RSI 15m={rsi_15:.0f} กลับตัวลง | "
                  f"RSI 1h={rsi_1h:.0f} อ่อนแรง | "
                  f"4h RSI={rsi_4h:.0f} ยังสูง (ยังไม่ confirm reversal) | "
                  f"MACD 15m/1h เป็นลบ")
        return True, "Short", tp1, tp2, ssl, expire_str, reason

    return False, "N/A", None, None, None, None, None

def send_micro_bounce_alert(symbol, direction, price, tp1, tp2, ssl, expire_thai, reason, m):
    """ส่ง Telegram สำหรับ Micro Bounce Signal"""
    token   = cfg.TELEGRAM_TOKEN
    chat_id = cfg.TELEGRAM_CHAT_ID
    if not token or "YOUR" in token: return False

    sym   = symbol.replace("USDT","")
    dicon = "🟢 LONG" if direction == "Long" else "🔴 SHORT"
    ind   = {"15m": m.get("tf_15m") or {}, "1h": m.get("tf_1h") or {}, "4h": m.get("tf_4h") or {}}
    fr    = m.get("funding_rate")

    msg  = f"⚡ *MICRO BOUNCE* — {dicon} *{sym}/USDT*\n"
    msg += f"_(15m/1h bounce — 4h ยังไม่ confirm)_\n\n"
    msg += f"🕐 *{now_thai().strftime('%H:%M')} TH* | ⏰ *หมดอายุ {expire_thai}*\n\n"
    msg += f"💰 *Entry:*   ${price:,.2f} (ตลาด)\n"
    msg += f"🎯 *TP1:*     ${tp1:,.2f} (+0.8%)\n"
    msg += f"🎯 *TP2:*     ${tp2:,.2f} (+1.4%)\n"
    msg += f"🛡 *Soft SL:* ${ssl:,.2f} (-0.6%)\n\n"
    msg += f"📊 RSI 15m:{ind['15m'].get('rsi','—')} | 1h:{ind['1h'].get('rsi','—')} | 4h:{ind['4h'].get('rsi','—')}\n"
    if fr is not None:
        msg += f"💸 Funding: {fr:+.4f}%\n"
    msg += f"\n⚠️ *ความเสี่ยง:* เทรดได้ถ้า 4h RSI ยังไม่ขึ้นเกิน 40\n"
    msg += f"❌ *ยกเลิก signal นี้ถ้าเกิน {expire_thai}*\n"
    msg += f"\n💬 {reason}\n"
    msg += f"\n_⚡ sasi.asia/dashboard · {cfg.BOT_VERSION} Micro_"

    params = urllib.parse.urlencode({"chat_id":chat_id,"text":msg,"parse_mode":"Markdown"})
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage?{params}"),
            timeout=10
        ) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        log(f"Telegram micro bounce error: {e}")
        return False

# ═══════════════════════════════════════════════════════════
# TELEGRAM TEMPLATES — SET1v4
# Rule: TRADEABLE signals (APPROVED, WEAK APPROVAL) → full trade template
#       REJECTED (Claude rejects) → blocked template, NO trade fields
#       NO TRADE / FILTERED / WEAK SIGNAL → NOT sent to Telegram
# ═══════════════════════════════════════════════════════════

def _tg_rsi_line(ind):
    t15 = ind.get("15m", {}); t1h = ind.get("1h", {}); t4h = ind.get("4h", {})
    r15 = t15.get("rsi","—"); r1h = t1h.get("rsi","—"); r4h = t4h.get("rsi","—")
    return f"📊 *RSI* 15m:{r15} | 1h:{r1h} | 4h:{r4h}\n"

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
    icon = {"TRENDING":"📈","RANGING":"↔️","VOLATILE":"⚡","MIXED":"❓"}.get(regime,"🔸")
    return f"{icon} {regime}"

def _has_required_trade_fields(sig):
    """Main Telegram feed accepts only tradeable signals with usable levels."""
    return bool(
        sig.get("symbol")
        and sig.get("direction")
        and sig.get("entry")
        and sig.get("tp1")
        and (sig.get("hsl") or sig.get("ssl"))
    )

def send_telegram(sig):
    """
    Route ตาม verdict — APPROVED/WEAK APPROVAL เท่านั้นที่ส่ง Telegram

    ✅ APPROVED      → _send_telegram_trade (Entry/TP/SL)
    ⚠️ WEAK APPROVAL → _send_telegram_trade (Entry/TP/SL + ลด size note)
    ⚠️ REJECTED      → ไม่ส่ง (ตาม spec — Claude block ไม่ออก Telegram)
    ❌ อื่นๆ          → ไม่ส่ง (safeguard)

    IMPORTANT: ต้องเรียก should_notify() ก่อนเรียกฟังก์ชันนี้เสมอ
    """
    token   = cfg.TELEGRAM_TOKEN
    chat_id = cfg.TELEGRAM_CHAT_ID
    if not token or "YOUR" in token: return False

    verdict = sig.get("verdict", "")
    if verdict in ("APPROVED", "WEAK APPROVAL"):
        if not _has_required_trade_fields(sig):
            log(f"  ⛔ send_telegram blocked: missing trade levels for verdict={verdict}")
            return False
        return _send_telegram_trade(sig, token, chat_id)

    # REJECTED / NO TRADE / FILTERED / WEAK SIGNAL → ห้ามส่ง
    log(f"  ⛔ send_telegram blocked: verdict={verdict} — ไม่ส่ง Telegram")
    return False

def _send_telegram_trade(sig, token, chat_id):
    """✅ APPROVED / ⚠️ WEAK APPROVAL — full trade alert with Entry/TP/SL"""
    verdict = sig["verdict"]
    sym     = sig["symbol"].replace("USDT","")
    dicon   = "🟢 LONG" if sig["direction"] == "Long" else "🔴 SHORT"
    icon    = "✅" if verdict == "APPROVED" else "⚠️"
    size_note = "" if verdict == "APPROVED" else "  _(ลด position size)_"
    gate    = sig.get("gate_path", "—")
    conf    = sig.get("conf", "—")
    regime  = sig.get("regime", "—")
    strat   = sig.get("strategy_used", "—")
    ind     = sig.get("indicators", {})
    t15     = ind.get("15m", {})
    fr      = sig.get("funding_rate") or (sig.get("indicators") or {}).get("funding_rate")

    msg  = f"{icon} *{verdict}* — {dicon} *{sym}/USDT*{size_note}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🕐 *{now_thai().strftime('%H:%M')} TH* | Conf: *{conf}/100*\n"
    msg += f"📍 {_fmt_regime(regime)} | Strategy: *{strat}*\n"
    msg += f"🔀 Gate: `{gate}`\n\n"
    msg += f"💰 *Entry:*   {fmt(sig.get('entry'))}\n"
    msg += f"🎯 *TP1:*     {fmt(sig.get('tp1'))}\n"
    msg += f"🎯 *TP2:*     {fmt(sig.get('tp2'))}\n"
    msg += f"🎯 *TP3:*     {fmt(sig.get('tp3'))}\n"
    msg += f"🛡 *Soft SL:* {fmt(sig.get('ssl'))}\n"
    msg += f"🛑 *Hard SL:* {fmt(sig.get('hsl'))}\n\n"
    msg += _tg_rsi_line(ind)
    msg += f"📈 *MACD hist* 15m: {t15.get('macd_hist','—')}\n"
    if t15.get("vol_ratio"): msg += f"📦 *Volume:* {t15['vol_ratio']:.1f}x avg\n"
    if fr is not None: msg += f"💸 *Funding:* {fr:+.4f}%\n"
    if sig.get("reason"):
        short_reason = str(sig["reason"])[:200]
        msg += f"\n💬 {short_reason}\n"
    msg += f"\n_⚡ sasi.asia/dashboard · {cfg.BOT_VERSION}_"
    return _tg_send(token, chat_id, msg)

def _send_telegram_rejected(sig, token, chat_id):
    """
    ❌ REJECTED — Claude rejected this setup.
    Shows WHY it was rejected — NO Entry/TP/SL (not a trade alert).
    """
    sym    = sig["symbol"].replace("USDT","")
    dicon  = "🟢 LONG" if sig["direction"] == "Long" else "🔴 SHORT"
    regime = sig.get("regime", "—")
    strat  = sig.get("strategy_used", "—")
    conf   = sig.get("conf", "—")
    rc     = sig.get("reason_code") or sig.get("levels", {}) and sig.get("levels",{}).get("reason_code") or "—"
    reason = sig.get("reject_reason") or sig.get("reason") or "ไม่มีรายละเอียด"
    ind    = sig.get("indicators", {})

    msg  = f"❌ *REJECTED* — {dicon} *{sym}/USDT*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🕐 *{now_thai().strftime('%H:%M')} TH* | Conf: *{conf}/100*\n"
    msg += f"📍 {_fmt_regime(regime)} | Strategy: *{strat}*\n\n"
    msg += f"🚫 *Claude ปฏิเสธ signal นี้*\n"
    if rc and rc != "—":
        msg += f"📋 Reason Code: `{rc}`\n"
    msg += f"💬 {str(reason)[:300]}\n\n"
    msg += _tg_rsi_line(ind)
    msg += f"\n_ไม่เทรด — ดูรายละเอียดใน dashboard_\n"
    msg += f"_⚡ sasi.asia/dashboard · {cfg.BOT_VERSION}_"
    return _tg_send(token, chat_id, msg)

def should_notify(verdict):
    """
    Gate ว่า verdict ใดควรส่ง Telegram

    ตาม spec ที่ต้องการ:
      ✅ APPROVED      → ส่ง (trade alert)
      ⚠️ WEAK APPROVAL → ส่ง (trade alert, ลด size)
      ⚠️ REJECTED      → ไม่ส่ง (Claude block — ไม่ส่ง Telegram เลย)
      ❌ NO TRADE      → ไม่ส่ง (bot block ก่อน Claude)
      ❌ FILTERED      → ไม่ส่ง
         WEAK SIGNAL   → ไม่ส่ง
    """
    return verdict in ("APPROVED", "WEAK APPROVAL")

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
def main():
    log("="*60)
    log(f"🚀 Crypto Signal Bot — {cfg.BOT_VERSION}")
    log("   Multi-TF 4h→1h→15m + Rebound 30m + Market Regime")
    log(f"   Review: {INDICATOR_SET['review_after']}")
    log("="*60)

    logs         = load_log()
    claude_calls = 0
    skipped      = 0

    for symbol in cfg.SYMBOLS:
        log(f"\n📊 {symbol}")
        m = fetch_market(symbol)
        if not m:
            log("  ❌ Failed to fetch"); continue

        t15 = m["tf_15m"] or {}; t1h = m["tf_1h"] or {}; t4h = m["tf_4h"] or {}
        log(f"  Price: ${m['price']:,.2f} | RSI 15m:{t15.get('rsi')} 1h:{t1h.get('rsi')} 4h:{t4h.get('rsi')}")

        # ── Step 1: Detect Regime ─────────────────────────────
        regime, regime_conf, regime_reasons = detect_regime(t1h, t4h, m["price"])
        log(f"  📍 Regime: {regime} (conf:{regime_conf}%)")

        # ── Step 1.5: Micro Bounce Check ─────────────────────
        # ตรวจ bounce ระยะสั้น 15m/1h แม้ 4h ยังไม่ confirm
        # Gate: ส่ง Telegram เฉพาะเมื่อ NOTIFY_ON == "all" หรือ "approved_weak"
        mb_found, mb_dir, mb_tp1, mb_tp2, mb_ssl, mb_expire, mb_reason = \
            check_micro_bounce(m, regime)
        if mb_found:
            log(f"  ⚡ MICRO BOUNCE: {mb_dir} | expire={mb_expire} | {mb_reason[:60]}")
            ok = send_micro_bounce_alert(
                symbol, mb_dir, m["price"],
                mb_tp1, mb_tp2, mb_ssl, mb_expire, mb_reason, m
            )
            log(f"  📱 Micro Bounce Telegram: {'✅' if ok else '❌'}")

            # ── บันทึก signal_log สำหรับ recheck ────────────────
            th_now = now_thai()
            expire_dt = th_now + timedelta(hours=2)
            mb_entry = {
                "id":          f"{symbol}_MB_{th_now.strftime('%Y%m%d_%H%M')}",
                "time":        th_now.isoformat(),
                "time_thai":   th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol":      symbol,
                "price":       m["price"],
                "change":      m["change"],
                "bot_version": cfg.BOT_VERSION,
                "gate_path":   "MICRO_BOUNCE",
                "pre_conf":    45,
                "claude_called": False,
                "indicator_set": {"name": INDICATOR_SET["name"], "version": INDICATOR_SET["version"]},
                "regime":      regime,
                "regime_conf": regime_conf,
                "strategy_used": "MICRO_BOUNCE",
                "filter_passed": True,
                "direction":   mb_dir,
                "verdict":     "MICRO BOUNCE",
                "conf":        45,
                "entry":       str(m["price"]),
                "tp1":         str(mb_tp1),
                "tp2":         str(mb_tp2),
                "ssl":         str(mb_ssl),
                "hsl":         str(mb_ssl),
                "expire_time": expire_dt.isoformat(),
                "expire_thai": mb_expire,
                "filter_reason": mb_reason,
                "reject_reason": "",
                "reason_code": "MICRO_BOUNCE",
                "risk_flags":  [],
                "indicators": {
                    "15m": {"rsi": t15.get("rsi"), "macd_hist": t15.get("macd_hist")},
                    "1h":  {"rsi": t1h.get("rsi"), "macd_hist": t1h.get("macd_hist")},
                    "4h":  {"rsi": t4h.get("rsi")},
                },
                "funding_rate": m.get("funding_rate"),
                "recheck": None,
            }
            logs.insert(0, mb_entry)
            log(f"  💾 บันทึก MICRO BOUNCE signal — หมดอายุ {mb_expire}")

        # ── Step 2: VOLATILE → งด ─────────────────────────────
        # SPEC: VOLATILE = bot ปฏิเสธก่อน → NO TRADE (บันทึก log, ไม่ส่ง Telegram)
        if regime == "VOLATILE":
            log(f"  ⛔ VOLATILE — งดเทรด → NO TRADE")
            skipped += 1
            th_now = now_thai()
            base = {
                "id": f"{symbol}_{th_now.strftime('%Y%m%d_%H%M')}",
                "time": th_now.isoformat(),
                "time_thai": th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol": symbol, "price": m["price"], "change": m["change"],
                "bot_version": cfg.BOT_VERSION,
                "gate_path": "VOLATILE_BLOCK",
                "pre_conf": 0, "claude_called": False,
                "indicator_set": {"name": INDICATOR_SET["name"], "version": INDICATOR_SET["version"]},
                "regime": regime, "regime_conf": regime_conf, "regime_reasons": regime_reasons,
                "strategy_used": "NONE", "filter_passed": False,
                "filter_reason": "VOLATILE market — งดเทรด",
                "verdict": "NO TRADE", "conf": 0, "direction": "N/A",
                "reject_reason": "ตลาด VOLATILE — ATR สูงเกิน งดเทรดทุก strategy",
                "indicators": {
                    "15m": {"rsi": t15.get("rsi"), "macd_hist": t15.get("macd_hist")},
                    "1h":  {"rsi": t1h.get("rsi")},
                    "4h":  {"rsi": t4h.get("rsi")},
                },
                "decision_log": {"regime": {"value": regime, "reasons": regime_reasons}},
                "recheck": None,
            }
            logs.insert(0, base); time.sleep(1); continue

        # ── Step 3: เลือก Strategy ────────────────────────────
        passed = False; direction = "N/A"
        strategy = "NONE"; filter_reason = ""
        tf_bk = {}; rb_data = {}
        pre_conf = 0
        block_reason_code = ""

        if regime == "TRENDING":
            strategy = "TREND_FOLLOW"
            passed, direction, reasons, tf_bk = check_multi_tf(
                m["tf_4h"], m["tf_1h"], m["tf_15m"], m["price"])
            filter_reason = " | ".join(reasons)
            pre_conf = 70 if passed else (50 if tf_bk.get("4h",{}).get("bias") != "NEUTRAL" else 20)
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
            # ใช้ /13 เสมอ (single scale)
            pre_conf = min(round(max(long_score, short_score) / cfg.REBOUND_MAX_SCORE * 100), 95)
            # Persistence bonus
            persist_bonus = get_persistence_bonus(symbol, direction, logs)
            if persist_bonus and passed:
                pre_conf = min(pre_conf + persist_bonus, 95)
                log(f"  🔄 Persistence bonus +{persist_bonus}% → pre_conf={pre_conf}%")
            log(f"  ↔️  Strategy: REBOUND | {'✅ PASS' if passed else '⏭ SKIP'}: {filter_reason[:80]}")
            log(f"  📊 Pre-score: {max(long_score,short_score)}/{cfg.REBOUND_MAX_SCORE} → Confidence {pre_conf}% | Funding={m.get('funding_rate')}%")

        # ── Step 4+5: 5-Tier Gate Architecture (SET1v4) ──────────
        #
        #  Tier 1: pre_conf < FILTER_MIN_CONF (25)  → FILTERED
        #  Tier 2: 25 <= pre_conf < WEAK_MIN_CONF (35) → WEAK SIGNAL (log only)
        #  Tier 3: 35 <= pre_conf < CLAUDE_MIN_CONF (50) → bot-only NO TRADE / WEAK
        #  Tier 4: 50 <= pre_conf < AUTO_APPROVE_CONF (70) → Claude validates
        #  Tier 5: pre_conf >= 70 + strong alignment → AUTO APPROVED by bot

        def _make_base_log(verdict_val, gate_path, claude_called=False):
            th_now = now_thai()
            return {
                "id":        f"{symbol}_{th_now.strftime('%Y%m%d_%H%M')}",
                "time":      th_now.isoformat(),
                "time_thai": th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol": symbol, "price": m["price"], "change": m["change"],
                "bot_version": cfg.BOT_VERSION,
                "indicator_set": {"name": INDICATOR_SET["name"], "version": INDICATOR_SET["version"]},
                "regime": regime, "regime_conf": regime_conf,
                "strategy_used": strategy, "direction": direction,
                "pre_conf": pre_conf, "verdict": verdict_val,
                "conf": pre_conf, "gate_path": gate_path,
                "claude_called": claude_called,
                "filter_passed": passed,
                "filter_reason": filter_reason,
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
                    "gate": {
                        "filter": cfg.FILTER_MIN_CONF, "weak": cfg.WEAK_MIN_CONF,
                        "claude": cfg.CLAUDE_MIN_CONF, "auto": cfg.AUTO_APPROVE_CONF,
                    },
                    "tf_30m_rebound": rb_data if strategy == "REBOUND" else {},
                    "tf_breakdown": tf_bk,
                },
                "recheck": None,
            }

        # ── Tier 1: FILTERED ──────────────────────────────────
        # SPEC: score ต่ำเกิน → ทิ้งทันที ไม่บันทึกใน log ด้วยซ้ำ
        if pre_conf < cfg.FILTER_MIN_CONF:
            skipped += 1
            log(f"  🗑 TIER1: pre_conf={pre_conf}% < {cfg.FILTER_MIN_CONF}% → FILTERED (ไม่บันทึก log)")
            time.sleep(1); continue

        # ── Tier 2: WEAK SIGNAL (log only, no Claude) ─────────
        if pre_conf < cfg.WEAK_MIN_CONF:
            skipped += 1
            log(f"  📋 TIER2: pre_conf={pre_conf}% → WEAK SIGNAL (log only)")
            logs.insert(0, _make_base_log("WEAK SIGNAL", "TIER2_WEAK"))
            time.sleep(1); continue

        # ── Tier 3: Bot-only decision (35-49%) ────────────────
        # ถ้า filter ไม่ผ่าน และ pre_conf ยังต่ำ → bot says NO TRADE
        if pre_conf < cfg.CLAUDE_MIN_CONF:
            if not passed and not block_reason_code == "FILTER_OVERRIDE":
                skipped += 1
                log(f"  🤖 TIER3: pre_conf={pre_conf}% + filter fail → bot NO TRADE (no Claude)")
                entry = logs[0] if logs else {}
                rec = _make_base_log("NO TRADE", "TIER3_BOT_REJECT")
                rec["reject_reason"] = f"Bot: pre_conf={pre_conf}% in bot-decide zone, filter not passed"
                logs.insert(0, rec)
                time.sleep(1); continue
            else:
                # filter ผ่าน แต่ pre_conf ยังต่ำ → WEAK SIGNAL ไม่เรียก Claude
                skipped += 1
                log(f"  📋 TIER3: pre_conf={pre_conf}% filter passed but below Claude gate → WEAK SIGNAL")
                logs.insert(0, _make_base_log("WEAK SIGNAL", "TIER3_WEAK_PASSED"))
                time.sleep(1); continue

        # ── Tier 5: AUTO APPROVE (>= 70% + alignment) ─────────
        # Check ก่อนเรียก Claude — ถ้า strong enough → approve ทันที
        auto_approved = False
        if pre_conf >= cfg.AUTO_APPROVE_CONF and passed:
            # ตรวจ strong alignment: EMA 15m + 1h ต้องเข้าทิศเดียวกัน
            ema_15m_bull = t15.get("ema_short",0) and t15.get("ema_mid",0) and t15.get("ema_short") > t15.get("ema_mid")
            ema_1h_bull  = t1h.get("ema_short",0) and t1h.get("ema_mid",0) and t1h.get("ema_short") > t1h.get("ema_mid")
            if direction == "Long" and ema_15m_bull and ema_1h_bull:
                auto_approved = True
            elif direction == "Short" and not ema_15m_bull and not ema_1h_bull:
                auto_approved = True

        if auto_approved:
            log(f"  ✅ TIER5: pre_conf={pre_conf}% ≥ {cfg.AUTO_APPROVE_CONF}% + strong alignment → AUTO APPROVED")
            fallback = calc_fallback_levels(m["price"], direction, m.get("atr", 0))
            levels = fallback.copy()
            levels["entry"]       = str(round(m["price"], 2))
            levels["reason"]      = f"Bot AUTO APPROVE: pre_conf={pre_conf}%"
            levels["reason_code"] = "AUTO_APPROVED"
            verdict, conf = "APPROVED", pre_conf
            sig = build_signal(symbol, direction, m, verdict, conf, levels,
                               f"BOT_AUTO:{pre_conf}", regime, regime_conf, regime_reasons,
                               strategy, filter_reason, tf_bk, rb_data,
                               gate_path="TIER5_AUTO_APPROVED",
                               pre_conf=pre_conf,
                               claude_called=False)
            logs.insert(0, sig)
            if should_notify(verdict):
                ok = send_telegram(sig)
                log(f"  📱 Telegram: {'✅' if ok else '❌'}")
            time.sleep(1); continue

        # ── Tier 4: Claude validates (50-69%) ─────────────────
        log(f"  🤖 TIER4: pre_conf={pre_conf}% → Claude validation ({cfg.CLAUDE_MODEL})")
        try:
            summary  = build_summary(m, regime, strategy, tf_bk, rb_data, direction=direction)
            ai_text  = call_claude(symbol, direction, strategy, summary)
            verdict, conf, levels = parse_ai(ai_text)
            claude_calls += 1

            if not levels.get("entry"): levels["entry"] = str(round(m["price"], 2))

            # ── TP/SL fallback สำหรับ TRADEABLE signals เท่านั้น ──
            if verdict in ("APPROVED", "WEAK APPROVAL"):
                fallback = calc_fallback_levels(m["price"], direction, m.get("atr", 0))
                if not levels.get("ssl"): levels["ssl"] = fallback["ssl"]
                if not levels.get("hsl"): levels["hsl"] = fallback["hsl"]
                if not levels.get("tp1"): levels["tp1"] = fallback["tp1"]
                if not levels.get("tp2"): levels["tp2"] = fallback["tp2"]
                if not levels.get("tp3"): levels["tp3"] = fallback["tp3"]
            # REJECTED / NO TRADE → build_signal enforces None for trade fields

            sig = build_signal(symbol, direction, m, verdict, conf, levels,
                               ai_text, regime, regime_conf, regime_reasons,
                               strategy, filter_reason, tf_bk, rb_data,
                               gate_path="TIER4_CLAUDE",
                               pre_conf=pre_conf,
                               claude_called=True)

            log(f"  → {verdict} | Conf:{conf} | Entry:{fmt(levels.get('entry'))} "
                f"| TP1:{fmt(levels.get('tp1'))} | SL:{fmt(levels.get('ssl'))}")

            logs.insert(0, sig)

            if should_notify(verdict):
                ok = send_telegram(sig)
                log(f"  📱 Telegram: {'✅' if ok else '❌'}")
            else:
                log(f"  📱 Telegram: skipped ({verdict})")

        except Exception as e:
            log(f"  ❌ Error: {e}")
            th_now = now_thai()
            logs.insert(0, {
                "id": f"{symbol}_{th_now.strftime('%Y%m%d_%H%M')}",
                "time": th_now.isoformat(),
                "time_thai": th_now.strftime('%Y-%m-%d %H:%M TH'),
                "symbol": symbol, "price": m["price"],
                "indicator_set": {"name": INDICATOR_SET["name"]},
                "regime": regime, "strategy_used": strategy,
                "verdict": "ERROR", "error_msg": str(e),
                "reject_reason": f"ERROR: {str(e)}",
                "recheck": None,
            })

        time.sleep(2)

    save_log(logs)
    log(f"\n{'='*60}")
    log(f"✅ Done — Claude: {claude_calls}/{len(cfg.SYMBOLS)} | Skipped: {skipped}")
    log(f"{'='*60}")

if __name__ == "__main__":
    main()
