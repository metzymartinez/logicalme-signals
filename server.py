import os
import json
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload)

def analyze_with_claude(data):
    price = data.get("price", "unknown")
    ma20 = data.get("ma20", "unknown")
    ma200 = data.get("ma200", "unknown")
    macd = data.get("macd", "unknown")
    macd_signal = data.get("macd_signal", "unknown")
    bressert = data.get("bressert", "unknown")
    time_et = data.get("time", "unknown")
    volume = data.get("volume", "unknown")
    prev_close = data.get("prev_close", "unknown")

    prompt = f"""You are Logical Me, an intraday trading signal system combining Oliver Velez Pristine Method and Walter Bressert cycle analysis.

Current SPY 2-minute chart data as of {time_et} ET:
- Price: ${price}
- 20ma: {ma20}
- 200ma: {ma200}
- MACD value: {macd}
- MACD signal line: {macd_signal}
- Bressert oscillator: {bressert} (scale 0-100, below 20 oversold, above 80 overbought)
- Volume: {volume}
- Previous close: ${prev_close}

Apply these exact rules in order:

GATE 1 — VELEZ MA CHECK:
- Is 20ma above 200ma? (bullish) or below? (bearish)
- Is price above the 20ma?
- Is the gap between 20ma and 200ma widening? (strength) or narrowing? (caution)

GATE 2 — VELEZ CANDLE & STRUCTURE:
- Is price making higher highs and higher lows? (uptrend confirmed)
- Is this a pullback entry (preferred) or a breakout chase (avoid)?
- 5-bar rule: warn if extended run without pullback

GATE 3 — MACD:
- Is MACD above or below signal line?
- Is MACD histogram expanding or contracting?

GATE 4 — BRESSERT CYCLE:
- Is oscillator in oversold zone (under 20) = potential bounce
- Is oscillator mid-range (20-70) = room to run
- Is oscillator overbought (above 80) = caution, reduce size

GATE 5 — LOGICAL ME CONVICTION SCORE:
Score 1 point for each:
- 20ma above 200ma
- Price above 20ma
- MACD bullish
- Bressert under 70 (room to run)
- Higher lows structure intact
- Not extended (5-bar rule clear)
Max score = 6. Report as X/6.

OUTPUT FORMAT (keep it tight for Telegram):
Line 1: Signal emoji + action (🟢 LONG ENTRY / 🔴 SHORT ENTRY / 🟡 WAIT / ⚫ STAY OUT)
Line 2: Price and time
Line 3-7: Each gate result with ✅ or ❌
Line 8: Conviction score X/6
Line 9: Entry zone, target, stop, R:R ratio
Line 10: One sentence Claude read of the overall situation
Line 11: ⚠️ Not financial advice. Educational only.

Be direct. No fluff. Traders need fast answers."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

@app.route("/alert", methods=["POST"])
def receive_alert():
    try:
        data = request.get_json(force=True)
        if not data:
            raw = request.data.decode("utf-8")
            try:
                data = json.loads(raw)
            except:
                data = {"price": raw}

        analysis = analyze_with_claude(data)

        header = (
            f"<b>LOGICAL ME — SPY SIGNAL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
        send_telegram(header + analysis)
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        error_msg = f"⚠️ Logical Me error: {str(e)}"
        send_telegram(error_msg)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/test", methods=["GET"])
def test():
    test_data = {
        "price": "737.40",
        "ma20": "737.10",
        "ma200": "735.80",
        "macd": "0.42",
        "macd_signal": "0.28",
        "bressert": "45",
        "time": "9:53 AM",
        "volume": "1250000",
        "prev_close": "737.62"
    }
    analysis = analyze_with_claude(test_data)
    header = (
        f"<b>LOGICAL ME TEST SIGNAL — SPY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    send_telegram(header + analysis)
    return jsonify({"status": "test sent", "analysis": analysis}), 200

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Logical Me Signal Server running"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
