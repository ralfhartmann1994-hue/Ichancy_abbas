import os
import time
import json
import re
import logging
from collections import deque
from flask import Flask, request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ================= إعداد logging =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= إعدادات البيئة =================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("ضع TELEGRAM_TOKEN في Render")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "0933000000")
PAYMENT_CODE = os.environ.get("PAYMENT_CODE", "7788297")
APP_URL = os.environ.get("APP_URL")  # مثال: https://ichancy-abbas.onrender.com
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

# ================= تحميل/حفظ =================
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

# ================= لوحات المفاتيح =================
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

# ================= تحقق المدخلات =================
def is_valid_full_name(name: str) -> bool:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return len(parts) >= 3

def is_valid_age(text: str) -> bool:
    return text.isdigit() and 10 <= int(text) <= 100

def is_valid_amount(text: str) -> bool:
    return text.isdigit() and 10000 <= int(text) <= 1000000 and int(text) % 5000 == 0

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
    pattern = r"تم\s+استلام\s+مبلغ\s+(\d+)\s*ل\.س.*?رقم\s+العملية\s+هو\s+(\d+)"
    for sms in list(incoming_sms):
        m = re.search(pattern, sms["message"], re.IGNORECASE)
        if not m:
            continue
        amount_str, op_code = m.group(1), m.group(2)
        if amount_str == str(amount) and op_code == str(code):
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
        "📥 <b>تمت تعبئة الحساب بنجاح</b>\n\n"
        f"👤 الاسم: {u.get('full_name')}\n"
        f"🎂 العمر: {u.get('age')}\n"
        f"✅ مرات التعبئة: {u.get('successful_topups')}\n"
        f"💳 المبلغ: {amount:,} ل.س\n"
        f"UserID: {user_id}\n"
        f"Username: @{username or '—'}"
    )
    try:
        chat_id = int(ADMIN_CHAT_ID)
    except Exception:
        chat_id = ADMIN_CHAT_ID
    try:
        bot.send_message(chat_id, text)
    except Exception:
        pass

# ================= أوامر البوت =================
@bot.message_handler(commands=["start"])
def on_start(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]
    
    if u.get("full_name") and u.get("age"):
        u["state"] = S_MAIN_MENU
        save_data()
        bot.send_message(chat_id, "مرحبا مجددًا!", reply_markup=kb_main())
    else:
        bot.send_message(chat_id, "هل أنت مسجل حساب لدينا في الكاشيرا؟", reply_markup=kb_yes_no())
        u["state"] = S_IDLE
        save_data()

@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]
    text = (msg.text or "").strip()

    # منع الرجوع قبل تعبئة البيانات
    if text in ["⬅️ رجوع", "رجوع", "عودة"] and u.get("state") in [S_WAIT_NAME, S_WAIT_AGE]:
        bot.send_message(chat_id, "لا يمكنك الرجوع الآن. أكمِل بياناتك أولاً.")
        return

    # زر الرجوع في باقي الحالات
    if text in ["⬅️ رجوع", "رجوع", "عودة"]:
        u["state"] = S_MAIN_MENU
        u["pending"] = {}
        save_data()
        bot.send_message(chat_id, "تم الرجوع للقائمة الرئيسية.", reply_markup=kb_main())
        return

    # سؤال نعم/لا عند البداية
    if text in ["نعم", "لا"] and u.get("state") == S_IDLE:
        if text == "لا":
            bot.send_message(chat_id, "الرجاء التواصل مع الدعم لإنشاء حساب لك:\n@MAA2857", reply_markup=ReplyKeyboardRemove())
            u["state"] = S_IDLE
            save_data()
            return
        else:
            if u.get("full_name") and u.get("age"):
                u["state"] = S_MAIN_MENU
                save_data()
                bot.send_message(chat_id, "مرحبا مجددًا!", reply_markup=kb_main())
                return
            else:
                u["state"] = S_WAIT_NAME
                save_data()
                bot.send_message(chat_id, "ادخل معلومات حسابك\nالاسم الثلاثي:", reply_markup=ReplyKeyboardRemove())
                return

    state = u.get("state", S_IDLE)

    # الاسم
    if state == S_WAIT_NAME:
        if is_valid_full_name(text):
            u["full_name"] = text
            u["state"] = S_WAIT_AGE
            save_data()
            bot.send_message(chat_id, "جيد ✅\nالآن أدخل العمر (10-100):", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "❌ الاسم غير صالح. أدخل اسم ثلاثي صحيح.", reply_markup=kb_back())
        return

    # العمر
    if state == S_WAIT_AGE:
        if is_valid_age(text):
            u["age"] = int(text)
            u["state"] = S_MAIN_MENU
            save_data()
            bot.send_message(chat_id, "تم حفظ بياناتك ✅", reply_markup=kb_main())
        else:
            bot.send_message(chat_id, "❌ العمر غير صالح. أدخل رقم بين 10 و100.", reply_markup=kb_back())
        return

    # القائمة الرئيسية
    if state == S_MAIN_MENU:
        if text == "💰 تعبئة الحساب":
            u["state"] = S_TOPUP_METHOD
            save_data()
            bot.send_message(chat_id, "اختر طريقة التعبئة:", reply_markup=kb_only_syriatel())
        elif text == "📄 ملفي الشخصي":
            bot.send_message(chat_id, f"👤 الاسم: {u['full_name']}\n🎂 العمر: {u['age']}\n✅ مرات التعبئة: {u['successful_topups']}", reply_markup=kb_main())
        elif text == "🆘 مساعدة":
            bot.send_message(chat_id, "تواصل معنا إذا كنت تواجه أي مشكلة:\n@MAA2857", reply_markup=kb_main())
        else:
            bot.send_message(chat_id, "اختر من الأزرار:", reply_markup=kb_main())
        return

    # اختيار طريقة التعبئة
    if state == S_TOPUP_METHOD:
        if text == "سيريتيل كاش":
            u["pending"] = {"method": "syriatel_cash", "amount": 0}
            u["state"] = S_WAIT_AMOUNT
            save_data()
            bot.send_message(chat_id, f"أدخل قيمة التعبئة (10000 حتى 1000000 وبمضاعفات 5000):", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "اختر 'سيريتيل كاش'.", reply_markup=kb_only_syriatel())
        return

    # إدخال المبلغ
    if state == S_WAIT_AMOUNT:
        if is_valid_amount(text):
            amount = int(text)
            u["pending"]["amount"] = amount
            u["state"] = S_WAIT_CONFIRM_SENT
            save_data()
            bot.send_message(chat_id, f"حوّل المبلغ إلى الرقم: {PAYMENT_NUMBER}\nاستخدم الكود: {PAYMENT_CODE}\nبعد التحويل اضغط ✅ تم", reply_markup=kb_done_back())
        else:
            bot.send_message(chat_id, "❌ المبلغ غير صحيح.", reply_markup=kb_back())
        return

    # تأكيد الإرسال
    if state == S_WAIT_CONFIRM_SENT:
        if text == "✅ تم":
            u["state"] = S_WAIT_TRANSFER_CODE
            save_data()
            bot.send_message(chat_id, "أدخل رمز عملية التحويل:", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "اضغط ✅ تم بعد التحويل.", reply_markup=kb_done_back())
        return

    # إدخال رمز العملية + المطابقة
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

            bot.send_message(chat_id, "✅ تمت العملية بنجاح.\nسيتم تعبئة حسابك خلال ربع ساعة.", reply_markup=kb_main())
            send_admin_notification(uid, msg.from_user.username, u, amount)
        else:
            bot.send_message(chat_id, "❌ الرمز غير صحيح أو لا يوجد SMS مطابق خلال آخر 5 دقائق.", reply_markup=kb_back())
        return

# ================= SMS Gateway =================
@app.route("/sms", methods=["POST"])
def sms_webhook():
    try:
        raw_data = request.data.decode("utf-8", errors="ignore")
        logger.info("📩 RAW JSON: %s", raw_data, flush=True)

        data = request.get_json(force=True)  # force=True لضمان قراءة JSON حتى لو لم يكن Content-Type مضبوط
        logger.info("📩 JSON Parsed: %s", data, flush=True)

        message = data.get("message", "")
        sender = data.get("sender", "")
        logger.info("📩 Extracted -> sender: %s, message: %s", sender, message, flush=True)

        add_incoming_sms(message, sender)
        return jsonify({"status": "received"}), 200
    except Exception as e:
        logger.error("❌ Error in /sms: %s", e, flush=True)
        return jsonify({"error": str(e)}), 500
        
# ================= Telegram Webhook Endpoint =================
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json(force=True)
        bot.process_new_updates([telebot.types.Update.de_json(update)])
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error("Error in /webhook: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    return "Server is running ✅", 200

if __name__ == "__main__":
    load_data()
    try:
        bot.remove_webhook()
        if APP_URL:
            bot.set_webhook(url=f"{APP_URL}/webhook")
    except Exception as e:
        logger.warning("تحذير: فشل إعداد Webhook لتيليجرام: %s", e)
    app.run(host="0.0.0.0", port=PORT)
