import os
import json
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
client = Anthropic()

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RENDER_URL       = os.environ.get("RENDER_URL", "http://localhost:5000")

# ============================================================
# === SIGNAL CONFIG — matches v11 alert() names exactlys
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
# === PARSER — reads full v11 JSON payload
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
# === CLAUDE ONE-LINER
# ============================================================
def get_claude_read(parsed: dict, config: dict) -> str:
    price     = parsed.get("price", "?")
    direction = config["direction"]
    ma999     = parsed.get("ma999", "")
    ma200     = parsed.get("ma200", "")
    ma20      = parsed.get("ma20", "")
    ldn_h     = parsed.get("ldnH", "")
    ldn_l     = parsed.get("ldnL", "")
    regime    = parsed.get("regime", "")
    score     = parsed.get("score", "")
    cdv_4h    = parsed.get("cdv_4h", "")
    cdv_1h    = parsed.get("cdv_1h", "")
    cdv_15m   = parsed.get("cdv_15m", "")
    cdv_2m    = parsed.get("cdv_2m", "")

    prompt = f"""You are Logical Me — an intraday SPY options signal system using Oliver Velez Pristine Method + CDV + 999 EMA.

Signal: {config['label']} | Direction: {direction} | Score: {score}/6
SPY price: ${price} | Regime: {regime}
London High: ${ldn_h} | London Low: ${ldn_l}
20 SMA: ${ma20} | 200 SMA: ${ma200} | 999 EMA: ${ma999}
CDV — 4H: {cdv_4h} | 1H: {cdv_1h} | 15m: {cdv_15m} | 2m: {cdv_2m}

Write ONE sharp sentence — what the tape is telling us RIGHT NOW.
Use actual price levels. Be specific. No generic statements. No emojis. Max 20 words."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


# ============================================================
# === TELEGRAM MESSAGE BUILDER
# ============================================================
def build_telegram_message(parsed: dict, config: dict) -> str:
    signal_type = parsed.get("signal_type", "UNKNOWN")
    ticker      = parsed.get("ticker", "SPY")
    price_str   = parsed.get("price", "?")
    time_et     = parsed.get("time", "?")
    direction   = config["direction"]
    emoji       = config["emoji"]
    option_type = config["option"]
    score       = parsed.get("score", "?")
    regime      = parsed.get("regime", "")
    rvol        = parsed.get("rvol", False)

    cdv_4h  = parsed.get("cdv_4h",  "")
    cdv_1h  = parsed.get("cdv_1h",  "")
    cdv_15m = parsed.get("cdv_15m", "")
    cdv_2m  = parsed.get("cdv_2m",  "")
    cdv_line = f"{cdv_emoji(cdv_4h)}4H {cdv_emoji(cdv_1h)}1H {cdv_emoji(cdv_15m)}15m {cdv_emoji(cdv_2m)}2m"

    targets = compute_targets(parsed, direction)
    strike  = suggest_strike(parsed, direction)

    ma999 = parsed.get("ma999", "")
    ma200 = parsed.get("ma200", "")
    ma20  = parsed.get("ma20",  "")

    pattern_warn = check_200_pattern(parsed, direction)
    read_line    = get_claude_read(parsed, config)

    regime_tag = "🟡 BULL DAY" if regime == "BULL" else "🔴 BEAR DAY" if regime == "BEAR" else ""
    rvol_tag   = " | RVOL ✓" if rvol else ""

    msg = (
        f"{emoji} <b>{config['label']}</b> — {ticker} @ <b>${price_str}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕐 {time_et} | Score: {score}/6 | {regime_tag}{rvol_tag}\n"
        f"\n📊 <b>CDV</b>\n"
        f"{cdv_line}\n"
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
# === HELPERS
# ============================================================
def send_telegram(message: str):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    resp    = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


# ============================================================
# === SESSION BRIEF — 9AM ET WEEKDAY SCHEDULER
# ============================================================

BRIEF_PROMPT = """You are Logical Me — SPY session brief system.

PRE-FLIGHT (do this first, do not skip):
1. Call data_get_pine_tables on the SPY 2min chart.
2. If the table is empty — stop immediately. Output only:
   "⚠️ Logical Me isn't painting. Load it on the SPY 2min chart and re-run."
3. If the table has data — continue.

DATA COLLECTION (read don't reconstruct):
- Read CDV, levels, and signal data directly from data_get_pine_tables and data_get_pine_labels.
- Do NOT call data_get_ohlcv to reconstruct anything Logical Me already calculated.
- Use quote_get for current price only.
- One targeted news search for macro/catalyst context.

BUILD THE BRIEF:
- Price, CDV flow, key levels from pine table, news/events, playbook bias.
- If any section is missing data say so clearly — do not fill in with guesses.

FORMAT (Telegram-ready, no hashtags, no sources block):
🗓️ [DAY, DATE] · SPY SESSION BRIEF
━━━━━━━━━━━━━━━━━━━━━
📍 PRICE: $[price]
━━━━━━━━━━━━━━━━━━━━━
📊 FLOW (CDV)
[cdv alignment across timeframes]
━━━━━━━━━━━━━━━━━━━━━
🗺️ MAP
[key levels from pine table]
━━━━━━━━━━━━━━━━━━━━━
📰 NEWS + EVENTS
[macro bullets]
━━━━━━━━━━━━━━━━━━━━━
🎯 PLAYBOOK
BIAS: [BULL / BEAR / NEUTRAL] — [one sentence why]
[levels to watch, key triggers]
━━━━━━━━━━━━━━━━━━━━━"""


def run_session_brief():
    """Called by scheduler at 9:00 AM ET weekdays."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": BRIEF_PROMPT}]
        )
        brief = response.content[0].text.strip()
        send_telegram(brief)
    except Exception as e:
        try:
            send_telegram(f"⚠️ Session brief failed: {str(e)}")
        except Exception:
            pass


# Start scheduler
scheduler = BackgroundScheduler(timezone="America/New_York")
scheduler.add_job(run_session_brief, "cron", day_of_week="mon-fri", hour=9, minute=0)
scheduler.start()


# ============================================================
# === ROUTES
# ============================================================
@app.route("/alert", methods=["POST"])
def receive_alert():
    try:
        raw_body    = request.data.decode("utf-8")
        parsed      = parse_tradingview_message(raw_body)
        signal_type = parsed.get("signal_type", "UNKNOWN")
        config      = SIGNAL_CONFIG.get(signal_type)

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
        error_msg = f"⚠️ Logical Me error: {str(e)}"
        try:
            send_telegram(error_msg)
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/run-brief", methods=["POST", "GET"])
def trigger_brief():
    """Manual trigger endpoint — GET or POST to fire brief immediately."""
    try:
        run_session_brief()
        return jsonify({"status": "brief sent"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/test", methods=["GET"])
def test():
    fake_payload = json.dumps({
        "signal":        "STAR_LONG",
        "ticker":        "SPY",
        "price":         736.67,
        "time":          "10:15 ET",
        "open":          736.10,
        "high":          736.75,
        "low":           735.90,
        "volume":        1482300,
        "ma20":          736.20,
        "ma33":          735.80,
        "ma200":         734.50,
        "ma999":         728.30,
        "ldnH":          740.92,
        "ldnL":          735.73,
        "nyH":           736.75,
        "nyL":           735.90,
        "at_support":    True,
        "at_resistance": False,
        "score":         6,
        "regime":        "BULL",
        "rvol":          True,
        "cdv_4h":        "GREEN",
        "cdv_1h":        "GREEN",
        "cdv_15m":       "GREEN",
        "cdv_2m":        "GREEN",
        "stoch_k":       28.5,
        "macd":          "BULL",
        "choch":         1,
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
        "status":            "Logical Me v11 Signal Server",
        "signals_supported": list(SIGNAL_CONFIG.keys()),
        "model":             "claude-sonnet-4-6",
        "version":           "v11",
        "brief_schedule":    "9:00 AM ET weekdays",
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
