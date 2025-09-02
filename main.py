import os, time, json, re, threading
from collections import deque
from flask import Flask, request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ================= إعدادات البيئة =================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("ضع TELEGRAM_TOKEN في Render")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "0933000000")
PAYMENT_CODE = os.environ.get("PAYMENT_CODE", "7788297")
APP_URL = os.environ.get("APP_URL")
PORT = int(os.environ.get("PORT", 10000))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

DATA_FILE = "users_data.json"
users = {}
incoming_sms = deque(maxlen=200)  # كل عنصر: {"message":..., "sender":..., "timestamp":...}

# ================= حالات المستخدم =================
S_IDLE, S_WAIT_NAME, S_WAIT_AGE, S_MAIN_MENU, S_TOPUP_METHOD, S_WAIT_AMOUNT, S_WAIT_CONFIRM_SENT, S_WAIT_TRANSFER_CODE = range(8)

# ================= تحميل/حفظ =================
def load_data():
    global users
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                users = json.load(f)
        except:
            users = {}
    else:
        users = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except:
        pass

def ensure_user(uid):
    if str(uid) not in users:
        users[str(uid)] = {
            "state": S_IDLE,
            "full_name": None,
            "age": None,
            "successful_topups": 0,
            "pending": {}
        }

# ================= لوحات المفاتيح =================
def kb_main():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("💰 تعبئة الحساب"))
    kb.add(KeyboardButton("📄 ملفي الشخصي"))
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

# ================= تنظيف الرسائل القديمة =================
def clean_old_sms():
    now = time.time()
    while incoming_sms and now - incoming_sms[0]["timestamp"] > 300:  # 5 دقائق
        incoming_sms.popleft()

# ================= معالجة الرسائل =================
def match_sms_with(code, amount):
    clean_old_sms()
    for sms in incoming_sms:
        pattern = r"تم استلام مبلغ\s+(\d+)\s*ل\.س.*?رقم العملية هو\s+(\d+)"
        m = re.search(pattern, sms["message"])
        if m and m.group(1) == str(amount) and m.group(2) == str(code):
            incoming_sms.remove(sms)  # حذف الرسالة بعد المطابقة
            return True, sms
    return False, None

def send_admin_notification(user_id, username, u, amount):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "📥 <b>تمت تعبئة الحساب بنجاح</b>\n\n"
        f"👤 الاسم: {u['full_name']}\n"
        f"🎂 العمر: {u['age']}\n"
        f"✅ مرات التعبئة: {u['successful_topups']}\n"
        f"💳 المبلغ: {amount:,} ل.س\n"
        f"UserID: {user_id}\n"
        f"Username: @{username or '—'}"
    )
    try:
        bot.send_message(int(ADMIN_CHAT_ID), text)
    except:
        pass

# ================= أوامر البوت =================
@bot.message_handler(commands=["start"])
def on_start(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]
    if u["full_name"] and u["age"]:
        bot.send_message(chat_id, "اهلا بكم في بوت abbas كاشيرا 😎\nلسنا الوحيدين لكننا الأفضل 😎❤️", reply_markup=kb_main())
        u["state"] = S_MAIN_MENU
        save_data()
    else:
        bot.send_message(chat_id, "اهلا بكم في بوت abbas كاشيرا 😎\nلسنا الوحيدين لكننا الأفضل 😎❤️", reply_markup=ReplyKeyboardRemove())
        time.sleep(1)
        bot.send_message(chat_id, "ادخل معلومات حسابك\nالاسم الثلاثي:", reply_markup=kb_back())
        u["state"] = S_WAIT_NAME
        save_data()

@bot.message_handler(func=lambda m: m.text == "📄 ملفي الشخصي")
def profile_info(msg):
    uid = msg.from_user.id
    ensure_user(uid)
    u = users[str(uid)]
    if u["full_name"] and u["age"]:
        bot.send_message(msg.chat.id, f"👤 الاسم: {u['full_name']}\n🎂 العمر: {u['age']}\n✅ مرات التعبئة: {u['successful_topups']}", reply_markup=kb_main())
    else:
        bot.send_message(msg.chat.id, "لم تسجل بياناتك بعد.", reply_markup=kb_back())

@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]
    text = msg.text.strip()

    # زر رجوع
    if text in ["⬅️ رجوع", "رجوع", "عودة"]:
        u["state"] = S_MAIN_MENU
        u["pending"] = {}
        save_data()
        bot.send_message(chat_id, "تم الرجوع للقائمة الرئيسية.", reply_markup=kb_main())
        return

    state = u.get("state", S_IDLE)

    if state == S_WAIT_NAME:
        if is_valid_full_name(text):
            u["full_name"] = text
            u["state"] = S_WAIT_AGE
            save_data()
            bot.send_message(chat_id, "جيد ✅\nالآن أدخل العمر (10-100):", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "❌ الاسم غير صالح. أدخل اسم ثلاثي صحيح.", reply_markup=kb_back())
        return

    if state == S_WAIT_AGE:
        if is_valid_age(text):
            u["age"] = int(text)
            u["state"] = S_MAIN_MENU
            save_data()
            bot.send_message(chat_id, "تم حفظ بياناتك ✅", reply_markup=kb_main())
        else:
            bot.send_message(chat_id, "❌ العمر غير صالح. أدخل رقم بين 10 و100.", reply_markup=kb_back())
        return

    if state == S_MAIN_MENU:
        if text == "💰 تعبئة الحساب":
            u["state"] = S_TOPUP_METHOD
            save_data()
            bot.send_message(chat_id, "اختر طريقة التعبئة:", reply_markup=kb_only_syriatel())
        elif text == "📄 ملفي الشخصي":
            profile_info(msg)
        else:
            bot.send_message(chat_id, "اختر من الأزرار:", reply_markup=kb_main())
        return

    if state == S_TOPUP_METHOD:
        if text == "سيريتيل كاش":
            u["pending"] = {"method": "syriatel_cash", "amount": 0}
            u["state"] = S_WAIT_AMOUNT
            save_data()
            bot.send_message(chat_id, "أدخل قيمة التعبئة (10000 حتى 1000000
            وبمضاعفات 5000):", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "اختر 'سيريتيل كاش'.", reply_markup=kb_only_syriatel())
        return

    if state == S_WAIT_AMOUNT:
        if is_valid_amount(text):
            amount = int(text)
            u["pending"]["amount"] = amount
            u["state"] = S_WAIT_CONFIRM_SENT
            save_data()
            bot.send_message(
                chat_id,
                f"حوّل المبلغ إلى الرقم: {PAYMENT_NUMBER}\n"
                f"استخدم الكود: {PAYMENT_CODE}\n\n"
                "بعد التحويل اضغط ✅ تم",
                reply_markup=kb_done_back()
            )
        else:
            bot.send_message(chat_id, "❌ المبلغ غير صحيح.", reply_markup=kb_back())
        return

    if state == S_WAIT_CONFIRM_SENT:
        if text == "✅ تم":
            u["state"] = S_WAIT_TRANSFER_CODE
            save_data()
            bot.send_message(chat_id, "أدخل رمز عملية التحويل:", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "اضغط ✅ تم بعد التحويل.", reply_markup=kb_done_back())
        return

    if state == S_WAIT_TRANSFER_CODE:
        code = text.strip()
        amount = u["pending"].get("amount", 0)
        ok, sms = match_sms_with(code, amount)
        if ok:
            u["successful_topups"] += 1
            u["state"] = S_MAIN_MENU
            u["pending"] = {}
            save_data()
            bot.send_message(chat_id, "✅ تمت العملية بنجاح.\nسيتم تعبئة حسابك خلال ربع ساعة.", reply_markup=kb_main())
            send_admin_notification(uid, msg.from_user.username, u, amount)
        else:
            bot.send_message(chat_id, "❌ الرمز غير صحيح أو لا يوجد SMS مطابق.", reply_markup=kb_back())
        return

# ================= SMS Gateway =================
@app.route("/sms", methods=["POST"])
def sms_webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON received"}), 400

        message = data.get("message", "")
        sender = data.get("sender", "")

        # إضافة الرسالة إلى cache مؤقتة
        incoming_sms.append({"message": message, "sender": sender, "timestamp": time.time()})
        return jsonify({"status": "received"}), 200

    except Exception as e:
        print("Error in sms_webhook:", e)
        return jsonify({"error": str(e)}), 500

# ================= Telegram Webhook =================
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        print("Error in telegram_webhook:", e)
        return "Error", 500

# ================= الصفحة الرئيسية =================
@app.route("/", methods=["GET"])
def home():
    return "Server is running ✅", 200

# ================= تشغيل =================
if __name__ == "__main__":
    load_data()
    # إعداد Webhook للبوت
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/webhook")
    # تشغيل Flask
    app.run(host="0.0.0.0", port=PORT)
