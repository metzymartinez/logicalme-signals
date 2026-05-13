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
    TradingView sends plain text: 'EARLY_CALL SPY 737.40 14:32'
    Returns a structured dict with signal_type, ticker, price, time.
    Also handles JSON payloads for future flexibility.
    """
    raw_body = raw_body.strip()

    # Try JSON first (future-proof)
    try:
        data = json.loads(raw_body)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Parse plain text format: SIGNAL_TYPE TICKER PRICE TIME
    parts = raw_body.split()
    result = {"raw": raw_body}

    if len(parts) >= 1:
        result["signal_type"] = parts[0].upper()
    if len(parts) >= 2:
        result["ticker"] = parts[1]
    if len(parts) >= 3:
        result["price"] = parts[2]
    if len(parts) >= 4:
        result["time"] = " ".join(parts[3:])

    return result


def build_claude_prompt(signal_type: str, ticker: str, price: str, time_et: str, config: dict) -> str:
    direction = config["direction"]
    option_type = config["option"]
    description = config["description"]
    entry_note = config["entry_note"]
    conviction_base = config["conviction_base"]
    emoji = config["emoji"]

    return f"""You are Logical Me, an intraday SPY options signal system using the Oliver Velez Pristine Method and Walter Bressert cycle analysis.

A Pine Script alert just fired on the SPY 2-minute chart:

SIGNAL: {signal_type}
TICKER: {ticker}
PRICE: ${price}
TIME (ET): {time_et}
DIRECTION: {direction}
SIGNAL DESCRIPTION: {description}
PINE SCRIPT CONVICTION BASE: {conviction_base}/6

This signal already passed these gates in Pine Script (barstate.isconfirmed, no repaint):
{"- 20ma ABOVE 200ma" if direction == "LONG" else "- 20ma BELOW 200ma"}
{"- Price ABOVE 20ma" if direction == "LONG" else "- Price BELOW 20ma"}
{"- MACD bullish / zero cross up" if direction == "LONG" else "- MACD bearish / zero cross down"}
{"- CDV green or flipping green" if direction == "LONG" else "- CDV red or slowing"}
{"- Stoch curling or crossing up" if direction == "LONG" else "- Stoch curling or crossing down"}
{"- At or near support level" if direction == "LONG" else "- At or near resistance level"}

Your job: Write a tight Telegram signal message. Use this exact format:

{emoji} {signal_type} — {ticker} ${price} @ {time_et}
━━━━━━━━━━━━━━━━━━━━
Option: 0DTE {option_type}
Conviction: {conviction_base}/6
{entry_note}

Gates passed: [list the 4-5 key gates that fired, one per line with ✅]

⚡ Read: [One sharp sentence — what the chart is telling you right now. Be specific about price action, not generic.]

💰 Budget: $50-60 max | No trades before 9:50 AM | Wait for pullback
⚠️ Not financial advice. Educational only."""


def analyze_with_claude(parsed: dict) -> str:
    signal_type = parsed.get("signal_type", "UNKNOWN").upper()
    ticker = parsed.get("ticker", "SPY")
    price = parsed.get("price", "unknown")
    time_et = parsed.get("time", "unknown")

    config = SIGNAL_CONFIG.get(signal_type)

    if not config:
        # Unknown signal — fall back to a generic message
        return (
            f"⚠️ <b>Unknown signal type: {signal_type}</b>\n"
            f"Ticker: {ticker} | Price: ${price} | Time: {time_et}\n"
            f"Check Pine Script alertcondition names match server SIGNAL_CONFIG keys."
        )

    prompt = build_claude_prompt(signal_type, ticker, price, time_et, config)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
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
        parsed = parse_tradingview_message(raw_body)
        signal_type = parsed.get("signal_type", "UNKNOWN")

        analysis = analyze_with_claude(parsed)

        header = f"<b>LOGICAL ME — {signal_type}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        send_telegram(header + analysis)

        return jsonify({"status": "ok", "signal": signal_type}), 200

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
    Simulates an EARLY_CALL firing — hits the same code path as a real TradingView webhook.
    Visit https://logicalme-signals.onrender.com/test in your browser to wake the server
    and confirm the full pipeline: parse → Claude → Telegram.
    """
    fake_payload = "EARLY_CALL SPY 737.40 13:12 ET"
    parsed = parse_tradingview_message(fake_payload)
    analysis = analyze_with_claude(parsed)

    header = "<b>LOGICAL ME TEST — EARLY_CALL</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    send_telegram(header + analysis)

    return jsonify({
        "status": "test sent to Telegram",
        "parsed": parsed,
        "analysis": analysis,
    }), 200


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "Logical Me Signal Server running",
        "signals_supported": list(SIGNAL_CONFIG.keys()),
        "model": "claude-sonnet-4-20250514",
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
