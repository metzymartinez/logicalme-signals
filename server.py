import os
import json
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic()

TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Signal metadata
SIGNAL_CONFIG = {
    "MACD_CROSS_UP": {
        "direction": "LONG",
        "emoji": "🟢",
        "option": "CALL",
        "conviction": "MACD zero cross up",
    },
    "MACD_CROSS_DN": {
        "direction": "SHORT",
        "emoji": "🔴",
        "option": "PUT",
        "conviction": "MACD zero cross down",
    },
    "EARLY_CALL": {
        "direction": "LONG",
        "emoji": "🟡",
        "option": "CALL",
        "conviction": "Early — at support, stoch curling up, CDV flipping green",
    },
    "STAR_BUY": {
        "direction": "LONG",
        "emoji": "⭐",
        "option": "CALL",
        "conviction": "STAR — highest conviction, all gates confirmed",
    },
    "LONG": {
        "direction": "LONG",
        "emoji": "🟢",
        "option": "CALL",
        "conviction": "Volume + OR trigger confirmed, CDV not vetoing",
    },
    "CANDLE_BULL": {
        "direction": "LONG",
        "emoji": "🕯",
        "option": "CALL",
        "conviction": "Candle rejection at support — hammer / bull engulf / doji",
    },
    "EARLY_PUT": {
        "direction": "SHORT",
        "emoji": "🟡",
        "option": "PUT",
        "conviction": "Early — at resistance, stoch curling down, CDV slowing",
    },
    "STAR_SELL": {
        "direction": "SHORT",
        "emoji": "⭐",
        "option": "PUT",
        "conviction": "STAR — highest conviction, all gates confirmed",
    },
    "SHORT": {
        "direction": "SHORT",
        "emoji": "🔴",
        "option": "PUT",
        "conviction": "Volume + OR trigger confirmed, CDV not vetoing",
    },
    "PULLBACK": {
        "direction": "LONG",
        "emoji": "🔵",
        "option": "CALL",
        "conviction": "Pullback to 20ma in uptrend — reclaim entry",
    },
    "RALLY": {
        "direction": "SHORT",
        "emoji": "🔵",
        "option": "PUT",
        "conviction": "Rally to 20ma in downtrend — fade entry",
    },
    "CANDLE_BEAR": {
        "direction": "SHORT",
        "emoji": "🕯",
        "option": "PUT",
        "conviction": "Candle rejection at resistance — shooting star / bear engulf / doji",
    },
}


# ── Parsers ────────────────────────────────────────────────────────────────

def parse_tradingview_message(raw_body: str) -> dict:
    raw_body = raw_body.strip()
    try:
        data = json.loads(raw_body)
        if isinstance(data, dict):
            return {
                "signal_type":   str(data.get("signal", data.get("signal_type", "UNKNOWN"))).strip().upper().replace(" ", "_"),
                "ticker":        data.get("ticker", "SPY"),
                "price":         str(data.get("price", data.get("close", ""))),
                "time":          data.get("time", data.get("bar_time", "")),
                "open":          str(data.get("open", "")),
                "high":          str(data.get("high", "")),
                "low":           str(data.get("low", "")),
                "volume":        str(data.get("volume", "")),
                "ma20":          str(data.get("ma20", "")),
                "ldnH":          str(data.get("ldnH", "")),
                "ldnL":          str(data.get("ldnL", "")),
                "nyH":           str(data.get("nyH", "")),
                "nyL":           str(data.get("nyL", "")),
                "at_support":    data.get("at_support", False),
                "at_resistance": data.get("at_resistance", False),
                "score":         str(data.get("score", "")),
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


# ── Calculators ────────────────────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def suggest_strike(parsed: dict, direction: str) -> str:
    price = safe_float(parsed.get("price"))
    if not price:
        return "ATM"
    atm = round(price)
    if direction == "LONG":
        strike = atm if (price - atm) < 0.50 else atm + 1
        res = parsed.get("ldnH") or parsed.get("nyH")
        if res and res not in ("", "null", "None"):
            return f"${strike}C → target ${round(safe_float(res))}C"
        return f"${strike}C"
    else:
        strike = atm if (atm - price) < 0.50 else atm - 1
        sup = parsed.get("ldnL") or parsed.get("nyL")
        if sup and sup not in ("", "null", "None"):
            return f"${strike}P → target ${round(safe_float(sup))}P"
        return f"${strike}P"


def compute_targets(price: float, direction: str) -> dict:
    """
    Entry price unknown until filled — use estimated option price.
    For 0DTE ATM SPY options: rough entry ~$0.50-$1.50.
    We use a $1.00 placeholder until Greeks are wired.
    Conservative = 3x, Lottery = 8x, Stop = 50%.
    """
    entry_est = 1.00  # placeholder — will be replaced by Greeks later
    return {
        "entry_est":    f"${entry_est:.2f}",
        "conservative": f"${entry_est * 3:.2f}",
        "lottery":      f"${entry_est * 8:.2f}",
        "stop":         f"${entry_est * 0.50:.2f}",
    }


def compute_move(parsed: dict, direction: str) -> str:
    price = safe_float(parsed.get("price"))
    if not price:
        return "n/a"
    if direction == "LONG":
        target_raw = parsed.get("ldnH") or parsed.get("nyH")
    else:
        target_raw = parsed.get("ldnL") or parsed.get("nyL")
    target = safe_float(target_raw)
    if not target:
        return "n/a"
    move = target - price if direction == "LONG" else price - target
    sign = "+" if direction == "LONG" else "-"
    return f"{sign}${abs(move):.2f}"


def format_volume(vol_str: str) -> str:
    try:
        v = int(float(vol_str))
        if v >= 1_000_000:
            return f"{v/1_000_000:.1f}M"
        if v >= 1_000:
            return f"{v/1_000:.0f}K"
        return str(v)
    except (ValueError, TypeError):
        return vol_str or "n/a"


# ── Claude — one sentence only ─────────────────────────────────────────────

def get_claude_read(parsed: dict, config: dict) -> str:
    price     = parsed.get("price", "?")
    direction = config["direction"]
    ldn_h     = parsed.get("ldnH", "")
    ldn_l     = parsed.get("ldnL", "")
    ny_h      = parsed.get("nyH", "")
    ny_l      = parsed.get("nyL", "")
    ma20      = parsed.get("ma20", "")
    at_sup    = parsed.get("at_support", False)
    at_res    = parsed.get("at_resistance", False)
    conviction = config["conviction"]

    prompt = f"""You are Logical Me, an intraday SPY options signal system using Oliver Velez Pristine Method.

Signal fired: {config['option']} | Direction: {direction}
SPY price: ${price}
London High: ${ldn_h} | London Low: ${ldn_l}
NY High: ${ny_h} | NY Low: ${ny_l}
20MA: ${ma20}
At Support: {at_sup} | At Resistance: {at_res}
Conviction: {conviction}

Write ONE sharp sentence — what price action tells you RIGHT NOW. Use the actual price levels. Be specific. No generic statements. No emojis. Max 20 words."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


# ── Message builder ────────────────────────────────────────────────────────

def build_telegram_message(parsed: dict, config: dict) -> str:
    signal_type = parsed.get("signal_type", "UNKNOWN")
    ticker      = parsed.get("ticker", "SPY")
    price_str   = parsed.get("price", "?")
    time_et     = parsed.get("time", "?")
    direction   = config["direction"]
    emoji       = config["emoji"]
    option_type = config["option"]

    price     = safe_float(price_str)
    strike    = suggest_strike(parsed, direction)
    targets   = compute_targets(price, direction)
    move      = compute_move(parsed, direction)
    vol_fmt   = format_volume(parsed.get("volume", ""))

    # Level context
    ldn_h = parsed.get("ldnH", "")
    ldn_l = parsed.get("ldnL", "")

    target_label = ""
    if direction == "LONG" and ldn_h:
        target_label = f"Target: ${ldn_h} (London High) | Move: {move}"
    elif direction == "SHORT" and ldn_l:
        target_label = f"Target: ${ldn_l} (London Low) | Move: {move}"

    # Claude one-liner
    read_line = get_claude_read(parsed, config)

    # 15m alignment hint
    htf_line = "15m: check alignment before sizing up"

    msg = (
        f"{emoji} <b>{signal_type}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"SPY @ <b>${price_str}</b> | {time_et}\n"
        f"Vol: {vol_fmt}\n"
    )

    if target_label:
        msg += f"{target_label}\n"

    msg += (
        f"\n📊 <b>OPTION</b>\n"
        f"{strike} — 0DTE {option_type}\n"
        f"\n🎯 <b>TARGETS</b> (est. $1.00 entry)\n"
        f"Conservative: 3x → {targets['conservative']}\n"
        f"Lottery:      8x → {targets['lottery']}\n"
        f"Stop:        50% → {targets['stop']}\n"
        f"\n⚡ <i>{read_line}</i>\n"
        f"\n📈 {htf_line}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Place OTOCO on Webull\n"
        f"<i>Max $50-60 | Not financial advice</i>"
    )
    return msg


# ── Routes ─────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    resp    = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


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
                f"Check Pine Script alertcondition names match SIGNAL_CONFIG keys."
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


@app.route("/test", methods=["GET"])
def test():
    fake_payload = json.dumps({
        "signal":       "STAR_LONG",
        "ticker":       "SPY",
        "price":        736.67,
        "time":         "10:15 ET",
        "open":         736.10,
        "high":         736.75,
        "low":          735.90,
        "volume":       1482300,
        "ma20":         736.20,
        "ldnH":         740.92,
        "ldnL":         735.73,
        "nyH":          736.75,
        "nyL":          735.90,
        "at_support":   True,
        "at_resistance": False,
        "score":        6
    })

    parsed  = parse_tradingview_message(fake_payload)
    config  = SIGNAL_CONFIG.get(parsed["signal_type"], SIGNAL_CONFIG["STAR_BUY"])
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
        "status":           "Logical Me Signal Server running",
        "signals_supported": list(SIGNAL_CONFIG.keys()),
        "model":            "claude-sonnet-4-6",
        "alert_format": {
            "recommended": "JSON with signal, ticker, price, open, high, low, volume, time, ldnH, ldnL, nyH, nyL, ma20",
            "example":     '{"signal":"STAR_LONG","ticker":"SPY","price":736.67,"ldnH":740.92,"ldnL":735.73}'
        }
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
