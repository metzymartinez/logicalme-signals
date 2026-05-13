import os
import json
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Signal metadata — maps Pine Script alert name to context Claude needs
SIGNAL_CONFIG = {
    "EARLY_CALL": {
        "direction": "LONG",
        "emoji": "🟢",
        "option": "CALL",
        "conviction_base": 4,
        "description": "Early call signal — at support, stoch curling up, CDV flipping green",
        "entry_note": "Lottery ticket entry. $50-60 max. Target 2x-3x.",
    },
    "STAR_LONG": {
        "direction": "LONG",
        "emoji": "⭐",
        "option": "CALL",
        "conviction_base": 6,
        "description": "STAR LONG — highest conviction. MACD zero cross + CDV green + stoch cross up + curl up",
        "entry_note": "Highest conviction setup. Size up within rules. Target 3x+.",
    },
    "LONG": {
        "direction": "LONG",
        "emoji": "🟢",
        "option": "CALL",
        "conviction_base": 5,
        "description": "Long signal — 5/6 Velez gates green, CDV confirming",
        "entry_note": "Standard pullback entry. $50-60 max.",
    },
    "EARLY_PUT": {
        "direction": "SHORT",
        "emoji": "🔴",
        "option": "PUT",
        "conviction_base": 4,
        "description": "Early put signal — at resistance, stoch curling down, CDV slowing",
        "entry_note": "Lottery ticket entry. $50-60 max. Target 2x-3x.",
    },
    "STAR_SHORT": {
        "direction": "SHORT",
        "emoji": "⭐",
        "option": "PUT",
        "conviction_base": 6,
        "description": "STAR SHORT — highest conviction. At resistance + curl down + stoch cross down + CDV flip red",
        "entry_note": "Highest conviction short setup. Size up within rules. Target 3x+.",
    },
    "SHORT": {
        "direction": "SHORT",
        "emoji": "🔴",
        "option": "PUT",
        "conviction_base": 5,
        "description": "Short signal — 5/6 Velez short gates, CDV red, not at support",
        "entry_note": "Standard rally-to-20ma short entry. $50-60 max.",
    },
}


def parse_tradingview_message(raw_body: str) -> dict:
    """
    Supports two formats:

    1. JSON (recommended — set your TV alert message to JSON):
       {"signal":"STAR_LONG","ticker":"SPY","price":736.67,"time":"2025-05-13T17:03:36Z",
        "open":736.40,"high":736.75,"low":736.00,"volume":1234567,"bar_time":"2025-05-13T17:03:00Z"}

    2. Plain text (legacy fallback):
       STAR_LONG SPY 736.67 17:03 open=736.40 high=736.75 low=736.00 vol=1234567
    """
    raw_body = raw_body.strip()

    # ── JSON path ──────────────────────────────────────────────
    try:
        data = json.loads(raw_body)
        if isinstance(data, dict):
            # Normalize field names
            normalized = {
                "signal_type":    str(data.get("signal", data.get("signal_type", "UNKNOWN"))).upper(),
                "ticker":         data.get("ticker", "SPY"),
                "price":          str(data.get("price", data.get("close", "unknown"))),
                "time":           data.get("time", data.get("bar_time", "unknown")),
                "open":           str(data.get("open", "")),
                "high":           str(data.get("high", "")),
                "low":            str(data.get("low", "")),
                "volume":         str(data.get("volume", "")),
                "bar_time":       data.get("bar_time", ""),
                "ma20":           str(data.get("ma20", "")),
                "ldnH":           str(data.get("ldnH", "")),
                "ldnL":           str(data.get("ldnL", "")),
                "nyH":            str(data.get("nyH", "")),
                "nyL":            str(data.get("nyL", "")),
                "at_support":     data.get("at_support", False),
                "at_resistance":  data.get("at_resistance", False),
                "score":          str(data.get("score", "")),
                "raw":            raw_body,
            }
            return normalized
    except (json.JSONDecodeError, ValueError):
        pass

    # ── Plain text path ────────────────────────────────────────
    # Expected: SIGNAL TICKER PRICE TIME [key=value ...]
    result = {"raw": raw_body}
    parts = raw_body.split()

    if len(parts) >= 1:
        result["signal_type"] = parts[0].upper()
    if len(parts) >= 2:
        result["ticker"] = parts[1]
    if len(parts) >= 3:
        result["price"] = parts[2]
    if len(parts) >= 4:
        # Time may be "13:12" or "13:12 ET" — grab until next key=value or end
        time_parts = []
        extra_parts = []
        for p in parts[3:]:
            if "=" in p:
                extra_parts.append(p)
            else:
                time_parts.append(p)
        result["time"] = " ".join(time_parts)

        # Parse optional key=value extras: open=736.40 high=736.75 low=736.00 vol=1234567
        for kv in extra_parts:
            k, v = kv.split("=", 1)
            k = k.lower().strip()
            if k in ("open", "o"):
                result["open"] = v
            elif k in ("high", "h"):
                result["high"] = v
            elif k in ("low", "l"):
                result["low"] = v
            elif k in ("volume", "vol", "v"):
                result["volume"] = v

    return result


def format_candle_context(parsed: dict) -> str:
    """Build a readable candle data block if OHLV data is present."""
    lines = []
    if parsed.get("open"):
        lines.append(f"  Open:   ${parsed['open']}")
    if parsed.get("high"):
        lines.append(f"  High:   ${parsed['high']}")
    if parsed.get("low"):
        lines.append(f"  Low:    ${parsed['low']}")
    if parsed.get("price"):
        lines.append(f"  Close:  ${parsed['price']}")
    if parsed.get("volume"):
        vol = parsed["volume"]
        try:
            vol = f"{int(float(vol)):,}"
        except (ValueError, TypeError):
            pass
        lines.append(f"  Volume: {vol}")
    return "\n".join(lines) if lines else "  (no candle data)"


def compute_candle_stats(parsed: dict) -> dict:
    """Derive range, body size, and direction from OHLCV if available."""
    stats = {}
    try:
        o = float(parsed.get("open", 0))
        h = float(parsed.get("high", 0))
        l = float(parsed.get("low", 0))
        c = float(parsed.get("price", 0))
        if all([o, h, l, c]):
            stats["range"] = round(h - l, 2)
            stats["body"] = round(abs(c - o), 2)
            stats["candle_direction"] = "bullish" if c > o else "bearish"
            stats["wick_upper"] = round(h - max(o, c), 2)
            stats["wick_lower"] = round(min(o, c) - l, 2)
    except (ValueError, TypeError):
        pass
    return stats



def suggest_strike(parsed: dict, direction: str) -> str:
    """
    Suggest a 0DTE strike based on price, support/resistance, and London levels.
    Logic:
      CALL — buy ATM or 1 strike OTM above price, biased toward resistance as target
      PUT  — buy ATM or 1 strike OTM below price, biased toward support as target
    SPY options trade in $1 increments.
    """
    try:
        price = float(parsed.get("price", 0))
        if not price:
            return "ATM"

        # Round to nearest $1 strike
        atm = round(price)

        if direction == "LONG":
            # Entry: ATM or 1 strike above if price is closer to upper half
            entry_strike = atm if (price - atm) < 0.50 else atm + 1
            # Target: resistance level if available
            res = parsed.get("ldnH") or parsed.get("nyH")
            if res and res not in ("", "null", "None"):
                target = round(float(res))
                return f"${entry_strike}C → target ${target}C"
            return f"${entry_strike}C"

        else:  # SHORT
            entry_strike = atm if (atm - price) < 0.50 else atm - 1
            sup = parsed.get("ldnL") or parsed.get("nyL")
            if sup and sup not in ("", "null", "None"):
                target = round(float(sup))
                return f"${entry_strike}P → target ${target}P"
            return f"${entry_strike}P"

    except (ValueError, TypeError):
        return "ATM"


def build_claude_prompt(parsed: dict, config: dict) -> str:
    signal_type  = parsed.get("signal_type", "UNKNOWN")
    ticker       = parsed.get("ticker", "SPY")
    price        = parsed.get("price", "unknown")
    time_et      = parsed.get("time", "unknown")
    direction    = config["direction"]
    option_type  = config["option"]
    description  = config["description"]
    entry_note   = config["entry_note"]
    conviction   = config["conviction_base"]
    emoji        = config["emoji"]

    candle_block = format_candle_context(parsed)
    stats        = compute_candle_stats(parsed)
    strike       = suggest_strike(parsed, direction)

    # Level context
    ldn_h = parsed.get("ldnH", "")
    ldn_l = parsed.get("ldnL", "")
    ny_h  = parsed.get("nyH",  "")
    ny_l  = parsed.get("nyL",  "")
    ma20  = parsed.get("ma20", "")
    at_sup = parsed.get("at_support", False)
    at_res = parsed.get("at_resistance", False)

    levels_block = (
        f"  London High: ${ldn_h}\n" if ldn_h and ldn_h not in ("", "null") else ""
    ) + (
        f"  London Low:  ${ldn_l}\n" if ldn_l and ldn_l not in ("", "null") else ""
    ) + (
        f"  NY High:     ${ny_h}\n"  if ny_h  and ny_h  not in ("", "null") else ""
    ) + (
        f"  NY Low:      ${ny_l}\n"  if ny_l  and ny_l  not in ("", "null") else ""
    ) + (
        f"  20MA:        ${ma20}\n"  if ma20  and ma20  not in ("", "null") else ""
    ) + (
        f"  At Support:  {'YES' if at_sup else 'NO'}\n"
    ) + (
        f"  At Resistance: {'YES' if at_res else 'NO'}\n"
    )

    stats_lines = ""
    if stats:
        stats_lines = (
            f"\nCandle stats (auto-computed):\n"
            f"  Range: ${stats.get('range','?')} | Body: ${stats.get('body','?')} | "
            f"Direction: {stats.get('candle_direction','?')}\n"
            f"  Upper wick: ${stats.get('wick_upper','?')} | Lower wick: ${stats.get('wick_lower','?')}"
        )

    gates = (
        [
            "20MA ABOVE 200MA — uptrend structure intact",
            "Price ABOVE 20MA — bulls in control",
            "MACD bullish zero-cross up — momentum confirming",
            "CDV green or flipping green — money flow rotating long",
            "Stoch curling or crossing up at support — cycle low",
        ]
        if direction == "LONG"
        else [
            "20MA BELOW 200MA — downtrend structure intact",
            "Price BELOW 20MA — bears in control",
            "MACD bearish / zero cross down — momentum confirming",
            "CDV red or slowing — money flow rotating short",
            "Stoch curling or crossing down at resistance — cycle high",
        ]
    )
    gates_text = "\n".join(f"✅ {g}" for g in gates)

    return f"""You are Logical Me, an intraday SPY options signal system using the Oliver Velez Pristine Method and Walter Bressert cycle analysis.

A Pine Script alert just fired on the {ticker} 2-minute chart:

SIGNAL:    {signal_type}
TICKER:    {ticker}
CLOSE:     ${price}
TIME (ET): {time_et}
DIRECTION: {direction}
DESCRIPTION: {description}
CONVICTION: {conviction}/6

Candle data from TradingView (live, filled by {{{{placeholders}}}}):
{candle_block}
{stats_lines}

Key levels (live from Pine Script):
{levels_block}
Suggested 0DTE strike: {strike}

Gates confirmed by Pine Script (barstate.isconfirmed, no repaint):
{gates_text}

Write a tight Telegram signal message in this EXACT format — no deviations:

{emoji} {signal_type} — {ticker} ${price} @ {time_et}
━━━━━━━━━━━━━━━━━━━━
Option: 0DTE {option_type}
Conviction: {conviction}/6
{entry_note}

Gates passed:
[list the 4-5 key gates that fired, one per line with ✅]

📊 Candle: [one line — close vs open, range, any notable wicks. Use the actual numbers.]

⚡ Read: [One sharp sentence — what price action + candle structure tells you RIGHT NOW. Be specific, use the actual price levels from the data. No generic statements.]

🎯 Strike: {strike}
💰 Budget: $50-60 max | No trades before 9:50 AM | Wait for pullback
⚠️ Not financial advice. Educational only."""


def analyze_with_claude(parsed: dict) -> str:
    signal_type = parsed.get("signal_type", "UNKNOWN").upper()
    ticker      = parsed.get("ticker", "SPY")
    price       = parsed.get("price", "unknown")
    time_et     = parsed.get("time", "unknown")

    config = SIGNAL_CONFIG.get(signal_type)

    if not config:
        return (
            f"⚠️ <b>Unknown signal: {signal_type}</b>\n"
            f"Ticker: {ticker} | Price: ${price} | Time: {time_et}\n"
            f"Check Pine Script alertcondition names match SIGNAL_CONFIG keys.\n"
            f"Raw: {parsed.get('raw', '')}"
        )

    prompt = build_claude_prompt(parsed, config)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


@app.route("/alert", methods=["POST"])
def receive_alert():
    try:
        raw_body = request.data.decode("utf-8")
        parsed   = parse_tradingview_message(raw_body)
        signal_type = parsed.get("signal_type", "UNKNOWN")

        analysis = analyze_with_claude(parsed)

        header = f"<b>LOGICAL ME — {signal_type}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        send_telegram(header + analysis)

        return jsonify({"status": "ok", "signal": signal_type, "parsed": parsed}), 200

    except Exception as e:
        error_msg = f"⚠️ Logical Me error: {str(e)}"
        try:
            send_telegram(error_msg)
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/test", methods=["GET"])
def test():
    """
    Returns JSON only — does NOT send to Telegram.
    To send to Telegram: /test?send=true&key=YOUR_TEST_KEY
    Set TEST_KEY as an environment variable on Render.
    """
    fake_payload = json.dumps({
        "signal": "STAR_LONG",
        "ticker": "SPY",
        "price": 736.67,
        "time": "13:12 ET",
        "open": 736.10,
        "high": 736.75,
        "low": 735.90,
        "volume": 1482300,
        "bar_time": "2025-05-13T17:12:00Z",
        "ma20": 736.20,
        "ldnH": 737.61,
        "ldnL": 735.73,
        "nyH": 736.75,
        "nyL": 735.90,
        "at_support": True,
        "at_resistance": False,
        "score": 6
    })

    parsed   = parse_tradingview_message(fake_payload)
    analysis = analyze_with_claude(parsed)

    # Only send to Telegram if explicitly requested with correct key
    telegram_sent = False
    if request.args.get("send") == "true":
        provided_key = request.args.get("key", "")
        test_key     = os.environ.get("TEST_KEY", "")
        if provided_key and test_key and provided_key == test_key:
            header = "<b>LOGICAL ME TEST — STAR_LONG</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            send_telegram(header + analysis)
            telegram_sent = True
        else:
            return jsonify({"status": "unauthorized — wrong or missing TEST_KEY"}), 401

    return jsonify({
        "status": "ok — Telegram sent" if telegram_sent else "ok — JSON only, no Telegram",
        "parsed": parsed,
        "analysis": analysis,
    }), 200


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "Logical Me Signal Server running",
        "signals_supported": list(SIGNAL_CONFIG.keys()),
        "model": "claude-sonnet-4-6",
        "alert_format": {
            "recommended": "JSON with {{ticker}}, {{close}}, {{open}}, {{high}}, {{low}}, {{volume}}, {{time}}",
            "example": '{"signal":"STAR_LONG","ticker":"{{ticker}}","price":{{close}},"open":{{open}},"high":{{high}},"low":{{low}},"volume":{{volume}},"time":"{{time}}"}'
        }
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
