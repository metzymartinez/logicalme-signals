import os
import json
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Anthropic client is used ONLY for the two once-a-day narrative posts
# (8AM morning brief + EOD recap). The intraday alert path is 100%
# deterministic templating and never touches the API. Client is created
# lazily so the server still boots if the key is absent.
_anthropic_client = None
def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic()
    return _anthropic_client

ANTHROPIC_MODEL = "claude-sonnet-4-6"

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ============================================================
# === SIGNAL CONFIG — matches v18.3 alert() names exactly
# ============================================================
SIGNAL_CONFIG = {
    "STAR_LONG": {
        "direction": "LONG",
        "emoji": "⭐",
        "option": "CALL",
        "label": "STAR LONG",
        "conviction": "Highest conviction — all gates confirmed",
    },
    "STAR_SHORT": {
        "direction": "SHORT",
        "emoji": "⭐",
        "option": "PUT",
        "label": "STAR SHORT",
        "conviction": "Highest conviction — all gates confirmed",
    },
    "EARLY_CALL": {
        "direction": "LONG",
        "emoji": "🟡",
        "option": "CALL",
        "label": "EARLY CALL",
        "conviction": "Early — at support, stoch curling up, CDV flipping green",
    },
    "EARLY_PUT": {
        "direction": "SHORT",
        "emoji": "🟡",
        "option": "PUT",
        "label": "EARLY PUT",
        "conviction": "Early — at resistance, stoch curling down, CDV slowing",
    },
    "PULL_LONG": {
        "direction": "LONG",
        "emoji": "🐂",
        "option": "CALL",
        "label": "PULLBACK",
        "conviction": "Pullback to 20ma in uptrend — pristine reclaim",
    },
    "RALLY_SHORT": {
        "direction": "SHORT",
        "emoji": "🐻",
        "option": "PUT",
        "label": "RALLY FADE",
        "conviction": "Rally to 20ma in downtrend — pristine fade",
    },
    "FLIP_LONG": {
        "direction": "LONG",
        "emoji": "⚡",
        "option": "CALL",
        "label": "15m FLIP LONG",
        "conviction": "15m CDV flipped green + 1H green — institutional buy",
    },
    "FLIP_SHORT": {
        "direction": "SHORT",
        "emoji": "⚡",
        "option": "PUT",
        "label": "15m FLIP SHORT",
        "conviction": "15m CDV flipped red + 1H red — institutional sell",
    },
    "999_LONG": {
        "direction": "LONG",
        "emoji": "🟡",
        "option": "CALL",
        "label": "999 EMA LONG",
        "conviction": "Price near 999 EMA above — battlefield bounce",
    },
    "999_SHORT": {
        "direction": "SHORT",
        "emoji": "🟡",
        "option": "PUT",
        "label": "999 EMA SHORT",
        "conviction": "Price near 999 EMA below — battlefield rejection",
    },
    "LONG": {
        "direction": "LONG",
        "emoji": "🟢",
        "option": "CALL",
        "label": "LONG",
        "conviction": "CDV + trigger confirmed",
    },
    "SHORT": {
        "direction": "SHORT",
        "emoji": "🔴",
        "option": "PUT",
        "label": "SHORT",
        "conviction": "CDV + trigger confirmed",
    },
    "MACD_CROSS_UP": {
        "direction": "LONG",
        "emoji": "📈",
        "option": "CALL",
        "label": "MACD CROSS UP",
        "conviction": "MACD crossed zero line up with CDV fuel",
    },
    "MACD_CROSS_DN": {
        "direction": "SHORT",
        "emoji": "📉",
        "option": "PUT",
        "label": "MACD CROSS DOWN",
        "conviction": "MACD crossed zero line down with CDV fuel",
    },
    "CANDLE_BULL": {
        "direction": "LONG",
        "emoji": "🕯",
        "option": "CALL",
        "label": "CANDLE BULL",
        "conviction": "Hammer / bull engulf / doji at support",
    },
    "CANDLE_BEAR": {
        "direction": "SHORT",
        "emoji": "🕯",
        "option": "PUT",
        "label": "CANDLE BEAR",
        "conviction": "Shooting star / bear engulf / doji at resistance",
    },
}


# ============================================================
# === PARSER — reads full v18.3 JSON payload (adds VP + NODE)
# ============================================================
def parse_tradingview_message(raw_body: str) -> dict:
    raw_body = raw_body.strip()
    try:
        data = json.loads(raw_body)
        if isinstance(data, dict):
            return {
                "signal_type":   str(data.get("signal", data.get("signal_type", "UNKNOWN"))).strip().upper().replace(" ", "_"),
                "ticker":        data.get("ticker", "SPY"),
                "price":         str(data.get("price", "")),
                "time":          data.get("time", ""),
                "open":          str(data.get("open", "")),
                "high":          str(data.get("high", "")),
                "low":           str(data.get("low", "")),
                "volume":        str(data.get("volume", "")),
                # MAs
                "ma8":           str(data.get("ma8", "")),
                "ma20":          str(data.get("ma20", "")),
                "ma33":          str(data.get("ma33", "")),
                "ma200":         str(data.get("ma200", "")),
                "ma999":         str(data.get("ma999", "")),
                # Session levels
                "ldnH":          str(data.get("ldnH", "")),
                "ldnL":          str(data.get("ldnL", "")),
                "nyH":           str(data.get("nyH", "")),
                "nyL":           str(data.get("nyL", "")),
                # Context
                "at_support":    data.get("at_support", False),
                "at_resistance": data.get("at_resistance", False),
                "score":         str(data.get("score", "")),
                "regime":        str(data.get("regime", "")),
                "rvol":          data.get("rvol", False),
                # CDV all timeframes
                "cdv_4h":        str(data.get("cdv_4h", "")),
                "cdv_1h":        str(data.get("cdv_1h", "")),
                "cdv_15m":       str(data.get("cdv_15m", "")),
                "cdv_2m":        str(data.get("cdv_2m", "")),
                # Indicators
                "stoch_k":       str(data.get("stoch_k", "")),
                "macd":          str(data.get("macd", "")),
                "choch":         str(data.get("choch", "")),
                # Structural bias / veto (v18)
                "bias":          str(data.get("bias", "")),
                "veto":          str(data.get("veto", "NONE")),
                # Volume Profile (v18)
                "yPOC":          str(data.get("yPOC", "")),
                "yVAH":          str(data.get("yVAH", "")),
                "yVAL":          str(data.get("yVAL", "")),
                "dPOC":          str(data.get("dPOC", "")),
                "poc_bias":      str(data.get("poc_bias", "")),
                "auction":       str(data.get("auction", "")),
                # NODE density (v18.3)
                "node_density":  str(data.get("node_density", "")),
                "node_state":    str(data.get("node_state", "")),
                "node_veto":     data.get("node_veto", False),
                # VIX (v18.2)
                "vix":           str(data.get("vix", "")),
                "vix_regime":    str(data.get("vix_regime", "")),
                "vix_div":       str(data.get("vix_div", "none")),
                # 7Hr morning brief fields (London 1AM-8AM candle)
                "7hr_open":      str(data.get("7hr_open", "")),
                "7hr_high":      str(data.get("7hr_high", "")),
                "7hr_low":       str(data.get("7hr_low", "")),
                "7hr_close":     str(data.get("7hr_close", "")),
                "7hr_lean":      str(data.get("7hr_lean", "")),
                "fakeout":       str(data.get("fakeout", "none")),
                "verdict":       str(data.get("verdict", "")),
                "raw":           raw_body,
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Plain text fallback
    parts  = raw_body.split()
    result = {"raw": raw_body}
    if len(parts) >= 1: result["signal_type"] = parts[0].strip().upper().replace(" ", "_")
    if len(parts) >= 2: result["ticker"]      = parts[1]
    if len(parts) >= 3: result["price"]       = parts[2]
    return result


# ============================================================
# === HELPERS
# ============================================================
def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def format_volume(vol_str: str) -> str:
    try:
        v = int(float(vol_str))
        if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if v >= 1_000:     return f"{v/1_000:.0f}K"
        return str(v)
    except (ValueError, TypeError):
        return vol_str or "n/a"


def cdv_emoji(val: str) -> str:
    return "🟢" if str(val).upper() == "GREEN" else "🔴"


# ============================================================
# === TARGET ENGINE
# ============================================================
def compute_targets(parsed: dict, direction: str) -> dict:
    price  = safe_float(parsed.get("price"))
    ma200  = safe_float(parsed.get("ma200"))
    ma999  = safe_float(parsed.get("ma999"))
    ldn_h  = safe_float(parsed.get("ldnH"))
    ldn_l  = safe_float(parsed.get("ldnL"))
    ny_h   = safe_float(parsed.get("nyH"))
    ny_l   = safe_float(parsed.get("nyL"))

    if direction == "LONG":
        t1 = ldn_h if ldn_h > price else (ny_h if ny_h > price else 0)
        t2 = ma200 if ma200 > price else 0
        t3 = ma999 if ma999 > price else 0
        move = (t1 - price) if t1 else 0
    else:
        t1 = ldn_l if ldn_l < price else (ny_l if ny_l < price else 0)
        t2 = ma200 if ma200 < price else 0
        t3 = ma999 if ma999 < price else 0
        move = (price - t1) if t1 else 0

    return {
        "t1":    f"${t1:.2f}" if t1 else "n/a",
        "t2":    f"${t2:.2f}" if t2 else "n/a",
        "t3_999": f"${t3:.2f}" if t3 else "n/a",
        "move":  f"+${move:.2f}" if direction == "LONG" and move else f"-${abs(move):.2f}" if move else "n/a",
    }


def suggest_strike(parsed: dict, direction: str) -> str:
    price = safe_float(parsed.get("price"))
    if not price:
        return "ATM"
    atm = round(price)
    if direction == "LONG":
        strike = atm if (price - atm) < 0.50 else atm + 1
        return f"${strike}C"
    else:
        strike = atm if (atm - price) < 0.50 else atm - 1
        return f"${strike}P"


def check_200_pattern(parsed: dict, direction: str) -> str | None:
    price  = safe_float(parsed.get("price"))
    ma200  = safe_float(parsed.get("ma200"))
    cdv_15m = parsed.get("cdv_15m", "").upper()
    cdv_1h  = parsed.get("cdv_1h",  "").upper()

    if not price or not ma200:
        return None

    near_200 = abs(price - ma200) / ma200 < 0.003

    if near_200:
        if direction == "SHORT" and cdv_15m == "RED" and cdv_1h == "RED":
            return "⚠️ 200 SMA bounce = FAKE — CDV still red — hunting 999 EMA"
        if direction == "LONG" and cdv_15m == "GREEN" and cdv_1h == "GREEN":
            return "✅ 200 SMA held — CDV green — extended target 999 EMA"
    return None


# ============================================================
# === TAPE READ — deterministic one-liner (NO API, NO credits)
# Builds the read from the same fields Pine already computed.
# Replaces the old get_claude_read() that called the Anthropic API
# on every alert. This can never hallucinate a level — it only
# echoes what the Pine script sent.
# ============================================================
def get_tape_read(parsed: dict, config: dict) -> str:
    direction = config["direction"]
    price   = safe_float(parsed.get("price"))
    ma999   = safe_float(parsed.get("ma999"))
    ma200   = safe_float(parsed.get("ma200"))
    ldn_h   = safe_float(parsed.get("ldnH"))
    ldn_l   = safe_float(parsed.get("ldnL"))
    regime  = str(parsed.get("regime", "")).upper()
    cdv_4h  = str(parsed.get("cdv_4h",  "")).upper()
    cdv_1h  = str(parsed.get("cdv_1h",  "")).upper()
    cdv_15m = str(parsed.get("cdv_15m", "")).upper()
    cdv_2m  = str(parsed.get("cdv_2m",  "")).upper()
    node_state = str(parsed.get("node_state", "")).upper()
    node_den   = parsed.get("node_density", "")

    want = "GREEN" if direction == "LONG" else "RED"
    aligned = [cdv_4h, cdv_1h, cdv_15m, cdv_2m].count(want)

    bits = []

    # 1. CDV alignment across the four timeframes
    if aligned == 4:
        bits.append(f"all 4 CDV {want.lower()} — full stack aligned")
    elif aligned == 3:
        bits.append(f"3/4 CDV {want.lower()} — strong")
    elif aligned == 2:
        bits.append("CDV split 2/4 — mixed fuel")
    else:
        bits.append(f"only {aligned}/4 CDV {want.lower()} — thin")

    # 2. NODE location (Grady HVN/LVN read) — the new v18.3 context
    if node_state == "HVN":
        bits.append(f"on HVN shelf ({node_den}) — reaction zone")
    elif node_state == "LVN":
        bits.append(f"in LVN vacuum ({node_den}) — price travels, no shelf")
    elif node_state == "MID":
        bits.append(f"MID node ({node_den}) — no clear shelf")

    # 3. 999 battlefield position
    if ma999:
        if price > ma999:
            bits.append(f"above 999 battlefield ${ma999:.2f}")
        else:
            bits.append(f"below 999 battlefield ${ma999:.2f}")

    # 4. 200 SMA proximity
    if ma200 and abs(price - ma200) / ma200 < 0.003:
        bits.append(f"testing 200 SMA ${ma200:.2f}")

    # 5. London range structure
    if direction == "LONG" and ldn_h and price < ldn_h:
        bits.append(f"London high ${ldn_h:.2f} overhead")
    elif direction == "SHORT" and ldn_l and price > ldn_l:
        bits.append(f"London low ${ldn_l:.2f} below")

    # 6. Regime-conflict warning (countertrend)
    if regime == "BULL" and direction == "SHORT":
        bits.append("⚠️ shorting into BULL regime — quick target only")
    elif regime == "BEAR" and direction == "LONG":
        bits.append("⚠️ buying into BEAR regime — quick target only")

    return " | ".join(bits)


# ============================================================
# === TELEGRAM MESSAGE BUILDER
# ============================================================
def build_telegram_message(parsed: dict, config: dict) -> str:
    ticker      = parsed.get("ticker", "SPY")
    price_str   = parsed.get("price", "?")
    time_et     = parsed.get("time", "?")
    direction   = config["direction"]
    emoji       = config["emoji"]
    option_type = config["option"]
    score       = parsed.get("score", "?")
    regime      = parsed.get("regime", "")
    rvol        = parsed.get("rvol", False)

    # CDV alignment
    cdv_4h  = parsed.get("cdv_4h",  "")
    cdv_1h  = parsed.get("cdv_1h",  "")
    cdv_15m = parsed.get("cdv_15m", "")
    cdv_2m  = parsed.get("cdv_2m",  "")
    cdv_line = f"{cdv_emoji(cdv_4h)}4H {cdv_emoji(cdv_1h)}1H {cdv_emoji(cdv_15m)}15m {cdv_emoji(cdv_2m)}2m"

    # Targets
    targets = compute_targets(parsed, direction)
    strike  = suggest_strike(parsed, direction)

    # MA levels
    ma999 = parsed.get("ma999", "")

    # 200 SMA pattern check
    pattern_warn = check_200_pattern(parsed, direction)

    # NODE line (v18.3)
    node_state = str(parsed.get("node_state", "")).upper()
    node_den   = parsed.get("node_density", "")
    node_line  = ""
    if node_state == "HVN":
        node_line = f"🟨 NODE: HVN {node_den} — on shelf, reaction zone"
    elif node_state == "LVN":
        node_line = f"🟦 NODE: LVN {node_den} — vacuum, price travels"
    elif node_state == "MID":
        node_line = f"⬜ NODE: MID {node_den}"

    # Deterministic tape read (no API)
    read_line = get_tape_read(parsed, config)

    # Regime tag
    regime_tag = "🟡 BULL DAY" if regime == "BULL" else "🔴 BEAR DAY" if regime == "BEAR" else ""
    rvol_tag   = " | RVOL ✓" if rvol else ""

    msg = (
        f"{emoji} <b>{config['label']}</b> — {ticker} @ <b>${price_str}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕐 {time_et} | Score: {score}/6 | {regime_tag}{rvol_tag}\n"
        f"\n📊 <b>CDV</b>\n"
        f"{cdv_line}\n"
    )

    if node_line:
        msg += f"{node_line}\n"

    msg += (
        f"\n📐 <b>LEVELS</b>\n"
        f"T1: {targets['t1']} | Move: {targets['move']}\n"
    )

    if targets['t2'] != "n/a":
        msg += f"T2: {targets['t2']} (200 SMA)\n"

    if targets['t3_999'] != "n/a":
        msg += f"T3: {targets['t3_999']} (999 EMA 🎯)\n"

    if ma999:
        msg += f"999 EMA: ${ma999}\n"

    if pattern_warn:
        msg += f"\n{pattern_warn}\n"

    msg += (
        f"\n🎰 <b>OPTION</b>\n"
        f"{strike} 0DTE {option_type}\n"
        f"Stop: 50% | Max: $50–60\n"
        f"\n⚡ <i>{read_line}</i>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<i>Not financial advice</i>"
    )

    return msg


# ============================================================
# === ROUTES + TELEGRAM
# ============================================================
def send_telegram(message: str):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    resp    = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


# --- Error guard: one Telegram alert per distinct error / 15 min ---
_ERROR_SEEN = {}
_ERROR_COOLDOWN = 15 * 60  # seconds

def notify_error_once(err_text: str):
    """Send a Telegram error alert at most once per distinct message
    per cooldown window, so a repeating failure doesn't spam the channel."""
    now = time.time()
    key = str(err_text)[:120]
    last = _ERROR_SEEN.get(key, 0)
    if now - last < _ERROR_COOLDOWN:
        return
    _ERROR_SEEN[key] = now
    try:
        send_telegram(f"⚠️ Logical Me: {err_text}")
    except Exception:
        pass


# ============================================================
# === 8AM MORNING BRIEF — macro + calendar + levels (v19)
# ============================================================
# ONE Anthropic call per morning. The API's server-side web_fetch /
# web_search tools pull the economic calendar (ForexFactory), the
# earnings calendar (Earnings Whispers) and overnight headlines INSIDE
# that single request, then return strict JSON. Chart levels always
# come from the Pine payload, never from the web. If the call fails
# for any reason the brief still posts from a deterministic template.

from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None

BRIEF_CALENDAR_URL = "https://www.forexfactory.com/calendar?day=today"
BRIEF_EARNINGS_URL = "https://beta.earningswhispers.com/"

# Index-moving names: only these (and top-30 S&P weights) get an earnings line.
BRIEF_MEGACAPS = ("AAPL MSFT NVDA AMZN GOOGL GOOG META AVGO TSLA BRK.B JPM LLY V UNH XOM "
                  "MA COST HD PG JNJ NFLX WMT ABBV BAC CRM ORCL CVX KO AMD MRK PEP ADBE CSCO")

BRIEF_TOOLS = [
    {
        "type": "web_fetch_20260318",
        "name": "web_fetch",
        "max_uses": 4,
        "use_cache": False,
        "max_content_tokens": 20000,
        "allowed_domains": ["forexfactory.com", "www.forexfactory.com",
                            "earningswhispers.com", "beta.earningswhispers.com",
                            "www.earningswhispers.com"],
    },
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 2,
    },
]

BRIEF_SYSTEM = """You are Logical Me, an intraday SPY 0DTE options system (Oliver Velez Pristine + CDV + 999 EMA battlefield + ICT AMD bias + Volume Profile).
You write the 8AM ET pre-market brief. You are terse, specific, directional. No hype, no emojis, no disclaimers, no markdown.

RULES
- All times in ET, 12-hour, no leading zero (8:30, 10:00, 2:00). Convert from the source's timezone if needed; if the source timezone is unclear, use standard US release times (NFP/CPI/PPI/Retail Sales/PCE 8:30, ISM/JOLTS/UMich 10:00, Treasury auctions 1:00, FOMC decision 2:00, minutes 2:00).
- Economic calendar: US events only, high or medium impact. Include consensus and prior when shown. Tier: RED = NFP, CPI, PPI, PCE, FOMC decision/minutes, GDP advance, Powell speech. YELLOW = ISM, retail sales, JOLTS, claims on a quiet day, UMich, Treasury auctions 10Y/30Y, other Fed speakers. Drop everything else.
- Earnings: only names in the MEGACAP list or top-30 S&P weights, before-open today or after-close yesterday/today. If none, say so in one line. Note when last night's reports are already priced into ES.
- Overnight: what moved while the trader slept — ES vs prior close and gap direction, Asia/Europe tone, DXY, 10Y, VIX, crude, one geopolitical/policy headline if it matters to index risk. Max 5 lines, each under 12 words.
- Plan: if/then lines using the chart levels supplied, in this exact style: "Above 773.25 + 1H CDV flip green -> longs toward 778.84". Give one long trigger, one short trigger, one no-trade condition. Add a no-trade window for every RED event (event time minus 5 to plus 15 minutes) and for the first 5 minutes after the open when a RED print lands pre-market.
- Read: ONE paragraph, max 45 words, framing the day: macro driver + structure + what confirms direction.
- Never invent numbers. If a fetch fails, set the section's "note" to what failed and keep the rest.

OUTPUT: respond with ONLY a JSON object, no prose before or after, no code fences:
{
  "overnight": ["line", ...],
  "calendar": [{"time":"8:30","event":"NFP","exp":"+150K","prior":"+73K","tier":"RED"}, ...],
  "calendar_note": "",
  "earnings": ["line", ...],
  "plan": ["line", ...],
  "no_trade": ["8:25-8:45 NFP", ...],
  "read": "paragraph"
}"""


def _now_et():
    try:
        return datetime.now(_ET) if _ET else datetime.now()
    except Exception:
        return datetime.now()


def _brief_levels_json(p) -> dict:
    keys = ("price", "ma999", "ma200", "ma33", "ma20", "ma8", "ldnH", "ldnL",
            "yPOC", "yVAH", "yVAL", "dPOC", "regime", "bias", "vix", "vix_regime",
            "cdv_4h", "cdv_1h", "cdv_15m", "7hr_open", "7hr_high", "7hr_low",
            "7hr_close", "7hr_lean", "fakeout", "verdict", "node_state")
    return {k: p.get(k, "") for k in keys if str(p.get(k, "")).strip() not in ("", "none")}


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in narrative response")
    return json.loads(text[start:end + 1])


def _html_safe(obj):
    """Escape model text so a stray '<' or '&' can't break Telegram HTML parse."""
    import html as _html
    if isinstance(obj, str):
        return _html.escape(obj, quote=False)
    if isinstance(obj, list):
        return [_html_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _html_safe(v) for k, v in obj.items()}
    return obj


def get_brief_intel(p) -> dict | None:
    """ONE API call. Returns the parsed JSON dict, or None on any failure."""
    now = _now_et()
    user = f"""Today is {now.strftime('%A, %B %d, %Y')}, it is {now.strftime('%-I:%M %p')} ET.

STEP 1 - fetch the US economic calendar: {BRIEF_CALENDAR_URL}
STEP 2 - fetch the earnings calendar: {BRIEF_EARNINGS_URL}
STEP 3 - web_search once for "stock market futures today" for the overnight tape (ES, DXY, 10Y, VIX, crude, headline).
STEP 4 - build the brief JSON.

MEGACAP list: {BRIEF_MEGACAPS}

Chart data from the Pine payload (authoritative for every level; do not replace with web numbers):
{json.dumps(_brief_levels_json(p), indent=1)}"""

    try:
        client = _get_anthropic()
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1800,
            system=BRIEF_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=BRIEF_TOOLS,
        )
        texts = [b.text for b in resp.content if getattr(b, "type", "") == "text" and getattr(b, "text", "").strip()]
        if not texts:
            raise ValueError("no text block in response")
        # The JSON is the final text block; earlier text blocks are tool-call chatter.
        for t in reversed(texts):
            try:
                return _html_safe(_extract_json(t))
            except Exception:
                continue
        raise ValueError("no parseable JSON block")
    except Exception as e:
        notify_error_once(f"AM brief intel failed, posted template only: {str(e)[:200]}")
        return None


def _tier_dot(tier: str) -> str:
    t = str(tier).upper()
    return "🔴" if t == "RED" else "🟡" if t == "YELLOW" else "⚪"


def _sane_range(price, lo, hi) -> bool:
    """Reject extended-hours print pollution (e.g. a $49 overnight SPY range)."""
    pr, l, h = safe_float(price), safe_float(lo), safe_float(hi)
    if not pr or not l or not h or h <= l:
        return False
    return (h - l) / pr <= 0.025


def build_morning_brief(p):
    """
    Builds the 8AM post. Returns (telegram_html, instagram_caption) as two
    separate strings so they post as two Telegram messages.
    """
    now      = _now_et()
    stamp    = now.strftime("%-I:%M %p ET")
    price    = p.get("price", "?")
    c7_high  = p.get("7hr_high", "?")
    c7_low   = p.get("7hr_low", "?")
    c7_lean  = str(p.get("7hr_lean", "")).upper() or "NEUTRAL"
    regime   = str(p.get("regime", "?")).upper()
    bias     = str(p.get("bias", "")).upper()
    ma999    = p.get("ma999", "?")
    ma200    = p.get("ma200", "?")
    ldnH     = p.get("ldnH", "?")
    ldnL     = p.get("ldnL", "?")
    yPOC     = p.get("yPOC", "")
    yVAH     = p.get("yVAH", "")
    yVAL     = p.get("yVAL", "")
    vix      = p.get("vix", "")
    vix_reg  = p.get("vix_regime", "")
    cdv_4h   = str(p.get("cdv_4h", "?")).upper()
    cdv_1h   = str(p.get("cdv_1h", "?")).upper()
    cdv_15m  = str(p.get("cdv_15m", "?")).upper()
    fakeout  = p.get("fakeout", "none")
    verdict  = p.get("verdict", "?")

    greens = [cdv_4h, cdv_1h, cdv_15m].count("GREEN")
    reds   = [cdv_4h, cdv_1h, cdv_15m].count("RED")
    align  = ("all GREEN — long bias" if greens == 3 else
              "all RED — short bias"  if reds == 3 else
              "MIXED — wait for alignment")

    def light(v):
        return "🟢" if v == "GREEN" else "🔴"

    lean_emoji = "🐂" if c7_lean == "LONG" else "🐻" if c7_lean == "SHORT" else "⏸"
    if _sane_range(price, c7_low, c7_high):
        range_line = f"Range: ${c7_low} – ${c7_high}"
    else:
        range_line = "Range: ⚠️ data suspect (extended-hours print) — using London H/L"

    intel = get_brief_intel(p) or {}

    # ---------- TELEGRAM ----------
    tg  = f"🗓️ <b>SPY MORNING BRIEF</b> · {stamp}\n"
    tg += "━━━━━━━━━━━━━━━\n"
    tg += f"📍 Price: <b>${price}</b>"
    if vix:
        tg += f"   VIX {vix}{(' · ' + vix_reg) if vix_reg else ''}"
    tg += "\n\n"

    on = intel.get("overnight") or []
    if on:
        tg += "🌍 <b>OVERNIGHT</b>\n" + "\n".join(f"• {x}" for x in on[:5]) + "\n\n"

    cal = intel.get("calendar") or []
    tg += "📅 <b>CALENDAR (ET)</b>\n"
    if cal:
        for ev in cal[:8]:
            line = f"{_tier_dot(ev.get('tier',''))} <b>{ev.get('time','?')}</b> {ev.get('event','?')}"
            ep = " / ".join(x for x in (
                f"exp {ev['exp']}"   if ev.get("exp")   else "",
                f"prior {ev['prior']}" if ev.get("prior") else "") if x)
            if ep:
                line += f"  <i>({ep})</i>"
            tg += line + "\n"
    else:
        tg += f"{intel.get('calendar_note') or 'calendar unavailable — check ForexFactory'}\n"
    nt = intel.get("no_trade") or []
    if nt:
        tg += "⛔ No-trade: " + " · ".join(nt[:4]) + "\n"
    tg += "\n"

    earn = intel.get("earnings") or []
    if earn:
        tg += "💼 <b>EARNINGS</b>\n" + "\n".join(f"• {x}" for x in earn[:4]) + "\n\n"

    tg += (
        f"{lean_emoji} <b>7Hr WICK (London 1AM–8AM)</b>\n"
        f"Lean: <b>{c7_lean}</b>\n"
        f"{range_line}\n\n"
        f"📊 <b>CDV STACK</b>\n"
        f"4H {light(cdv_4h)}  1H {light(cdv_1h)}  15m {light(cdv_15m)}\n"
        f"➤ {align}\n\n"
        f"🗺️ <b>KEY LEVELS</b>\n"
        f"Battlefield (999): ${ma999} · regime {regime}\n"
        f"200 SMA: ${ma200}\n"
        f"London H/L: ${ldnH} / ${ldnL}\n"
    )
    if yPOC:
        tg += f"yPOC ${yPOC}"
        if yVAH and yVAL:
            tg += f" · yVA ${yVAL}–${yVAH}"
        tg += "\n"
    if bias:
        tg += f"AMD bias: {bias}\n"
    if fakeout and fakeout != "none":
        tg += f"⚠️ <b>FAKE-OUT:</b> {fakeout}\n"
    tg += "\n"

    plan = intel.get("plan") or []
    tg += "🎯 <b>PLAN</b>\n"
    if plan:
        tg += "\n".join(f"• {x}" for x in plan[:4]) + "\n"
    else:
        tg += f"{verdict}\n"
    tg += "\n"

    read = intel.get("read") or ""
    if not read:
        stack = "aligned long" if greens == 3 else "aligned short" if reds == 3 else "mixed — wait for alignment"
        read = f"{c7_lean} lean into a {regime} regime, CDV {stack}. {verdict}"
    tg += f"🧠 <b>READ</b>\n{read}\n"
    tg += "━━━━━━━━━━━━━━━\n"
    tg += "<i>Pre-market lean has a shelf life — the live CDV stack and the 8:30 print override it.</i>"

    # ---------- INSTAGRAM (second message) ----------
    top_ev = next((e for e in cal if str(e.get("tier","")).upper() == "RED"), cal[0] if cal else None)
    ig = (
        f"📋 <b>INSTAGRAM CAPTION</b>\n"
        f"———————————————\n\n"
        f"🗓️ SPY PRE-MARKET · {now.strftime('%b %-d')}\n"
        f"📍 ${price}\n\n"
    )
    if top_ev:
        ig += f"Watch: {top_ev.get('time','')} {top_ev.get('event','')}\n"
    ig += (
        f"7Hr Lean: {c7_lean}\n"
        f"CDV: {align}\n\n"
        f"Battlefield ${ma999} | 200 ${ma200}\n"
        f"London ${ldnL}–${ldnH}\n\n"
    )
    if plan:
        ig += f"Plan: {plan[0]}\n\n"
    else:
        ig += f"Plan: {verdict}\n\n"
    ig += "#SPY #SP500 #OptionsTrading #LogicalMe #DayTrading #0DTE"

    return tg, ig


def post_morning_brief(p):
    """Send the brief as two Telegram messages (brief, then caption)."""
    tg, ig = build_morning_brief(p)
    send_telegram(tg[:4000])
    try:
        send_telegram(ig[:4000])
    except Exception as e:
        notify_error_once(f"IG caption post failed: {str(e)[:120]}")


def get_recap_narrative(data) -> str:
    """ONE narrative paragraph for the EOD recap. Uses the API.
    Falls back to the existing auto_generate_note() if the call fails."""
    prompt = f"""You are Logical Me, an intraday SPY options system. Write ONE tight paragraph (max 45 words)
recapping how SPY actually traded today versus the morning lean.

Open: {data.get('spy_open')} High: {data.get('spy_high')} Low: {data.get('spy_low')} Close: {data.get('spy_close')}
Regime: {data.get('regime')}
Morning 7Hr lean was: {str((LAST_MORNING_BRIEF or {}).get('7hr_lean','')).upper()}

Say whether the morning lean paid off or trapped, and the character of the day. No hype, no emojis, no disclaimers."""

    try:
        client = _get_anthropic()
        resp = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        notify_error_once(f"Recap narrative fell back to template: {str(e)}")
        return auto_generate_note(
            safe_float(data.get("spy_open")) or None,
            safe_float(data.get("spy_close")) or None,
            safe_float(data.get("spy_high")) or None,
            safe_float(data.get("spy_low")) or None,
        )


@app.route("/alert", methods=["POST"])
def receive_alert():
    try:
        raw_body    = request.data.decode("utf-8")
        parsed      = parse_tradingview_message(raw_body)
        signal_type = parsed.get("signal_type", "UNKNOWN")

        # === MORNING BRIEF — 8AM London-candle-lock post ===
        if signal_type == "MORNING_BRIEF":
            global LAST_MORNING_BRIEF
            LAST_MORNING_BRIEF = dict(parsed)
            post_morning_brief(parsed)
            return jsonify({"status": "ok", "signal": "MORNING_BRIEF"}), 200

        config = SIGNAL_CONFIG.get(signal_type)

        if not config:
            msg = (
                f"⚠️ <b>Unknown signal: {signal_type}</b>\n"
                f"Price: ${parsed.get('price','?')} | Time: {parsed.get('time','?')}\n"
                f"Check Pine Script signal names match server SIGNAL_CONFIG."
            )
            send_telegram(msg)
            return jsonify({"status": "unknown signal", "signal": signal_type}), 200

        message = build_telegram_message(parsed, config)
        send_telegram(message)
        return jsonify({"status": "ok", "signal": signal_type}), 200

    except Exception as e:
        # Return 200 so TradingView does NOT flag the webhook as failed and retry.
        notify_error_once(str(e))
        return jsonify({"status": "error", "message": str(e)}), 200


@app.route("/test", methods=["GET"])
def test():
    fake_payload = json.dumps({
        "signal":        "STAR_LONG",
        "ticker":        "SPY",
        "price":         736.67,
        "time":          "10:15 ET",
        "open":          736.10, "high": 736.75, "low": 735.90, "volume": 1482300,
        "ma20":          736.20, "ma33": 735.80, "ma200": 734.50, "ma999": 728.30,
        "ldnH":          740.92, "ldnL": 735.73, "nyH": 736.75, "nyL": 735.90,
        "at_support":    True, "at_resistance": False,
        "score":         6, "regime": "BULL", "rvol": True,
        "cdv_4h":        "GREEN", "cdv_1h": "GREEN", "cdv_15m": "GREEN", "cdv_2m": "GREEN",
        "stoch_k":       28.5, "macd": "BULL", "choch": 1,
        "dPOC":          734.90, "poc_bias": "LONG", "auction": "OPEN-DRIVE UP",
        "node_density":  82.0, "node_state": "HVN", "node_veto": False,
        "vix":           14.2, "vix_regime": "LOW", "vix_div": "none",
    })

    parsed  = parse_tradingview_message(fake_payload)
    config  = SIGNAL_CONFIG.get(parsed["signal_type"], SIGNAL_CONFIG["STAR_LONG"])
    message = build_telegram_message(parsed, config)

    telegram_sent = False
    if request.args.get("send") == "true":
        provided_key = request.args.get("key", "")
        test_key     = os.environ.get("TEST_KEY", "")
        if provided_key and test_key and provided_key == test_key:
            send_telegram(message)
            telegram_sent = True
        else:
            return jsonify({"status": "unauthorized"}), 401

    return jsonify({
        "status":  "ok — Telegram sent" if telegram_sent else "ok — JSON only",
        "parsed":  parsed,
        "message": message,
    }), 200


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status":            "Logical Me v18.3 Signal Server",
        "signals_supported": list(SIGNAL_CONFIG.keys()),
        "api":               "hybrid — intraday alerts template-only (no credits); AM brief (1 call w/ web_fetch calendar + earnings + web_search overnight) + EOD recap (1 call)",
        "version":           "v18.3",
        "extra_endpoints":   ["/recap (POST trades)", "/run-brief (manual AM brief)", "MORNING_BRIEF (auto)"],
    }), 200


# ============================================================
# === END-OF-DAY RECAP — market wrap + trades + IG caption
# ============================================================
def build_eod_recap(p):
    spy_open  = p.get("spy_open")
    spy_high  = p.get("spy_high")
    spy_low   = p.get("spy_low")
    spy_close = p.get("spy_close")
    regime    = str(p.get("regime", "")).upper()
    note      = p.get("note", "")
    trades    = p.get("trades", [])

    arrow = "🟢" if (spy_close and spy_open and spy_close >= spy_open) else "🔴"
    try:
        chg = round(spy_close - spy_open, 2)
        chg_pct = round((spy_close - spy_open) / spy_open * 100, 2)
        chg_str = f"{'+' if chg >= 0 else ''}{chg} ({'+' if chg_pct >= 0 else ''}{chg_pct}%)"
    except Exception:
        chg_str = ""

    trade_lines_tg = []
    trade_lines_ig = []
    total_pl = 0.0
    wins = 0
    for t in trades:
        sym  = t.get("sym", "?")
        buy  = t.get("buy")
        sell = t.get("sell")
        try:
            pl  = round(sell - buy, 2)
            ret = round((sell - buy) / buy * 100, 0)
            total_pl += pl
            if pl > 0:
                wins += 1
            res = "🟢" if pl >= 0 else "🔴"
            trade_lines_tg.append(f"{res} {sym}: ${buy} → ${sell}  ({'+' if ret>=0 else ''}{ret:.0f}%, {'+' if pl>=0 else ''}${pl})")
            trade_lines_ig.append(f"{sym} {'+' if ret>=0 else ''}{ret:.0f}%")
        except Exception:
            trade_lines_tg.append(f"• {sym}")
            trade_lines_ig.append(f"{sym}")

    record = f"{wins}/{len(trades)} green" if trades else "no trades logged"

    tg = (
        f"🔔 <b>SPY END-OF-DAY RECAP</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{arrow} <b>MARKET WRAP</b>\n"
        f"Open ${spy_open} → Close <b>${spy_close}</b>  {chg_str}\n"
        f"Range: ${spy_low} – ${spy_high}\n"
        f"Regime: {regime}\n"
    )
    if note:
        tg += f"📝 {note}\n"
    tg += f"\n💼 <b>MY TRADES</b> ({record})\n"
    tg += ("\n".join(trade_lines_tg) if trade_lines_tg else "No trades today")
    if trades:
        tg += f"\n\n<b>Net: {'+' if total_pl>=0 else ''}${round(total_pl,2)}/contract</b>"
    tg += "\n━━━━━━━━━━━━━━━"

    ig = (
        f"\n\n———————————————\n"
        f"📋 <b>INSTAGRAM CAPTION (copy below)</b>\n"
        f"———————————————\n\n"
        f"📊 SPY DAILY RECAP\n"
        f"{arrow} ${spy_close} {chg_str}\n"
        f"Range ${spy_low}–${spy_high}\n\n"
    )
    if note:
        ig += f"{note}\n\n"
    if trade_lines_ig:
        ig += "Trades: " + "  |  ".join(trade_lines_ig) + "\n\n"
    ig += "#SPY #SP500 #OptionsTrading #LogicalMe #DayTrading #0DTE #Trading"

    return tg + ig


LAST_MORNING_BRIEF = {}


def auto_generate_note(spy_open, spy_close, spy_high, spy_low):
    if not (spy_open and spy_close):
        return ""
    mb       = LAST_MORNING_BRIEF or {}
    am_lean  = str(mb.get("7hr_lean", "")).upper()
    am_ma999 = mb.get("ma999")
    fakeout  = str(mb.get("fakeout", "none"))

    day_dir   = "up" if spy_close > spy_open else "down" if spy_close < spy_open else "flat"
    closed_above_999 = (am_ma999 is not None and spy_close is not None and spy_close > float(am_ma999))

    parts = []
    if am_lean == "LONG" and day_dir == "down":
        parts.append("Bull morning trapped - rejected and dumped to bearish PM")
    elif am_lean == "SHORT" and day_dir == "up":
        parts.append("Bear morning trapped - reclaimed and ripped to bullish PM")
    elif am_lean == "LONG" and day_dir == "up":
        parts.append("Clean bull day - AM lean confirmed, trend held")
    elif am_lean == "SHORT" and day_dir == "down":
        parts.append("Clean bear day - AM lean confirmed, trend held")
    else:
        if day_dir == "up":
            parts.append("SPY closed higher on the day")
        elif day_dir == "down":
            parts.append("SPY closed lower on the day")
        else:
            parts.append("SPY closed flat")

    if am_ma999 is not None and spy_close is not None:
        parts.append("closed above 999 EMA" if closed_above_999 else "closed below 999 EMA")

    try:
        rng = float(spy_high) - float(spy_low)
        if rng > 8:
            parts.append("wide range session")
    except Exception:
        pass

    if fakeout and fakeout != "none" and "TRAP" in fakeout:
        parts.append("Fake-Out flag fired AM")

    return " - ".join(parts)


def fetch_spy_quote():
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        return None
    try:
        url = f"https://financialmodelingprep.com/stable/quote?symbol=SPY&apikey={api_key}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        arr = resp.json()
        if not arr or not isinstance(arr, list):
            return None
        q = arr[0]
        return {
            "spy_open":  q.get("open"),
            "spy_high":  q.get("dayHigh"),
            "spy_low":   q.get("dayLow"),
            "spy_close": q.get("price"),
        }
    except Exception:
        return None


@app.route("/recap", methods=["POST"])
def recap():
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not data.get("spy_close"):
            quote = fetch_spy_quote()
            if quote:
                data = {**quote, **data}
        if not data.get("regime"):
            try:
                ma999 = float((LAST_MORNING_BRIEF or {}).get("ma999", 0))
                close = float(data.get("spy_close", 0))
                if ma999 and close:
                    data["regime"] = "BULL" if close > ma999 else "BEAR"
            except Exception:
                pass
        if not data.get("note"):
            # API-written recap narrative (once a day); falls back to
            # auto_generate_note() internally if the call fails.
            data["note"] = get_recap_narrative(data)
        message = build_eod_recap(data)
        send_telegram(message)
        return jsonify({"status": "ok", "recap": "sent"}), 200
    except Exception as e:
        notify_error_once(f"Recap error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 200


# ============================================================
# === MANUAL AM BRIEF TRIGGER + 9:00 ET SCHEDULER
# ============================================================
def run_morning_brief_job():
    """Fires the AM brief from the last stashed MORNING_BRIEF payload.
    If none stashed yet, sends a short heads-up instead of crashing."""
    try:
        if LAST_MORNING_BRIEF:
            post_morning_brief(LAST_MORNING_BRIEF)
        else:
            send_telegram("🗓️ Morning brief scheduled, but no MORNING_BRIEF payload received yet today.")
    except Exception as e:
        notify_error_once(f"AM brief job error: {str(e)}")


@app.route("/run-brief", methods=["GET", "POST"])
def run_brief():
    run_morning_brief_job()
    return jsonify({"status": "ok", "brief": "fired"}), 200


try:
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone="America/New_York")
    scheduler.add_job(run_morning_brief_job, "cron", day_of_week="mon-fri", hour=9, minute=0)
    scheduler.start()
except Exception as _sched_err:
    print(f"[scheduler] not started: {_sched_err}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
