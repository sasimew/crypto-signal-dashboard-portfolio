"""
config_set2.py — Crypto Signal Bot SET2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
การเปลี่ยนแปลงจาก SET1v4:
  [REGIME]   MACD4h weight +1→+3/+5 tier, ATR ต้อง pair กับ MACD ถึงจะ RANGING
  [SCORING]  rsi_turning_up/down weight +2→+3, margin threshold ขึ้น
  [GATE]     Claude threshold ขึ้น (50→55), pre-filter กัน wasted calls
  [SHORT]    เงื่อนไขเข้มขึ้น — ต้องมี 4h bearish + rsi_turning_down
  [TREND]    check_multi_tf threshold ผ่อนลง ให้ TRENDING trigger ได้

Review: 2026-05-03
"""
import os

# ─── Identity ────────────────────────────────────────────────
BOT_VERSION     = "SET2v2"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL    = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_MIN_CONF = 55   # ↑ จาก 50 — กัน low-quality gray zone

# ─── Symbols ─────────────────────────────────────────────────
SYMBOLS       = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
CRON_SYMBOLS  = ["BTCUSDT"]

# ─── RSI Thresholds ──────────────────────────────────────────
RSI_OVERBOUGHT = 68   # เหมือนเดิม
RSI_OVERSOLD   = 32   # เหมือนเดิม

# ─── EMA ─────────────────────────────────────────────────────
EMA_SHORT      = 9
EMA_MID        = 21
EMA_LONG       = 200
EMA_CROSS_BUFFER = 0.0003   # เหมือนเดิม

# ─── MACD ────────────────────────────────────────────────────
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9

# ─── Bollinger Bands ─────────────────────────────────────────
BB_PERIOD   = 20
BB_STD      = 2.0

# ─── Volume ──────────────────────────────────────────────────
VOLUME_AVG       = 20
REBOUND_VOL_MIN  = 0.5   # เหมือนเดิม (vol ไม่แยก approved/rejected ได้)

# ─── Swing ───────────────────────────────────────────────────
SWING_LOOKBACK       = 20
REBOUND_SWING_BUFFER = 0.012

# ─── TF Names ────────────────────────────────────────────────
TF_BIAS      = "4h"
TF_DIRECTION = "1h"
TF_ENTRY     = "15m"
TF_REBOUND   = "30m"

# ─── R:R ─────────────────────────────────────────────────────
MIN_RR = 1.5

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [REGIME] — แก้ Bug-007: MACD4h น้ำหนักจริง
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SET1 problem: MACD4h ได้แค่ +1 TRENDING ทุกกรณี
# → ตลาด bearish (-130 avg MACD) ยังถูกเรียก RANGING 100%

# ATR threshold (% ของ price)
REGIME_ATR_TREND    = 0.20   # > 0.20% → TRENDING (ลดจาก 0.25 ให้ trigger ง่ายขึ้น)
REGIME_ATR_VOLATILE = 0.60   # > 0.60% → VOLATILE

# BB Width threshold (%)
REGIME_BB_RANGING   = 2.0    # < 2.0% → RANGING signal

# EMA4h diff threshold (%)
REGIME_EMA_DIFF     = 0.25   # > 0.25% → TRENDING signal (ลดจาก 0.30)

# [NEW SET2] MACD4h magnitude tiers
REGIME_MACD4H_STRONG  = 80   # |MACD4h hist| > 80  → +4 TRENDING (ชัดเจน)
REGIME_MACD4H_MEDIUM  = 30   # |MACD4h hist| > 30  → +2 TRENDING
REGIME_MACD4H_WEAK    = 5    # |MACD4h hist| > 5   → +1 TRENDING (เดิม)

# [NEW SET2] RANGING ต้องมีทั้ง low ATR AND flat MACD4h (ไม่ใช่แค่ ATR อย่างเดียว)
REGIME_RANGING_REQUIRE_FLAT_MACD = True   # True = RANGING ต้องการ |MACD4h|<MACD4H_WEAK

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [REBOUND] — Scoring calibration จาก data analysis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data insight:
#   rsi_turning_up + long_score≥7 → 61% approval (vs 8.6% baseline)
#   rsi_turning_up alone           → 38% approval
#   score margin ≥6                → 57% approval
#   score margin ≤3                → <1% approval (waste)

REBOUND_BB_LOW    = 0.30   # เหมือนเดิม
REBOUND_BB_HIGH   = 0.70   # เหมือนเดิม
REBOUND_MAX_SCORE = 16     # ↑ จาก 13 (เพิ่ม weight ให้ rsi_turn + margin)
REBOUND_MIN_SCORE = 3      # เหมือนเดิม
REBOUND_1H_RSI_GUARD = 25  # เหมือนเดิม

# [NEW SET2] rsi_turning_up/down weight = 3 (จาก 2)
#   ข้อมูล: approved=86% มี turn_up vs rejected=42% → weight ต้องสูงกว่า core signals
REBOUND_RSI_TURN_WEIGHT = 3   # เดิม hardcoded ที่ 2

# [NEW SET2] minimum margin (L-S หรือ S-L) เพื่อผ่าน filter
#   ข้อมูล: margin ≤3 → <1% actionable, margin ≥6 → 57% actionable
REBOUND_MIN_MARGIN  = 4    # ต้องมี margin ≥4 ถึงส่ง Claude

# [NEW SET2] minimum long_score ที่จะให้ Claude validate
#   ข้อมูล: score≥7 → 53% approval, score<5 → 1% approval
REBOUND_MIN_SCORE_FOR_CLAUDE = 7  # ต้องได้ ≥7 ถึงเรียก Claude

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [SHORT] — เงื่อนไขเข้มขึ้น เปิดทาง Short สมเหตุสมผล
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SET1 problem: 100% Short rejected โดย Claude
#   เหตุ: 1h EMA bullish → Claude บอก "uptrend structure"
#   แต่ short ถูกต้องเมื่อ 1h RSI overbought + MACD rolling over

# ต้องผ่าน ALL ของ short gate ก่อนส่ง Claude:
SHORT_REQUIRE_4H_BEARISH      = True   # 4h MACD hist < 0 (ตลาดลง)
SHORT_REQUIRE_1H_RSI_MAX      = 72     # 1h RSI ต้อง < 72 (ไม่ใช่ overbought explosion)
SHORT_REQUIRE_RSI_TURN_DOWN   = True   # ต้องมี rsi_turning_down = True
SHORT_REQUIRE_MIN_SCORE       = 7      # short_score ≥ 7 (เหมือน Long)
SHORT_REQUIRE_FUNDING_BIAS    = False  # ไม่บังคับ funding (nice to have)

# block short ถ้า 1h bullish มาก (Claude จะ reject แน่)
SHORT_BLOCK_1H_MACD_STRONG   = 50     # ถ้า 1h MACD hist > 50 → block short

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [TREND FOLLOW] — ปรับ check_multi_tf ให้ TRENDING trigger ได้
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SET1 problem: check_multi_tf ต้อง EMA + RSI + price ทุกอย่าง strict
#   → NEUTRAL เสมอ แม้ trending ชัด

MTF_4H_RSI_BULL    = 50    # ↓ จาก 55 (ผ่อนลง — 4h RSI > 50 = bullish bias)
MTF_4H_RSI_BEAR    = 50    # ↑ จาก 45 (ผ่อนลง — 4h RSI < 50 = bearish bias)
MTF_1H_RSI_CONFIRM = 45    # ↓ จาก 50 (ยืนยัน direction 1h)

# [NEW SET2] Pullback entry สำหรับ TREND strategy
# เมื่อ 4h bullish → รอ 1h pullback ถึง EMA21 ไม่ใช่ breakout
TREND_USE_PULLBACK_ENTRY = True   # True = entry ที่ pullback ไม่ใช่ EMA cross

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [GATE] — Confidence tiers calibrated จาก data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SET1 problem: pre_conf 70+ → ยัง 5/6 reject โดย Claude
#   → scoring overestimate, ต้องการ quality filter ก่อน
FILTER_MIN_CONF  = 25    # เหมือนเดิม
WEAK_MIN_CONF    = 35    # เหมือนเดิม
CLAUDE_MIN_CONF  = 55    # ↑ จาก 50 (กัน weak gray zone)
# ─── Tier 5 Auto-approve (แยก TREND vs REBOUND) ─────────────
# Bug-001 fix: REBOUND ใช้ threshold ต่ำกว่า TREND เพราะ scale /16
AUTO_APPROVE_CONF         = 80   # TREND_FOLLOW: EMA aligned ต้อง pre ≥ 80
REBOUND_AUTO_APPROVE_CONF = 68   # REBOUND TIER5-A: true auto-approve (score≥8 margin≥6)
                                  # data basis: 4/11 eligible approved (36%) — bar สูงพอ
REBOUND_TIER5_MIN_SCORE   = 8    # TIER5-A: score ≥ 8
REBOUND_TIER5_MIN_MARGIN  = 6    # TIER5-A: margin ≥ 6

# TIER5-B: reduced Claude gate (ไม่ auto-approve แต่ลด threshold เข้า Claude)
# data basis: score≥7 margin≥5 rsi_turn_up → 58% Claude approval (21/36 signals)
REBOUND_TIER5B_SCORE      = 7    # TIER5-B score threshold
REBOUND_TIER5B_MARGIN     = 5    # TIER5-B margin threshold
REBOUND_TIER5B_GATE       = 45   # ลด Claude gate 55→45 สำหรับ quality REBOUND signals

# ─── Funding Rate ─────────────────────────────────────────────
FUNDING_BONUS_HIGH = -0.03
FUNDING_BONUS_MED  = -0.01

# ─── 24h Drop Bonus ───────────────────────────────────────────
DROP_BONUS_HIGH = -5.0
DROP_BONUS_MED  = -3.0

# ─── Countertrend Block ───────────────────────────────────────
BLOCK_COUNTERTREND = True

# ─── Persistence Bonus ────────────────────────────────────────
PERSISTENCE_BONUS = 5

# ─── Telegram ────────────────────────────────────────────────
# Accept both SET2 names and the deployed SET1 env names.
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_TOKEN   = TG_BOT_TOKEN
TELEGRAM_CHAT_ID = TG_CHAT_ID
NOTIFY_ON    = "approved_weak"  # approved_weak | all | approved_only

# ─── Micro Bounce ────────────────────────────────────────────
MICRO_BOUNCE_RSI_FAST  = 5
MICRO_BOUNCE_RSI_SLOW  = 15
MICRO_BOUNCE_THRESHOLD = 0.6
MICRO_BOUNCE_MAX_HOLD  = 2

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [MARKET CONTEXT] — Binance Futures microstructure
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Philosophy: Conservative phase — negative penalty always apply,
# positive bonus apply only to SHORT (data-scarce direction).
# Long positive bonus กักไว้ก่อน จนกว่าจะมี 50+ signals validate.
#
# ─── L/S Ratio thresholds ────────────────────────────────────
# BTC ปกติ 0.85–1.15 → extreme = < 0.75 or > 1.35
CTX_LS_SHORTS_EXTREME  = 0.75   # ls_ratio < 0.75 → shorts very crowded → squeeze risk
CTX_LS_SHORTS_HIGH     = 0.90   # ls_ratio < 0.90 → shorts overcrowded → bounce likely
CTX_LS_LONGS_HIGH      = 1.20   # ls_ratio > 1.20 → longs overcrowded → weak long
CTX_LS_LONGS_EXTREME   = 1.40   # ls_ratio > 1.40 → longs very crowded → liquidation risk

# ─── Taker Volume thresholds ─────────────────────────────────
# ใช้ rolling 3-period avg ไม่ใช่ spot เพื่อลด noise
CTX_TAKER_AGGRESSIVE   = 58     # taker_buy/sell_pct >= 58 → aggressive dominance
CTX_TAKER_MODERATE     = 53     # taker_buy/sell_pct >= 53 → moderate pressure

# ─── OI Change threshold ─────────────────────────────────────
# OI drop → positions closing → momentum weakening (ลบเสมอ ไม่มี positive)
CTX_OI_DROP_STRONG     = -2.0   # oi_change_pct < -2.0% → -2 (strong close-out)
CTX_OI_DROP_MILD       = -1.0   # oi_change_pct < -1.0% → -1 (mild close-out)
# OI เพิ่มขึ้น → neutral (ไม่รู้ direction → ไม่ให้ bonus)

# ─── Volume filter (จากภาพ signal) ──────────────────────────
# Signal 1 วิเคราะห์: vol_ratio = 0.01 → liquidity risk สูงมาก
CTX_VOL_MIN_FOR_SIGNAL = 0.10   # vol_ratio < 0.10 → block ก่อน Claude

# ─── ctx_bonus cap ───────────────────────────────────────────
CTX_BONUS_MAX          = 5      # max ±5 ต่อ signal (ไม่ override technical)

# ─── Apply policy (conservative phase) ───────────────────────
# True = apply bonus/penalty ต่อ direction นั้นๆ
CTX_APPLY_NEGATIVE_LONG    = True    # penalty ลด Long ที่ overcrowded → ปลอดภัย
CTX_APPLY_NEGATIVE_SHORT   = True    # penalty ลด Short ที่ shorts crowded
CTX_APPLY_POSITIVE_SHORT   = True    # bonus เพิ่ม Short (data-scarce → ช่วยเปิดทาง)
CTX_APPLY_POSITIVE_LONG    = False   # รอ validate 50+ signals ก่อน

# ─── Tier 5 ctx bonus (ไม่ใช้ auto-approve ลดต่ำ) ────────────
# แทนที่จะลด AUTO_APPROVE_CONF → ลด CLAUDE_MIN_CONF เล็กน้อยแทน
# หมายความว่ายังส่ง Claude แต่ threshold ต่ำลง 3 pts ถ้า ctx aligned
CTX_CLAUDE_GATE_REDUCTION  = 3      # ctx_aligned → CLAUDE_MIN_CONF - 3 (55→52)
CTX_ENABLE_TIER5_BONUS     = False  # ปิดไว้ก่อน: auto-approve ที่ 65 เสี่ยงเกิน

# ─── Logging ─────────────────────────────────────────────────
CTX_LOG_UNAVAILABLE    = True   # log เมื่อ market_ctx fetch ล้มเหลว
CTX_LOG_APPLIED        = True   # log ctx_bonus ที่ apply จริงๆ

# ─── Files ───────────────────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(_DATA_DIR, exist_ok=True)

LOG_FILE    = os.path.join(_DATA_DIR, "signal_log.json")
RECHECK_LOG = os.path.join(_DATA_DIR, "recheck_log.json")
