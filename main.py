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
PROMOTIONS_FILE = "promotions.txt"
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
    S_VIEW_PROMOTIONS,  # حالة جديدة لعرض العروض
) = range(10)

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

def load_promotions():
    """تحميل نص العروض من ملف خارجي"""
    try:
        if os.path.exists(PROMOTIONS_FILE):
            with open(PROMOTIONS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return content if content else "لا توجد عروض متاحة حاليًا 📭"
        else:
            # إنشاء ملف العروض الافتراضي إذا لم يكن موجوداً
            default_promo = (
                "عرض من إشانسي بوت للزبائن الكرام 💥\n"
                "نقدم لكم عروض على السحب \n"
                "ستكون نسبة السحب من البوت هي 0٪ ⚡\n"
                "وعرض على الايداع ⬇️\n"
                "نسبة 10  ٪ على المبالغ من ال100 الف وما فوق ❤️‍🔥\n"
                "يعني  كل 200 الف بتوصلك 220000 🔥\n"
                "هذه العروض مفتوحة حتى توفير عروض جديدة ⭐\n"
                "بالتوفيق للملوك 🫡💥"
            )
            with open(PROMOTIONS_FILE, "w", encoding="utf-8") as f:
                f.write(default_promo)
            return default_promo
    except Exception as e:
        logger.exception("فشل تحميل ملف العروض: %s", e)
        return "❌ خطأ في تحميل العروض، تواصل مع الدعم"

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
    """القائمة الرئيسية مع زر العروض الجديد"""
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("💰 تعبئة الحساب"))
    kb.add(KeyboardButton("🎁 العروض"), KeyboardButton("📄 ملفي الشخصي"))
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

# ------------------ تحديد الحالة الصحيحة للرجوع ------------------
def get_back_state(current_state):
    """تحديد الحالة الصحيحة عند الضغط على رجوع"""
    back_map = {
        S_NO_ACCOUNT: S_IDLE,
        S_TOPUP_METHOD: S_MAIN_MENU,
        S_WAIT_AMOUNT: S_TOPUP_METHOD,
        S_WAIT_CONFIRM_SENT: S_WAIT_AMOUNT,
        S_WAIT_TRANSFER_CODE: S_WAIT_CONFIRM_SENT,
        S_VIEW_PROMOTIONS: S_MAIN_MENU,
    }
    return back_map.get(current_state, S_MAIN_MENU)

# ------------------ أوامر البوت ------------------
WELCOME_FIRST = (
    "عرض من إشانسي بوت للزبائن الكرام 💥\n"
    "نقدم لكم عروض على السحب \n"
    "ستكون نسبة السحب من البوت هي 0٪ ⚡\n"
    "وعرض على الايداع ⬇️"
    "نسبة 10  ٪ على المبالغ من ال100 الف وما فوق ❤️‍🔥"
    "يعني  كل 200 الف بتوصلك 220000 🔥" 
    "هذه العروض مفتوحة حتى توفير عروض جديدة ⭐"
    "بالتوفيق للملوك 🫡💥"
)

# ================= نقطة البداية =================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    ensure_user(uid)
    u = users[str(uid)]

    # التحقق إذا عنده ملف شخصي سابق
    if u.get("full_name") and u.get("age"):
        bot.send_message(uid, "👋 مرحبًا مجددًا يا " + u["full_name"], reply_markup=kb_main())
        u["state"] = S_MAIN_MENU
        save_data()
        return

    # إذا جديد أو ناقص بياناته -> يبدأ من البداية
    bot.send_message(uid, WELCOME_FIRST)
    send_delayed_message(uid, "هل أنت مسجل لدينا في الكاشيرا؟", reply_markup=kb_yes_no())
    u["state"] = S_IDLE
    save_data()

# ================= التعامل مع الرسائل =================
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message):
    uid = message.from_user.id
    text = (message.text or "").strip()
    ensure_user(uid)
    u = users[str(uid)]
    state = u.get("state", S_IDLE)

    # ================= معالجة زر الرجوع =================
    if text == "⬅️ رجوع":
        back_state = get_back_state(state)
        
        if back_state == S_IDLE:
            bot.send_message(uid, "هل أنت مسجل لدينا في الكاشيرا؟", reply_markup=kb_yes_no())
            u["state"] = S_IDLE
        elif back_state == S_MAIN_MENU:
            bot.send_message(uid, "تم الرجوع للقائمة الرئيسية", reply_markup=kb_main())
            u["state"] = S_MAIN_MENU
            u["pending"] = {}  # مسح أي بيانات مؤقتة
        elif back_state == S_TOPUP_METHOD:
            bot.send_message(uid, "اختر طريقة التعبئة:", reply_markup=kb_only_syriatel())
            u["state"] = S_TOPUP_METHOD
        elif back_state == S_WAIT_AMOUNT:
            bot.send_message(uid, "أدخل المبلغ المراد تعبئته (بين 10000 و 1000000 ل.س، مضاعف 5000):", reply_markup=kb_back())
            u["state"] = S_WAIT_AMOUNT
        elif back_state == S_WAIT_CONFIRM_SENT:
            amount = u.get("pending", {}).get("amount", 0)
            bot.send_message(uid, f"قم بتحويل {amount:,} ل.س إلى الرقم {PAYMENT_NUMBER} ثم اضغط ✅ تم", reply_markup=kb_done_back())
            u["state"] = S_WAIT_CONFIRM_SENT
            
        save_data()
        return

    # ================= حالة البداية =================
    if state == S_IDLE:
        if text == "نعم":
            bot.send_message(uid, "من فضلك أدخل اسمك الثلاثي:")
            u["state"] = S_WAIT_NAME
        elif text == "لا":
            bot.send_message(uid, f"تواصل معنا لإنشاء حساب ايشانسي لك ثم أعد المحاولة. @{ADMIN_PROF}", reply_markup=kb_back())
            u["state"] = S_NO_ACCOUNT
        else:
            bot.send_message(uid, "الرجاء اختيار نعم أو لا", reply_markup=kb_yes_no())
        save_data()
        return

    # ================= حالة عدم وجود حساب =================
    if state == S_NO_ACCOUNT:
        # في هذه الحالة، أي نص غير "رجوع" سيعيد نفس الرسالة
        bot.send_message(uid, f"تواصل معنا لإنشاء حساب ايشانسي لك ثم أعد المحاولة. @{ADMIN_PROF}", reply_markup=kb_back())
        return

    # ================= انتظار الاسم =================
    if state == S_WAIT_NAME:
        if is_valid_full_name(text):
            u["full_name"] = text
            bot.send_message(uid, "الرجاء إدخال عمرك:")
            u["state"] = S_WAIT_AGE
        else:
            bot.send_message(uid, "❌ الاسم غير صالح، الرجاء إدخال اسمك الثلاثي الكامل.")
        save_data()
        return

    # ================= انتظار العمر =================
    if state == S_WAIT_AGE:
        if is_valid_age(text):
            u["age"] = int(text)
            bot.send_message(uid, "✅ تم تسجيلك بنجاح", reply_markup=kb_main())
            u["state"] = S_MAIN_MENU
        else:
            bot.send_message(uid, "❌ العمر غير صالح. الرجاء إدخال رقم بين 10 و100.")
        save_data()
        return

    # ================= القائمة الرئيسية =================
    if state == S_MAIN_MENU:
        if text == "💰 تعبئة الحساب":
            bot.send_message(uid, "اختر طريقة التعبئة:", reply_markup=kb_only_syriatel())
            u["state"] = S_TOPUP_METHOD
        elif text == "🎁 العروض":
            promo_text = load_promotions()
            bot.send_message(uid, promo_text, reply_markup=kb_back())
            u["state"] = S_VIEW_PROMOTIONS
        elif text == "📄 ملفي الشخصي":
            bot.send_message(uid, f"👤 {u.get('full_name')}\n🎂 {u.get('age')}\n✅ تعبئات ناجحة: {u.get('successful_topups')}")
        elif text == "🆘 مساعدة":
            bot.send_message(uid, "للمساعدة تواصل مع الأدمن: @" + ADMIN_PROF)
        else:
            bot.send_message(uid, "الرجاء اختيار من الأزرار المتاحة:", reply_markup=kb_main())
        save_data()
        return

    # ================= حالة عرض العروض =================
    if state == S_VIEW_PROMOTIONS:
        # في هذه الحالة، أي نص غير "رجوع" سيعيد عرض العروض
        promo_text = load_promotions()
        bot.send_message(uid, promo_text, reply_markup=kb_back())
        return

    # ================= اختيار طريقة التعبئة =================
    if state == S_TOPUP_METHOD:
        if text == "سيريتيل كاش":
            bot.send_message(uid, "أدخل المبلغ المراد تعبئته (بين 10000 و 1000000 ل.س، مضاعف 5000):", reply_markup=kb_back())
            u["state"] = S_WAIT_AMOUNT
        else:
            bot.send_message(uid, "الرجاء اختيار سيريتيل كاش:", reply_markup=kb_only_syriatel())
        save_data()
        return

    # ================= انتظار المبلغ =================
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

    # ================= انتظار تأكيد الإرسال =================
    if state == S_WAIT_CONFIRM_SENT:
        if text == "✅ تم":
            bot.send_message(uid, "الرجاء إدخال رقم العملية (رقم عملية التعبئة):", reply_markup=kb_back())
            u["state"] = S_WAIT_TRANSFER_CODE
        else:
            amount = u.get("pending", {}).get("amount", 0)
            bot.send_message(uid, f"الرجاء الضغط على ✅ تم بعد تحويل {amount:,} ل.س", reply_markup=kb_done_back())
        save_data()
        return

    # ================= انتظار رمز العملية =================
    if state == S_WAIT_TRANSFER_CODE:
        if text.isdigit():
            code = text
            amount = u["pending"].get("amount")
            ok, sms = match_sms_with(code, amount)
            if ok:
                u["successful_topups"] += 1
                u["pending"] = {}
                bot.send_message(uid, f"✅ تم تأكيد تعبئة {amount:,} ل.س بنجاح", reply_markup=kb_main())
                # الرسالة الإضافية
                bot.send_message(uid, "⏳ سيتم تعبئة حسابك الايشانسي خلال ربع ساعة كحد أقصى بسبب الضغط على البوت 🤖")
                u["state"] = S_MAIN_MENU
                send_admin_notification(uid, message.from_user.username, u, amount)
            else:
                bot.send_message(uid, "❌ لم يتم العثور على عملية بهذا الكود. تحقق من الرسائل.")
        else:
            bot.send_message(uid, "❌ الرجاء إدخال رقم العملية (أرقام فقط)")
        save_data()
        return

# ================= Flask Webhook =================
@app.route("/" + TOKEN, methods=["POST"])
def webhook():
    try:
        update = request.stream.read().decode("utf-8")
        bot.process_new_updates([telebot.types.Update.de_json(update)])
        return "!", 200
    except Exception as e:
        logger.exception("خطأ في webhook")
        return "Error", 500

@app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

@app.route("/sms", methods=["POST"])
def sms_hook():
    try:
        data = request.get_json(force=True)
        msg = data.get("message")
        sender = data.get("sender")
        if msg:
            add_incoming_sms(msg, sender)
            logger.info("تم استقبال SMS من %s: %s", sender, msg)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "no message"}), 400
    except Exception as e:
        logger.exception("خطأ في SMS webhook")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    load_data()
    if APP_URL:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
    app.run(host="0.0.0.0", port=PORT)
