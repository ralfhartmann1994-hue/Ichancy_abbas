import os
import time
import json
import re
from collections import deque
from flask import Flask, request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ================= Ø¥Ø¹Ø¯Ø§Ø¯Ø§Øª Ø§Ù„Ø¨ÙŠØ¦Ø© =================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("Ø¶Ø¹ TELEGRAM_TOKEN ÙÙŠ Render")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "0933000000")
PAYMENT_CODE = os.environ.get("PAYMENT_CODE", "7788297")
APP_URL = os.environ.get("APP_URL")  # Ù…Ø«Ø§Ù„: https://ichancy-abbas.onrender.com
PORT = int(os.environ.get("PORT", 10000))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

DATA_FILE = "users_data.json"
users = {}
incoming_sms = deque(maxlen=200)
SMS_CACHE_SECONDS = 5 * 60

(
    S_IDLE,
    S_WAIT_NAME,
    S_WAIT_AGE,
    S_MAIN_MENU,
    S_TOPUP_METHOD,
    S_WAIT_AMOUNT,
    S_WAIT_CONFIRM_SENT,
    S_WAIT_TRANSFER_CODE,
) = range(8)

def load_data():
    global users
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                users = json.load(f)
        except Exception:
            users = {}
    else:
        users = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def ensure_user(uid):
    key = str(uid)
    if key not in users:
        users[key] = {
            "state": S_IDLE,
            "full_name": None,
            "age": None,
            "successful_topups": 0,
            "pending": {},
        }

def kb_main():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("ðŸ’° ØªØ¹Ø¨Ø¦Ø© Ø§Ù„Ø­Ø³Ø§Ø¨"))
    kb.add(KeyboardButton("ðŸ“„ Ù…Ù„ÙÙŠ Ø§Ù„Ø´Ø®ØµÙŠ"))
    kb.add(KeyboardButton("ðŸ†˜ Ù…Ø³Ø§Ø¹Ø¯Ø©"))
    return kb

def kb_yes_no():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Ù†Ø¹Ù…"), KeyboardButton("Ù„Ø§"))
    return kb

def kb_back():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("â¬…ï¸ Ø±Ø¬ÙˆØ¹"))
    return kb

def kb_done_back():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("âœ… ØªÙ…"))
    kb.add(KeyboardButton("â¬…ï¸ Ø±Ø¬ÙˆØ¹"))
    return kb

def kb_only_syriatel():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Ø³ÙŠØ±ÙŠØªÙŠÙ„ ÙƒØ§Ø´"))
    kb.add(KeyboardButton("â¬…ï¸ Ø±Ø¬ÙˆØ¹"))
    return kb

def is_valid_full_name(name: str) -> bool:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return len(parts) >= 3

def is_valid_age(text: str) -> bool:
    return text.isdigit() and 10 <= int(text) <= 100

def is_valid_amount(text: str) -> bool:
    return text.isdigit() and 10000 <= int(text) <= 1000000 and int(text) % 5000 == 0

def clean_old_sms():
    now = time.time()
    while incoming_sms and (now - incoming_sms[0]["timestamp"] > SMS_CACHE_SECONDS):
        incoming_sms.popleft()

def add_incoming_sms(message: str, sender: str):
    clean_old_sms()
    incoming_sms.append({"message": message or "", "sender": sender or "", "timestamp": time.time()})

def match_sms_with(code: str, amount: int):
    clean_old_sms()
    pattern = r"ØªÙ…\s+Ø§Ø³ØªÙ„Ø§Ù…\s+Ù…Ø¨Ù„Øº\s+(\d+)\s*Ù„\.Ø³.*?Ø±Ù‚Ù…\s+Ø§Ù„Ø¹Ù…Ù„ÙŠØ©\s+Ù‡Ùˆ\s+(\d+)"
    for sms in list(incoming_sms):
        m = re.search(pattern, sms["message"], re.IGNORECASE)
        if not m:
            continue
        amount_str, op_code = m.group(1), m.group(2)
        amount_str = re.sub(r"[^\d]", "", amount_str)
        code_clean = re.sub(r"[^\d]", "", code)
        op_code_clean = re.sub(r"[^\d]", "", op_code)
        if amount_str == str(amount) and op_code_clean == code_clean:
            try:
                incoming_sms.remove(sms)
            except ValueError:
                pass
            return True, sms
    return False, None

def send_admin_notification(user_id, username, u, amount):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "ðŸ“¥ <b>ØªÙ…Øª ØªØ¹Ø¨Ø¦Ø© Ø§Ù„Ø­Ø³Ø§Ø¨ Ø¨Ù†Ø¬Ø§Ø­</b>\n\n"
        f"ðŸ‘¤ Ø§Ù„Ø§Ø³Ù…: {u.get('full_name')}\n"
        f"ðŸŽ‚ Ø§Ù„Ø¹Ù…Ø±: {u.get('age')}\n"
        f"âœ… Ù…Ø±Ø§Øª Ø§Ù„ØªØ¹Ø¨Ø¦Ø©: {u.get('successful_topups')}\n"
        f"ðŸ’³ Ø§Ù„Ù…Ø¨Ù„Øº: {amount:,} Ù„.Ø³\n"
        f"UserID: {user_id}\n"
        f"Username: @{username or 'â€”'}"
    )
    try:
        chat_id = int(ADMIN_CHAT_ID)
    except Exception:
        chat_id = ADMIN_CHAT_ID
    try:
        bot.send_message(chat_id, text)
    except Exception:
        pass

@bot.message_handler(commands=["start"])
def on_start(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]

    if u.get("full_name") and u.get("age"):
        u["state"] = S_MAIN_MENU
        save_data()
        bot.send_message(chat_id, "Ù…Ø±Ø­Ø¨Ø§ Ù…Ø¬Ø¯Ø¯Ù‹Ø§!", reply_markup=kb_main())
    else:
        bot.send_message(chat_id, "Ù‡Ù„ Ø£Ù†Øª Ù…Ø³Ø¬Ù„ Ø­Ø³Ø§Ø¨ Ù„Ø¯ÙŠÙ†Ø§ ÙÙŠ Ø§Ù„ÙƒØ§Ø´ÙŠØ±Ø§ØŸ", reply_markup=kb_yes_no())
        u["state"] = S_IDLE
        save_data()

@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]
    text = (msg.text or "").strip()

    if text in ["â¬…ï¸ Ø±Ø¬ÙˆØ¹", "Ø±Ø¬ÙˆØ¹", "Ø¹ÙˆØ¯Ø©"] and u.get("state") in [S_WAIT_NAME, S_WAIT_AGE]:
        bot.send_message(chat_id, "Ù„Ø§ ÙŠÙ…ÙƒÙ†Ùƒ Ø§Ù„Ø±Ø¬ÙˆØ¹ Ø§Ù„Ø¢Ù†. Ø£ÙƒÙ…ÙÙ„ Ø¨ÙŠØ§Ù†Ø§ØªÙƒ Ø£ÙˆÙ„Ø§Ù‹.")
        return

    if text in ["â¬…ï¸ Ø±Ø¬ÙˆØ¹", "Ø±Ø¬ÙˆØ¹", "Ø¹ÙˆØ¯Ø©"]:
        u["state"] = S_MAIN_MENU
        u["pending"] = {}
        save_data()
        bot.send_message(chat_id, "ØªÙ… Ø§Ù„Ø±Ø¬ÙˆØ¹ Ù„Ù„Ù‚Ø§Ø¦Ù…Ø© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©.", reply_markup=kb_main())
        return

    if text in ["Ù†Ø¹Ù…", "Ù„Ø§"] and u.get("state") == S_IDLE:
        if text == "Ù„Ø§":
            bot.send_message(chat_id, "Ø§Ù„Ø±Ø¬Ø§Ø¡ Ø§Ù„ØªÙˆØ§ØµÙ„ Ù…Ø¹ Ø§Ù„Ø¯Ø¹Ù… Ù„Ø¥Ù†Ø´Ø§Ø¡ Ø­Ø³Ø§Ø¨ Ù„Ùƒ:\n@MAA2857", reply_markup=ReplyKeyboardRemove())
            u["state"] = S_IDLE
            save_data()
            return
        else:
            if u.get("full_name") and u.get("age"):
                u["state"] = S_MAIN_MENU
                save_data()
                bot.send_message(chat_id, "Ù…Ø±Ø­Ø¨Ø§ Ù…Ø¬Ø¯Ø¯Ù‹Ø§!", reply_markup=kb_main())
                return
            else:
                u["state"] = S_WAIT_NAME
                save_data()
                bot.send_message(chat_id, "Ø§Ø¯Ø®Ù„ Ù…Ø¹Ù„ÙˆÙ…Ø§Øª Ø­Ø³Ø§Ø¨Ùƒ\nØ§Ù„Ø§Ø³Ù… Ø§Ù„Ø«Ù„Ø§Ø«ÙŠ:", reply_markup=ReplyKeyboardRemove())
                return

    state = u.get("state", S_IDLE)

    if state == S_WAIT_NAME:
        if is_valid_full_name(text):
            u["full_name"] = text
            u["state"] = S_WAIT_AGE
            save_data()
            bot.send_message(chat_id, "Ø¬ÙŠØ¯ âœ…\nØ§Ù„Ø¢Ù† Ø£Ø¯Ø®Ù„ Ø§Ù„Ø¹Ù…Ø± (10-100):", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "âŒ Ø§Ù„Ø§Ø³Ù… ØºÙŠØ± ØµØ§Ù„Ø­. Ø£Ø¯Ø®Ù„ Ø§Ø³Ù… Ø«Ù„Ø§Ø«ÙŠ ØµØ­ÙŠØ­.", reply_markup=kb_back())
        return

    if state == S_WAIT_AGE:
        if is_valid_age(text):
            u["age"] = int(text)
            u["state"] = S_MAIN_MENU
            save_data()
            bot.send_message(chat_id, "ØªÙ… Ø­ÙØ¸ Ø¨ÙŠØ§Ù†Ø§ØªÙƒ âœ…", reply_markup=kb_main())
        else:
            bot.send_message(chat_id, "âŒ Ø§Ù„Ø¹Ù…Ø± ØºÙŠØ± ØµØ§Ù„Ø­. Ø£Ø¯Ø®Ù„ Ø±Ù‚Ù… Ø¨ÙŠÙ† 10 Ùˆ100.", reply_markup=kb_back())
        return

    if state == S_MAIN_MENU:
        if text == "ðŸ’° ØªØ¹Ø¨Ø¦Ø© Ø§Ù„Ø­Ø³Ø§Ø¨":
            u["state"] = S_TOPUP_METHOD
            save_data()
            bot.send_message(chat_id, "Ø§Ø®ØªØ± Ø·Ø±ÙŠÙ‚Ø© Ø§Ù„ØªØ¹Ø¨Ø¦Ø©:", reply_markup=kb_only_syriatel())
        elif text == "ðŸ“„ Ù…Ù„ÙÙŠ Ø§Ù„Ø´Ø®ØµÙŠ":
            bot.send_message(chat_id, f"ðŸ‘¤ Ø§Ù„Ø§Ø³Ù…: {u['full_name']}\nðŸŽ‚ Ø§Ù„Ø¹Ù…Ø±: {u['age']}\nâœ… Ù…Ø±Ø§Øª Ø§Ù„ØªØ¹Ø¨Ø¦Ø©: {u['successful_topups']}", reply_markup=kb_main())
        elif text == "ðŸ†˜ Ù…Ø³Ø§Ø¹Ø¯Ø©":
            bot.send_message(chat_id, "ØªÙˆØ§ØµÙ„ Ù…Ø¹Ù†Ø§ Ø¥Ø°Ø§ ÙƒÙ†Øª ØªÙˆØ§Ø¬Ù‡ Ø£ÙŠ Ù…Ø´ÙƒÙ„Ø©:\n@MAA2857", reply_markup=kb_main())
        else:
            bot.send_message(chat_id, "Ø§Ø®ØªØ± Ù…Ù† Ø§Ù„Ø£Ø²Ø±Ø§Ø±:", reply_markup=kb_main())
        return

    if state == S_TOPUP_METHOD:
        if text == "Ø³ÙŠØ±ÙŠØªÙŠÙ„ ÙƒØ§Ø´":
            u["pending"] = {"method": "syriatel_cash", "amount": 0}
            u["state"] = S_WAIT_AMOUNT
            save_data()
            bot.send_message(chat_id, f"Ø£Ø¯Ø®Ù„ Ù‚ÙŠÙ…Ø© Ø§Ù„ØªØ¹Ø¨Ø¦Ø© (10000 Ø­ØªÙ‰ 1000000 ÙˆØ¨Ù…Ø¶Ø§Ø¹ÙØ§Øª 5000):", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "Ø§Ø®ØªØ± 'Ø³ÙŠØ±ÙŠØªÙŠÙ„ ÙƒØ§Ø´'.", reply_markup=kb_only_syriatel())
        return

    if state == S_WAIT_AMOUNT:
        if is_valid_amount(text):
            amount = int(text)
            u["pending"]["amount"] = amount
            u["state"] = S_WAIT_CONFIRM_SENT
            save_data()
            bot.send_message(chat_id, f"Ø­ÙˆÙ‘Ù„ Ø§Ù„Ù…Ø¨Ù„Øº Ø¥Ù„Ù‰ Ø§Ù„Ø±Ù‚Ù…: {PAYMENT_NUMBER}\nØ§Ø³ØªØ®Ø¯Ù… Ø§Ù„ÙƒÙˆØ¯: {PAYMENT_CODE}\nØ¨Ø¹Ø¯ Ø§Ù„ØªØ­ÙˆÙŠÙ„ Ø§Ø¶ØºØ· âœ… ØªÙ…", reply_markup=kb_done_back())
        else:
            bot.send_message(chat_id, "âŒ Ø§Ù„Ù…Ø¨Ù„Øº ØºÙŠØ± ØµØ­ÙŠØ­.", reply_markup=kb_back())
        return

    if state == S_WAIT_CONFIRM_SENT:
        if text == "âœ… ØªÙ…":
            u["state"] = S_WAIT_TRANSFER_CODE
            save_data()
            bot.send_message(chat_id, "Ø£Ø¯Ø®Ù„ Ø±Ù…Ø² Ø¹Ù…Ù„ÙŠØ© Ø§Ù„ØªØ­ÙˆÙŠÙ„:", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "Ø§Ø¶ØºØ· âœ… ØªÙ… Ø¨Ø¹Ø¯ Ø§Ù„ØªØ­ÙˆÙŠÙ„.", reply_markup=kb_done_back())
        return

    if state == S_WAIT_TRANSFER_CODE:
        code = text.strip()
        try:
            amount = int(u.get("pending", {}).get("amount", 0))
        except Exception:
            amount = 0

        ok, _sms = match_sms_with(code, amount)

        if ok:
            u["successful_topups"] = int(u.get("successful_topups", 0)) + 1
            u["state"] = S_MAIN_MENU
            u["pending"] = {}
            save_data()

            bot.send_message(chat_id, "âœ… ØªÙ…Øª Ø§Ù„Ø¹Ù…Ù„ÙŠØ© Ø¨Ù†Ø¬Ø§Ø­.\nØ³ÙŠØªÙ… ØªØ¹Ø¨Ø¦Ø© Ø­Ø³Ø§Ø¨Ùƒ Ø®Ù„Ø§Ù„ Ø±Ø¨Ø¹ Ø³Ø§Ø¹Ø©.", reply_markup=kb_main())
            send_admin_notification(uid, msg.from_user.username, u, amount)
        else:
            bot.send_message(chat_id, "âŒ Ø§Ù„Ø±Ù…Ø² ØºÙŠØ± ØµØ­ÙŠØ­ Ø£Ùˆ Ù„Ø§ ÙŠÙˆØ¬Ø¯ SMS Ù…Ø·Ø§Ø¨Ù‚ Ø®Ù„Ø§Ù„ Ø¢Ø®Ø± 5 Ø¯Ù‚Ø§Ø¦Ù‚.", reply_markup=kb_back())
        return

@app.route("/sms", methods=["POST"])
def sms_webhook():
    try:
        data = request.get_json(silent=True) or {}
        message = data.get("message", "")
        sender = data.get("sender", "")
        print(f"[SMS Received] Sender: {sender}, Message: {message}")
        add_incoming_sms(message, sender)
        return jsonify({"status": "received"}), 200
    except Exception as e:
        print("Error in /sms:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json(force=True)
        bot.process_new_updates([telebot.types.Update.de_json(update)])
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print("Error in /webhook:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    return "Server is running âœ…", 200

if __name__ == "__main__":
    load_data()
    try:
        bot.remove_webhook()
        if APP_URL:
            bot.set_webhook(url=f"{APP_URL}/webhook")
    except Exception as e:
        print("ØªØ­Ø°ÙŠØ±: ÙØ´Ù„ Ø¥Ø¹Ø¯Ø§Ø¯ Webhook Ù„ØªÙŠÙ„ÙŠØ¬Ø±Ø§Ù…:", e)
    app.run(host="0.0.0.0", port=PORT)
