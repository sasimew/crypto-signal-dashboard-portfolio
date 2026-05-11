# SET3v1 — Indicator Changes & Release Notes
**วันที่:** 2026-05-11
**วิเคราะห์จาก:** signal_log_20260428_20260511.json (2,456 signals, 10 วัน, BTCUSDT)
**Previous version:** SET2v2
**สถานะ:** 🚀 Ready to deploy
**Review ครั้งหน้า:** 2026-05-18

---

## Executive Summary

SET2v2 มีปัญหาหลัก 3 จุดที่ถูกตรวจพบจาก log วิเคราะห์:

| ปัญหา | Impact | Root Cause | SET3 Fix |
|-------|--------|-----------|---------|
| LOW_LIQUIDITY_BLOCK ตัด 32.8% | 806 signals blocked ผิดพลาด | vol_ratio=0.000 = missing data ไม่ใช่ low vol | แยก 0.0 ออก (ข้ามการ check) |
| REBOUND 0 approved ใน 10 วัน | 0/95 REBOUND signals converted | margin requirement=4 แต่ data ได้ max=2-3 | HTF Exhaustion path (margin=2) |
| Claude reject 96.1% | 688/716 wasted API calls | 203 cases: entry_ok=True แต่ 4h RSI overbought | EXHAUSTION_PENALTY (pre_conf-10) |

**คาดการณ์ผลลัพธ์ SET3:**
- LOW_LIQUIDITY_BLOCK: 806 → ~260 signals/10วัน (-68%)
- REBOUND_HTF approved: 0 → ประมาณ 15-25 signals/10วัน
- Wasted Claude calls จาก overbought: -~200 calls/10วัน

---

## การเปลี่ยนแปลง

### [SET3-1] Vol Ratio Missing Data Fix

**ไฟล์:** `bot.py` — `low_liquidity_reason()`

**ปัญหา (SET2):**
```
vol_ratio = 0.000 → LOW_LIQUIDITY_BLOCK → 806 signals (32.8%) ถูกตัด
avg pre_conf_before_block = 61 → สัญญาณดีแต่ถูก block
median vol_ratio ของสัญญาณที่ถูก block = 0.000
```

**Root cause:** Binance API บางครั้ง return volume = 0 เมื่อ candle data ดึงไม่ครบในรอบนั้น ไม่ใช่ volume ต่ำจริง

**วิธีแก้ (SET3):**
```python
# SET2 (ผิด):
if vol_15m < cfg.CTX_VOL_MIN_FOR_SIGNAL:  # 0.0 < 0.1 → block
    return f"LOW_LIQUIDITY_BLOCK: ..."

# SET3 (ถูก):
if vol_15m == 0.0:
    return ""  # missing data — ข้ามการ check
if vol_15m < cfg.CTX_VOL_MIN_FOR_SIGNAL:  # confirmed low vol
    return f"LOW_LIQUIDITY_BLOCK: ..."
```

**เพิ่ม helper function:**
```python
def is_vol_data_missing(tf_15m):
    """Returns True if vol_ratio=0.000 (missing data, not low volume)"""
    vol = (tf_15m or {}).get("vol_ratio")
    return vol is not None and vol == 0.0
```

**เพิ่ม log fields:**
- `vol_data_missing: true/false` — ใน signal record และ build_signal
- `vol_data_missing: true` ส่งไปใน Claude payload (`vol_data_missing` field)
- Claude System Prompt: "If vol_data_missing=true, ignore vol_ratio for liquidity check"

**Risk assessment:** ต่ำ — เพิ่ม safety check เท่านั้น ไม่ได้ลบ filter

---

### [SET3-2] HTF Exhaustion Rebound Scorer (ฟีเจอร์ใหม่)

**ไฟล์:** `bot.py` — `check_htf_exhaustion()` (function ใหม่)

**ปัญหา (SET2):**
```
REBOUND (30m path): margin requirement = 4
data จริง: short_score=6, long_score=4 → margin=2 → WEAK_MARGIN_BLOCK
→ 65/95 REBOUND signals ถูก block (68%)
→ 0 APPROVED ใน 10 วัน

SET2 ไม่มีทางจับสัญญาณกลับตัวระยะสั้นใน TRENDING regime เลย:
- 1h RSI > 70: 400 TREND_FOLLOW signals = SHORT rebound ที่พลาด
- 1h RSI < 30: 93 signals = LONG rebound ที่พลาด
```

**Logic ใหม่ (SET3):**

```python
def check_htf_exhaustion(tf_4h, tf_1h, tf_15m, price):
    """
    HTF Exhaustion = ตลาดอยู่ในโซน overbought/oversold ที่ HTF ยืนยัน
    ไม่ต้องรอ 30m confirm เพราะ 4h RSI เป็น structural signal แล้ว
    
    SHORT exhaustion: 4h RSI > 72 + 1h RSI > 68 + 15m early weakening
    LONG exhaustion:  4h RSI < 28 + 1h RSI < 32 + 15m early recovery
    """
    # Scoring (short exhaustion):
    # 4h RSI > 72      → +3 ★ (primary signal)
    # 4h BB% > 0.90    → +2 (price ชิดขอบบน)
    # 1h RSI > 68      → +2 (momentum elevated)
    # 15m MACD < 0     → +1 (early divergence)
    # 15m RSI < 40     → +1 (15m weakening)
    # 4h MACD < 0      → +1 (4h momentum turning)
    
    # min_score = 5 (ต้องการ 4h RSI extreme + อย่างน้อย 1 confirm อื่น)
    # margin = 2 (HTF ทำหน้าที่ confirm แทน 30m margin)
```

**Strategy label:** `REBOUND_HTF` (แยกจาก `REBOUND` เพื่อ track win rate แยก)

**Trigger timing:**
- TRENDING regime: ตรวจ HTF exhaustion คู่กับ TREND_FOLLOW — ถ้า exhaustion ชนะ switch strategy
- RANGING regime: ตรวจ HTF exhaustion ก่อน 30m REBOUND — ถ้า HTF pass ใช้ HTF path

**ตัวอย่าง signal ที่จะถูก detect:**
```
4h RSI = 75, BB% = 0.94, 1h RSI = 71, 15m MACD < 0
→ short_htf_score = 3+2+2+1 = 8, margin = 8
→ REBOUND_HTF SHORT ✅ (SET2 จะเป็น TREND_FOLLOW Long หรือ NO TRADE)
```

**pre_conf scaling:**
```python
htf_pre = min(round(htf_score / 10 * 100), 75)
# score=5 → 50%, score=7 → 70%, score=8+ → 75% (cap)
```

---

### [SET3-3] REBOUND Margin Reduction (HTF path only)

**ไฟล์:** `bot.py` — `check_htf_exhaustion()`, `should_skip_claude()`

| Path | SET2 margin | SET3 margin | เหตุผล |
|------|------------|------------|-------|
| REBOUND (30m) | ≥ 4 | ≥ 4 (ไม่เปลี่ยน) | ยังต้องการ 30m confirm |
| REBOUND_HTF | ไม่มี | ≥ 2 | 4h RSI ทำหน้าที่ confirm แล้ว |

**การ bypass short gate:**
```python
def should_skip_claude(...):
    htf_mode = rb_data.get("htf_mode", False)
    if direction == "Short":
        if htf_mode:
            return False, ""  # HTF short gate ผ่านแล้วใน check_htf_exhaustion
        # ... ตรวจ should_block_short_set2 ปกติ
```

**invariant:** `htf_mode=True` signals ต้องผ่าน `check_htf_exhaustion()` scoring ก่อนถึงจะ bypass short gate

---

### [SET3-4] Exhaustion Penalty for TREND_FOLLOW Long

**ไฟล์:** `bot.py` — `main()` TRENDING branch

**ปัญหา (SET2):**
```
203 signals: entry_ok=True แต่ Claude reject เพราะ 4h/1h RSI overbought
เหตุผล: "4h RSI 73.34 = overbought extreme; price at BB 0.93 signals exhaustion"
→ wasted Claude API calls ~20/วัน
```

**วิธีแก้:**
```python
exhaustion_long = (
    rsi_4h_val > 72 and   # SET3_EXHAUSTION_RSI_OB
    bb_pct_4h  > 0.90 and # SET3_EXHAUSTION_BB_OB
    direction == "Long"
)
if exhaustion_long:
    pre_conf = max(base_pre_conf - 10, 0)  # SET3_EXHAUSTION_PENALTY
    block_reason_code = "HTF_EXHAUSTION_LONG"
```

**ผลที่คาดหวัง:**
- pre_conf: 75% → 65% สำหรับ overbought Long
- 65% < 75 (AUTO_APPROVE_CONF) → ส่ง Claude แทน auto-approve
- 65% ≥ 55 (CLAUDE_MIN_CONF) → ยังส่ง Claude แต่ไม่ auto-approve
- ถ้า exhaustion_long + HTF exhaustion pass → switch เป็น SHORT REBOUND_HTF แทน

---

### [SET3-5] System Prompt Update

**เพิ่ม REBOUND_HTF rules:**
```
For REBOUND_HTF SHORT: 4h RSI>72 is the primary signal — EMA alignment AGAINST direction is EXPECTED
  Accept if: 4h RSI>72 + short_htf_score>=5 + margin>=2 (counter-trend entry at exhaustion point)
For REBOUND_HTF LONG: 4h RSI<28 is the primary signal — same logic reversed
Exhaustion zone: 4h RSI>72 → LONG entries face mean-reversion risk, raise caution
```

**เพิ่ม vol_data_missing guidance:**
```
VOLUME WARNING: if vol_ratio_15m < 0.10 AND vol_data_missing=false, flag as liquidity_risk.
If vol_data_missing=true, ignore vol_ratio for this check.
```

**เพิ่ม reason_codes:**
- `HTF_EXHAUSTION_SHORT` — Claude approve REBOUND_HTF Short
- `HTF_EXHAUSTION_LONG` — Claude approve REBOUND_HTF Long

---

## Config Changes

ไม่มีการเปลี่ยน `config_set2.py` — SET3 ใช้ config เดิมทั้งหมด
ค่า threshold ใหม่ถูก embed ใน `INDICATOR_SET` dict ใน `bot.py`:

```python
"SET3_HTF_RSI_OB":          72,   # 4h RSI overbought threshold
"SET3_HTF_RSI_OS":          28,   # 4h RSI oversold threshold
"SET3_HTF_RSI_1H_SLOW":     68,   # 1h RSI momentum slowing (short signal)
"SET3_HTF_RSI_1H_SLOW_L":   32,   # 1h RSI momentum slowing (long signal)
"SET3_HTF_BB_OB":           0.90, # 4h BB% overbought
"SET3_HTF_BB_OS":           0.10, # 4h BB% oversold
"SET3_HTF_MARGIN_MIN":      2,    # HTF path margin minimum
"SET3_EXHAUSTION_RSI_OB":   72,   # TREND_FOLLOW exhaustion penalty trigger
"SET3_EXHAUSTION_BB_OB":    0.90, # BB% trigger for exhaustion penalty
"SET3_EXHAUSTION_PENALTY":  10,   # pre_conf reduction for overbought Long
```

---

## Log Fields เพิ่ม

| Field | Type | หมายเหตุ |
|-------|------|---------|
| `vol_data_missing` | bool | True ถ้า vol_ratio=0.000 (missing data) |
| `strategy_used` = `"REBOUND_HTF"` | string | แยก tracking จาก REBOUND |
| `block_reason_code` = `"HTF_EXHAUSTION_LONG"` | string | เมื่อ exhaustion penalty apply |

---

## สิ่งที่ไม่ได้เปลี่ยน

| Component | สถานะ |
|-----------|-------|
| REGIME DETECTION | ไม่เปลี่ยน (SET2 fix ยังดี) |
| TREND_FOLLOW logic | ไม่เปลี่ยน |
| REBOUND 30m scoring | ไม่เปลี่ยน (margin=4 ยังคง) |
| SHORT pre-filter (should_block_short_set2) | ไม่เปลี่ยน |
| Market Context bonus | ไม่เปลี่ยน |
| Gate tiers (1-5) | ไม่เปลี่ยน |
| Telegram notifications | ไม่เปลี่ยน |
| config_set2.py | ไม่เปลี่ยน |

---

## Monitoring Queries — สัปดาห์แรก

หลัง deploy ดู metrics เหล่านี้ทุกวัน:

```python
# 1. vol_data_missing rate (ตรวจสอบ fix)
vol_missing = [d for d in data if d.get('vol_data_missing') == True]
print(f"vol_data_missing: {len(vol_missing)} / {len(data)} ({len(vol_missing)/len(data)*100:.1f}%)")

# 2. REBOUND_HTF performance
htf = [d for d in data if d.get('strategy_used') == 'REBOUND_HTF']
verdicts = Counter(d.get('verdict') for d in htf)
print(f"REBOUND_HTF: {len(htf)} signals | {verdicts}")

# 3. LOW_LIQUIDITY_BLOCK rate (should drop ~68%)
liq = [d for d in data if d.get('gate_path') == 'TIER3_LOW_LIQUIDITY_BLOCK']
print(f"LOW_LIQ_BLOCK: {len(liq)} ({len(liq)/len(data)*100:.1f}%)")

# 4. HTF_EXHAUSTION_LONG penalty count
exhaustion = [d for d in data if d.get('block_reason_code') == 'HTF_EXHAUSTION_LONG']
print(f"EXHAUSTION_PENALTY applied: {len(exhaustion)}")

# 5. Claude reject rate (should improve)
claude = [d for d in data if d.get('gate_path') == 'TIER4_CLAUDE']
rejected = [d for d in claude if d.get('verdict') == 'REJECTED']
print(f"Claude reject: {len(rejected)}/{len(claude)} ({len(rejected)/len(claude)*100:.1f}% if claude else 0})")
```

---

## Invariants — ต้อง verify

| Invariant | ตรวจสอบ |
|-----------|---------|
| `REBOUND_HTF` signals ต้องมี `htf_mode=True` ใน rb_data | `assert d['decision_log']['tf_30m_rebound'].get('htf_mode')` |
| `vol_data_missing=True` ต้องไม่มี `gate_path=TIER3_LOW_LIQUIDITY_BLOCK` | ตรวจสอบใน log |
| `HTF_EXHAUSTION_LONG` ต้องมี 4h RSI > 72 | ตรวจสอบ indicators.4h.rsi |
| `REBOUND_HTF` margin ≥ 2 ไม่ใช่ margin ≥ 4 | ดู htf_data['margin_used'] |

---

## Deploy Checklist

- [ ] syntax check: `python3 -c "import ast; ast.parse(open('bot.py').read()); print('OK')"`
- [ ] scp bot.py ไปที่ VPS
- [ ] docker cp เข้า container + restart
- [ ] ตรวจ log แรก: มี `REBOUND_HTF` หรือ `vol_data_missing` หรือยัง
- [ ] ตรวจ LOW_LIQUIDITY_BLOCK rate หลัง 24h
- [ ] ตรวจ REBOUND_HTF win rate หลัง 1 สัปดาห์

---

## ขั้นตอน Deploy

```bash
# Mac → VPS
scp ~/Desktop/crypto_bot/bot.py root@187.77.144.27:/home/crypto_bot/

# VPS → Container
ssh root@187.77.144.27 "
  docker cp /home/crypto_bot/bot.py crypto-signal-api:/app/bot.py &&
  docker cp /home/crypto_bot/bot.py crypto-signal-bot:/app/bot.py &&
  docker restart crypto-signal-api crypto-signal-bot
"

# ดู log real-time
ssh root@187.77.144.27 "docker logs crypto-signal-bot --tail=30 -f"
```

---

## Pending (ยังไม่ทำใน SET3)

| Feature | เหตุผลที่เลื่อน | Activate Condition |
|---------|--------------|-------------------|
| Enable `CTX_APPLY_POSITIVE_LONG` | รอ validate ctx bonus | 50+ signals with ctx data |
| Multi-model (Groq) | Month 3 roadmap | ตาม schedule |
| REBOUND_HTF Tier 5 auto-approve | รอ win rate data | ≥ 10 REBOUND_HTF signals |
| Sentiment layer | Month 3 roadmap | ตาม schedule |

---

*SET3v1 — 2026-05-11*
*Data basis: 2,456 signals (10 วัน) — BTCUSDT*
*Review: 2026-05-18 — ดู REBOUND_HTF win rate + vol_data_missing rate*
