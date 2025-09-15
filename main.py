import os
import time
import json
import re
import logging
import threading
from collections import deque
from flask import Flask, request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ------------------ إعداد logging ------------------
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ------------------ إعداد البيئة ------------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("ضع TELEGRAM_TOKEN في Render")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
ADMIN_PROF = os.environ.get("ADMIN_PROF", "admin")
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "0933000000")
PAYMENT_CODE = os.environ.get("PAYMENT_CODE", "7788297")
APP_URL = os.environ.get("APP_URL")
PORT = int(os.environ.get("PORT", 10000))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

DATA_FILE = "users_data.json"
users = {}
data_lock = threading.Lock()

incoming_sms = deque(maxlen=200)
SMS_CACHE_SECONDS = 5 * 60

# حالات المستخدم
(
    S_IDLE,
    S_WAIT_NAME,
    S_WAIT_AGE,
    S_MAIN_MENU,
    S_TOPUP_METHOD,
    S_WAIT_AMOUNT,
    S_WAIT_CONFIRM_SENT,
    S_WAIT_TRANSFER_CODE,
    S_NO_ACCOUNT,
) = range(9)

# ================= تحميل/حفظ =================
def load_data():
    global users
    with data_lock:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    users = json.load(f)
                    if not isinstance(users, dict):
                        logger.warning("ملف البيانات لا يحتوي على dict — إعادة تهيئة")
                        users = {}
            except Exception as e:
                logger.exception("فشل تحميل ملف البيانات: %s", e)
                users = {}
        else:
            users = {}

def save_data():
    try:
        with data_lock:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception("فشل حفظ ملف البيانات: %s", e)

# ================= متسّخدمين =================
def ensure_user(uid: int):
    key = str(uid)
    with data_lock:
        if key not in users:
            users[key] = {
                "state": S_IDLE,
                "full_name": None,
                "age": None,
                "successful_topups": 0,
                "pending": {},
            }

# ------------------ لوحات المفاتيح ------------------
def kb_main():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("💰 تعبئة الحساب"))
    kb.add(KeyboardButton("📄 ملفي الشخصي"))
    kb.add(KeyboardButton("🆘 مساعدة"))
    return kb

def kb_yes_no():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("نعم"), KeyboardButton("لا"))
    return kb

def kb_back():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("⬅️ رجوع"))
    return kb

def kb_done_back():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("✅ تم"))
    kb.add(KeyboardButton("⬅️ رجوع"))
    return kb

def kb_only_syriatel():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("سيريتيل كاش"))
    kb.add(KeyboardButton("⬅️ رجوع"))
    return kb

# ------------------ تحقق المدخلات ------------------
def is_valid_full_name(name: str) -> bool:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    return len(parts) >= 3

def is_valid_age(text: str) -> bool:
    return (text or "").isdigit() and 10 <= int(text) <= 100

def is_valid_amount(text: str) -> bool:
    return (text or "").isdigit() and 10000 <= int(text) <= 1000000 and int(text) % 5000 == 0

# ================= أدوات SMS Cache =================
def clean_old_sms():
    now = time.time()
    while incoming_sms and (now - incoming_sms[0]["timestamp"] > SMS_CACHE_SECONDS):
        incoming_sms.popleft()

def add_incoming_sms(message: str, sender: str):
    clean_old_sms()
    incoming_sms.append({"message": message or "", "sender": sender or "", "timestamp": time.time()})

def match_sms_with(code: str, amount: int):
    clean_old_sms()
    pattern = r"تم\s+استلام\s+مبلغ\s+([0-9,]+)\s*ل\.س.*?رقم\s+العملية\s+هو\s+([0-9]+)"
    for sms in list(incoming_sms):
        m = re.search(pattern, sms["message"], re.IGNORECASE)
        if not m:
            continue
        amount_str = m.group(1).replace(',', '')
        op_code = m.group(2)
        if amount_str == str(amount) and op_code == str(code):
            try:
                incoming_sms.remove(sms)
            except ValueError:
                pass
            return True, sms
    return False, None

# ------------------ إشعارات للأدمن ------------------
def send_admin_notification(user_id, username, u, amount):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "📥 <b>تمت تعبئة الحساب بنجاح</b>\n\n"
        f"👤 الاسم: {u.get('full_name')}\n"
        f"🎂 العمر: {u.get('age')}\n"
        f"✅ مرات التعبئة: {u.get('successful_topups')}\n"
        f"💳 المبلغ: {amount:,} ل.س\n"
        f"UserID: {user_id}\n"
        f"Username: @{username or '—'}"
    )
    try:
        try:
            chat_id = int(ADMIN_CHAT_ID)
        except Exception:
            chat_id = ADMIN_CHAT_ID
        bot.send_message(chat_id, text)
    except Exception:
        logger.exception("فشل إرسال إشعار للأدمن")

# ------------------ مساعدة لإرسال رسالة مؤجلة ------------------
def send_delayed_message(chat_id, text, reply_markup=None, delay=1.2):
    def _send():
        try:
            bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            logger.exception("فشل إرسال رسالة مؤجلة إلى %s", chat_id)

    t = threading.Timer(delay, _send)
    t.daemon = True
    t.start()

# ------------------ أوامر البوت ------------------
WELCOME_FIRST = (
    "عرض من إشانسي بوت للزبائن الكرام 💥\n"
    "نقدم لكم عروض على السحب \n"
    "ستكون نسبة السحب من البوت هي 0٪ ⚡\n"
    "وعرض على الايداع ⬇️"
)

# ================= نقطة البداية =================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    ensure_user(uid)
    u = users[str(uid)]
    bot.send_message(uid, WELCOME_FIRST)
    send_delayed_message(uid, "هل أنت مسجل لدينا في الكاشيرا؟", reply_markup=kb_yes_no())
    u["state"] = S_IDLE
    save_data()

# ================= التعامل مع الرسائل =================
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message):
    uid = message.from_user.id
    text = message.text.strip()
    ensure_user(uid)
    u = users[str(uid)]
    state = u.get("state", S_IDLE)

    if text == "⬅️ رجوع":
        bot.send_message(uid, "هل أنت مسجل لدينا في الكاشيرا؟", reply_markup=kb_yes_no())
        u["state"] = S_IDLE
        save_data()
        return

    if state == S_IDLE:
        if text == "نعم":
            bot.send_message(uid, "من فضلك أدخل اسمك الثلاثي:")
            u["state"] = S_WAIT_NAME
        elif text == "لا":
            bot.send_message(uid, f"تواصل معنا لإنشاء حساب ايشانسي لك ثم أعد المحاولة. @{ADMIN_PROF}", reply_markup=kb_back())
            u["state"] = S_NO_ACCOUNT
        save_data()
        return

    if state == S_WAIT_NAME:
        if is_valid_full_name(text):
            u["full_name"] = text
            bot.send_message(uid, "الرجاء إدخال عمرك:")
            u["state"] = S_WAIT_AGE
        else:
            bot.send_message(uid, "❌ الاسم غير صالح، الرجاء إدخال اسمك الثلاثي الكامل.")
        save_data()
        return

    if state == S_WAIT_AGE:
        if is_valid_age(text):
            u["age"] = int(text)
            bot.send_message(uid, "✅ تم تسجيلك بنجاح", reply_markup=kb_main())
            u["state"] = S_MAIN_MENU
        else:
            bot.send_message(uid, "❌ العمر غير صالح. الرجاء إدخال رقم بين 10 و100.")
        save_data()
        return

    if state == S_MAIN_MENU:
        if text == "💰 تعبئة الحساب":
            bot.send_message(uid, "اختر طريقة التعبئة:", reply_markup=kb_only_syriatel())
            u["state"] = S_TOPUP_METHOD
        elif text == "📄 ملفي الشخصي":
            bot.send_message(uid, f"👤 {u.get('full_name')}\n🎂 {u.get('age')}\n✅ تعبئات ناجحة: {u.get('successful_topups')}")
        elif text == "🆘 مساعدة":
            bot.send_message(uid, "للمساعدة تواصل مع الأدمن: @" + ADMIN_PROF)
        save_data()
        return

    if state == S_TOPUP_METHOD:
        if text == "سيريتيل كاش":
            bot.send_message(uid, "أدخل المبلغ المراد تعبئته (بين 10000 و 1000000 ل.س، مضاعف 5000):", reply_markup=kb_back())
            u["state"] = S_WAIT_AMOUNT
        save_data()
        return

    if state == S_WAIT_AMOUNT:
        if is_valid_amount(text):
            amount = int(text)
            u["pending"]["amount"] = amount
            bot.send_message(uid, f"قم بتحويل {amount:,} ل.س إلى الرقم {PAYMENT_NUMBER} ثم اضغط ✅ تم", reply_markup=kb_done_back())
            u["state"] = S_WAIT_CONFIRM_SENT
        else:
            bot.send_message(uid, "❌ المبلغ غير صالح. أدخل رقم صحيح بين 10000 و 1000000 ل.س (مضاعف 5000).")
        save_data()
        return

    if state == S_WAIT_CONFIRM_SENT:
        if text == "✅ تم":
            bot.send_message(uid, "الرجاء إدخال رقم العملية (الكود):", reply_markup=kb_back())
            u["state"] = S_WAIT_TRANSFER_CODE
        save_data()
        return

    if state == S_WAIT_TRANSFER_CODE:
        if text.isdigit():
            code = text
            amount = u["pending"].get("amount")
            ok, sms = match_sms_with(code, amount)
            if ok:
                u["successful_topups"] += 1
                u["pending"] = {}
                bot.send_message(uid, f"✅ تم تأكيد تعبئة {amount:,} ل.س بنجاح", reply_markup=kb_main())
                u["state"] = S_MAIN_MENU
                send_admin_notification(uid, message.from_user.username, u, amount)
            else:
                bot.send_message(uid, "❌ لم يتم العثور على عملية بهذا الكود. تحقق من الرسائل.")
        save_data()
        return

# ================= Flask Webhook =================
@app.route("/" + TOKEN, methods=["POST"])
def webhook():
    update = request.stream.read().decode("utf-8")
    bot.process_new_updates([telebot.types.Update.de_json(update)])
    return "!", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

@app.route("/sms", methods=["POST"])
def sms_hook():
    data = request.get_json(force=True)
    msg = data.get("message")
    sender = data.get("sender")
    if msg:
        add_incoming_sms(msg, sender)
        logger.info("تم استقبال SMS من %s: %s", sender, msg)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "no message"}), 400

if __name__ == "__main__":
    load_data()
    if APP_URL:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
    app.run(host="0.0.0.0", port=PORT) 
