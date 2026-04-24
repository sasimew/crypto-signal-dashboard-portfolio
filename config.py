"""
Crypto Signal Bot — Configuration SET1v4
════════════════════════════════════════
SET1v4 — Bot-first architecture + 5-Tier Gate
Configured: 2026-04-21
Review:     2026-04-27
════════════════════════════════════════
Single source of truth สำหรับทุก threshold
bot.py, api_server.py, recheck.py ใช้ค่าจากที่นี่เท่านั้น
"""
import os

BOT_VERSION = "SET1v4"

# ── API KEYS ──────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "YOUR_CLAUDE_API_KEY")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN",    "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID",  "YOUR_CHAT_ID")

# ── TRADING ───────────────────────────────────────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

# ── INDICATORS ────────────────────────────────────────────────
RSI_PERIOD      = 14
RSI_OVERBOUGHT  = 68    # SET1v3: 68
RSI_OVERSOLD    = 32    # SET1v3: 32
EMA_SHORT       = 9
EMA_MID         = 21
EMA_LONG        = 200
EMA_CROSS_BUFFER= 0.0005
MIN_RR          = 1.5
MAX_RISK        = 8
MACD_FAST       = 12
MACD_SLOW       = 26
MACD_SIGNAL     = 9
BB_PERIOD       = 20
BB_STD          = 2.0
VOLUME_AVG      = 20
SWING_LOOKBACK  = 20

# ── TIMEFRAMES ────────────────────────────────────────────────
TF_MICRO      = "5m"
TF_ENTRY      = "15m"
TF_ENTRY_ALT  = "30m"
TF_DIRECTION  = "1h"
TF_BIAS       = "4h"
TF_REBOUND    = "30m"

# ── REGIME ────────────────────────────────────────────────────
REGIME_ATR_TREND    = 1.0
REGIME_ATR_VOLATILE = 3.0
REGIME_BB_RANGING   = 3.5
REGIME_EMA_DIFF     = 0.2

# ── REBOUND ───────────────────────────────────────────────────
REBOUND_BB_LOW        = 0.30   # BB%B < 0.30 → near lower band
REBOUND_BB_HIGH       = 0.70   # BB%B > 0.70 → near upper band
REBOUND_VOL_MIN       = 0.5    # volume ratio threshold
REBOUND_SWING_BUFFER  = 0.008  # ±0.8% swing zone
REBOUND_MIN_SCORE     = 3      # minimum score to pass filter
REBOUND_MAX_SCORE     = 13     # max possible score (denominator)
REBOUND_1H_RSI_GUARD  = 25     # block rebound if 1h RSI < 25 (extreme)
REBOUND_4H_BLOCK_SHORT= 38     # block SHORT rebound if 4h RSI < 38
REBOUND_4H_BLOCK_LONG = 65     # block LONG rebound if 4h RSI > 65

# ── COUNTERTREND BLOCK ────────────────────────────────────────
# ป้องกัน REBOUND SHORT ที่สวน trend 1h+15m
BLOCK_COUNTERTREND    = True   # เปิด/ปิด countertrend filter
CT_EMA_CONFLICT_BLOCK = True   # block ถ้า EMA 1h+15m ขัดแย้ง

# ── FUNDING RATE BONUS ────────────────────────────────────────
FUNDING_BONUS_HIGH = -0.03     # < -0.03% → +2 (short squeeze risk)
FUNDING_BONUS_MED  = -0.01     # < -0.01% → +1

# ── 24H DROP BONUS ────────────────────────────────────────────
DROP_BONUS_HIGH = -5.0         # < -5% → +2
DROP_BONUS_MED  = -3.0         # < -3% → +1

# ── MULTI-TF ──────────────────────────────────────────────────
MTF_4H_RSI_BULL    = 40
MTF_4H_RSI_BEAR    = 60
MTF_1H_RSI_CONFIRM = 48

# ── CONFIDENCE GATE (SET1v4 — bot-first architecture) ────────
# pre_conf = score / REBOUND_MAX_SCORE * 100
# Gate flow:
#   < 25              → FILTERED (no log)
#   25–34             → WEAK SIGNAL (log only, no Claude)
#   35–49             → bot-only decision (NO TRADE or WEAK SIGNAL)
#   50–69             → Claude validation
#   >= 70 + alignment → AUTO APPROVED by bot
FILTER_MIN_CONF  = 25   # < 25%  → FILTERED
WEAK_MIN_CONF    = 35   # 25-34% → WEAK SIGNAL
BOT_DECIDE_CONF  = 50   # 35-49% → bot decides without Claude
CLAUDE_MIN_CONF  = 50   # >= 50% → Claude validation
AUTO_APPROVE_CONF= 70   # >= 70% + strong alignment → auto APPROVED

# ── SIGNAL PERSISTENCE BONUS ─────────────────────────────────
# ถ้า signal เดิมซ้ำกัน 2+ รอบติดกัน → +confidence bonus
PERSISTENCE_BONUS = 5   # +5% confidence ถ้า signal ซ้ำ 2+ รอบ

# ── MICRO BOUNCE ──────────────────────────────────────────────
MICRO_RSI_15M_MIN  = 30    # RSI 15m ต่ำสุด
MICRO_RSI_15M_MAX  = 55    # RSI 15m สูงสุด
MICRO_RSI_1H_MIN   = 28
MICRO_RSI_1H_MAX   = 55
MICRO_RSI_4H_MAX   = 42    # 4h ยังต่ำ (ไม่ confirm)
MICRO_EXPIRE_HOURS = 2     # หมดอายุใน 2 ชั่วโมง
MICRO_TP1_PCT      = 0.008 # +0.8%
MICRO_TP2_PCT      = 0.014 # +1.4%
MICRO_SL_PCT       = 0.006 # -0.6%

# ── NOTIFICATION ──────────────────────────────────────────────
# "approved_weak" → ส่ง APPROVED + WEAK APPROVAL เท่านั้น (recommended)
# "all"           → ส่งทุก verdict รวม REJECTED (admin/debug mode)
# "approved"      → ส่งเฉพาะ APPROVED
NOTIFY_ON = "approved_weak"

# ── SERVER ────────────────────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000

# ── FILES ─────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(_DATA_DIR, exist_ok=True)

LOG_FILE    = os.path.join(_DATA_DIR, "signal_log.json")
RECHECK_LOG = os.path.join(_DATA_DIR, "recheck_log.json")

# Backward compatibility: older VPS installs stored logs in project root.
_LEGACY_LOG_FILE = os.path.join(BASE_DIR, "signal_log.json")
_LEGACY_RECHECK_LOG = os.path.join(BASE_DIR, "recheck_log.json")
if not os.path.exists(LOG_FILE) and os.path.exists(_LEGACY_LOG_FILE):
    LOG_FILE = _LEGACY_LOG_FILE
if not os.path.exists(RECHECK_LOG) and os.path.exists(_LEGACY_RECHECK_LOG):
    RECHECK_LOG = _LEGACY_RECHECK_LOG

# ── CLAUDE ────────────────────────────────────────────────────
CLAUDE_MODEL      = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 1200
