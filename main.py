import os, time, json, threading, re
from collections import deque
from flask import Flask, request, abort
from flask import request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ================= إعدادات البيئة =================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("ضع TELEGRAM_TOKEN في Render")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # رقم ID حساب الأدمن
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "0933000000")
PAYMENT_CODE = os.environ.get("PAYMENT_CODE", "7788297")
SMS_SHARED_SECRET = os.environ.get("SMS_SHARED_SECRET", "changeme")

APP_URL = os.environ.get("APP_URL")  # رابط Render مع https
PORT = int(os.environ.get("PORT", "10000"))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

DATA_FILE = "users_data.json"
users = {}
incoming_sms = deque(maxlen=200)

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
    if not text.isdigit():
        return False
    age = int(text)
    return 10 <= age <= 100

def is_valid_amount(text: str) -> bool:
    if not text.isdigit():
        return False
    amount = int(text)
    return 10000 <= amount <= 1000000 and amount % 5000 == 0

# ================= أوامر البوت =================
@bot.message_handler(commands=["start"])
def on_start(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]

    # إذا عنده بيانات → مباشرة القائمة الرئيسية
    if u["full_name"] and u["age"]:
        bot.send_message(
            chat_id,
            "اهلا بكم في بوت abbas كاشيرا 😎\nلسنا الوحيدين لكننا الأفضل 😎❤️",
            reply_markup=kb_main()
        )
        u["state"] = S_MAIN_MENU
        save_data()
    else:
        # تسجيل جديد
        bot.send_message(
            chat_id,
            "اهلا بكم في بوت abbas كاشيرا 😎\nلسنا الوحيدين لكننا الأفضل 😎❤️",
            reply_markup=ReplyKeyboardRemove()
        )
        time.sleep(1.5)
        bot.send_message(chat_id, "ادخل معلومات حسابك\nالاسم الثلاثي:", reply_markup=kb_back())
        u["state"] = S_WAIT_NAME
        save_data()

@bot.message_handler(func=lambda m: m.text == "📄 ملفي الشخصي")
def profile_info(msg):
    uid = msg.from_user.id
    ensure_user(uid)
    u = users[str(uid)]
    if u["full_name"] and u["age"]:
        bot.send_message(
            msg.chat.id,
            f"👤 الاسم: {u['full_name']}\n🎂 العمر: {u['age']}\n✅ مرات التعبئة: {u['successful_topups']}",
            reply_markup=kb_main()
        )
    else:
        bot.send_message(msg.chat.id, "لم تسجل بياناتك بعد.", reply_markup=kb_back())

# ================= معالجة النصوص =================
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
            bot.send_message(chat_id, "أدخل قيمة التعبئة (10000 حتى 1000000 وبمضاعفات 5000):", reply_markup=kb_back())
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
        ok, _ = match_sms_with(code, amount)
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
        # استلام البيانات بصيغة JSON
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON received"}), 400

        message = data.get("message", "")
        sender = data.get("sender", "")

        # Regex للتحقق من صيغة الرسالة كما في كودك الأصلي
        pattern = r"تم استلام مبلغ\s+(\d+)\s*ل\.س.*?رقم العملية هو\s+(\d+)"
        match = re.search(pattern, message)

        if match:
            amount = match.group(1)
            operation_id = match.group(2)
            # إرسال إشعار للأدمن
            bot.send_message(
                ADMIN_CHAT_ID,
                f"📩 دفع جديد من {sender}\n"
                f"💰 المبلغ: {amount} ل.س\n"
                f"🔢 رقم العملية: {operation_id}"
            )
            return jsonify({"status": "processed"}), 200
        else:
            # إرسال رسالة للأدمن عن الرسائل غير المطابقة
            bot.send_message(
                ADMIN_CHAT_ID,
                f"📩 رسالة غير مطابقة: {message}"
            )
            return jsonify({"status": "ignored"}), 200

    except Exception as e:
        # أي خطأ نطبعه في لوج Render ونرد على httpSMS
        print("Error in sms_webhook:", e)
        return jsonify({"error": str(e)}), 500
# ================= إرسال إشعار للأدمن =================
def send_admin_notification(user_id, username, u, amount):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "📥 <b>طلب تعبئة جديد</b>\n\n"
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
        bot.send_message(ADMIN_CHAT_ID, text)

# ================= تشغيل =================
if __name__ == "__main__":
    load_data()
    # تعيين Webhook
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/")
    app.run(host="0.0.0.0", port=PORT)
