import os, json, time, threading, re
from collections import deque
from flask import Flask, request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ========= الإعدادات من بيئة التشغيل =========
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("ضع TELEGRAM_TOKEN في إعدادات البيئة على Render")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # يفضّل رقم chat_id
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "0933000000")  # رقم التحويل (عدّله)
PAYMENT_CODE = os.environ.get("PAYMENT_CODE", "7788297")         # كود يمكن تغييره بسهولة
SMS_SHARED_SECRET = os.environ.get("SMS_SHARED_SECRET", "changeme")  # سر بسيط لاستقبال /sms

PORT = int(os.environ.get("PORT", "10000"))
DATA_FILE = "users_data.json"

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ========= تخزين المستخدمين والحالات =========
# الحالة: تحديد أين وصل المستخدم في الحوار
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

users = {}  # user_id -> dict
# شكل بيانات المستخدم:
# {
#   "state": S_*,
#   "full_name": "...",
#   "age": 0,
#   "successful_topups": 0,
#   "pending": {
#       "amount": 0,
#       "method": "syriatel_cash",
#       "requested_at": ts
#   }
# }

# تخزين آخر رسائل SMS الواردة (للمطابقة)
# سنحتفظ بآخر 200 رسالة
incoming_sms = deque(maxlen=200)

# ========= أدوات مساعدة =========
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
    if str(uid) not in users:
        users[str(uid)] = {
            "state": S_IDLE,
            "full_name": None,
            "age": None,
            "successful_topups": 0,
            "pending": {}
        }

def kb_main():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🟢 تعبئة الحساب"))
    return kb

def kb_back():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
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

def is_valid_full_name(name: str) -> bool:
    # اسم ثلاثي: ثلاث كلمات على الأقل، كل كلمة >=2 حروف (عربي/لاتيني)
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if len(parts) < 3:
        return False
    for p in parts:
        if len(p) < 2:
            return False
        # أحرف عربية أو لاتينية أو شرطة بسيطة
        if not re.fullmatch(r"[A-Za-z\u0600-\u06FF\-ʼ']+", p):
            return False
    return True

def is_valid_age(text: str) -> bool:
    if not re.fullmatch(r"\d{1,3}", text.strip()):
        return False
    age = int(text)
    return 10 <= age <= 100

def is_valid_amount(text: str) -> bool:
    if not re.fullmatch(r"\d+", text.strip()):
        return False
    amount = int(text)
    if not (10000 <= amount <= 1000000):
        return False
    # مضاعفات 5000 (آلاف من مضاعفات 5) وتنتهي بـ 000
    return amount % 5000 == 0

def reset_to_main(uid, chat_id):
    users[str(uid)]["state"] = S_MAIN_MENU
    users[str(uid)]["pending"] = {}
    save_data()
    bot.send_message(
        chat_id,
        "عدتَ إلى الواجهة الأساسية.\nاختر إجراءً:",
        reply_markup=kb_main()
    )

def summarize_profile(u):
    return (
        f"👤 <b>الاسم:</b> {u.get('full_name')}\n"
        f"🎂 <b>العمر:</b> {u.get('age')}\n"
        f"✅ <b>مرات التعبئة الناجحة:</b> {u.get('successful_topups',0)}"
    )

def send_admin_notification(user_id, username, profile, amount):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "📥 <b>طلب تعبئة جديد</b>\n\n"
        f"{profile}\n"
        f"💳 <b>القيمة المطلوبة:</b> {amount:,} ل.س\n"
        f"👤 <b>User ID:</b> {user_id}\n"
        f"🔗 <b>Username:</b> @{username if username else '—'}"
    )
    try:
        bot.send_message(int(ADMIN_CHAT_ID), text)
    except Exception:
        # إذا كان ADMIN_CHAT_ID ليس رقماً، محاولة الإرسال كنص (قد يفشل)
        try:
            bot.send_message(ADMIN_CHAT_ID, text)
        except Exception:
            pass

# ========= استقبال /start =========
@bot.message_handler(commands=["start"])
def on_start(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    users[str(uid)]["state"] = S_WAIT_NAME
    save_data()

    welcome = (
        "مرحبا بك في بوت ali كاشيرا ايشانسي 💚\n"
        "لسنا الوحيدين لكننا الأفضل 😌😎❤️‍🔥"
    )
    bot.send_message(chat_id, welcome, reply_markup=ReplyKeyboardRemove())
    time.sleep(1.5)  # تأخير بسيط ثم طلب الاسم
    bot.send_message(chat_id, "الرجاء إدخال <b>الاسم الثلاثي</b>:", reply_markup=kb_back())

# ========= زر رجوع عام =========
def is_back(msg):
    return msg.text and msg.text.strip() in ["⬅️ رجوع", "رجوع", "عودة"]

# ========= استلام الرسائل =========
@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(msg):
    uid = msg.from_user.id
    chat_id = msg.chat.id
    ensure_user(uid)
    u = users[str(uid)]
    text = (msg.text or "").strip()

    # زر رجوع: يلغي أي تدفق ويعود للواجهة
    if is_back(msg):
        reset_to_main(uid, chat_id)
        return

    state = u.get("state", S_IDLE)

    # إدخال الاسم
    if state == S_WAIT_NAME:
        if is_valid_full_name(text):
            u["full_name"] = text
            u["state"] = S_WAIT_AGE
            save_data()
            bot.send_message(chat_id, "جيد ✅\nالآن أدخل <b>العمر</b> (رقم من 10 إلى 100):", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "❌ الاسم غير صالح. الرجاء إدخال اسم ثلاثي صحيح.", reply_markup=kb_back())
        return

    # إدخال العمر
    if state == S_WAIT_AGE:
        if is_valid_age(text):
            u["age"] = int(text)
            u["state"] = S_MAIN_MENU
            save_data()
            bot.send_message(chat_id, "تم حفظ بياناتك ✅\n", reply_markup=ReplyKeyboardRemove())
            bot.send_message(chat_id, "معلومات حسابك:\n" + summarize_profile(u), reply_markup=kb_main())
        else:
            bot.send_message(chat_id, "❌ العمر غير صالح. أدخل رقمًا بين 10 و100.", reply_markup=kb_back())
        return

    # واجهة رئيسية
    if state == S_MAIN_MENU:
        if text == "🟢 تعبئة الحساب":
            u["state"] = S_TOPUP_METHOD
            save_data()
            bot.send_message(chat_id, "اختر طريقة التعبئة:", reply_markup=kb_only_syriatel())
        else:
            bot.send_message(chat_id, "اختر من الأزرار:", reply_markup=kb_main())
        return

    # اختيار طريقة التعبئة
    if state == S_TOPUP_METHOD:
        if text == "سيريتيل كاش":
            u["pending"] = {"method": "syriatel_cash", "amount": 0, "requested_at": time.time()}
            u["state"] = S_WAIT_AMOUNT
            save_data()
            bot.send_message(
                chat_id,
                "أدخل قيمة التعبئة (من 10000 إلى 1000000) وبمضاعفات 5000 مثل 10000 / 15000 / 20000 ...",
                reply_markup=kb_back()
            )
        else:
            bot.send_message(chat_id, "الرجاء اختيار 'سيريتيل كاش'.", reply_markup=kb_only_syriatel())
        return

    # إدخال المبلغ
    if state == S_WAIT_AMOUNT:
        if is_valid_amount(text):
            amount = int(text)
            u["pending"]["amount"] = amount
            u["state"] = S_WAIT_CONFIRM_SENT
            save_data()
            bot.send_message(
                chat_id,
                (
                    f"حوِّل المبلغ إلى هذا الرقم:\n"
                    f"<b>{PAYMENT_NUMBER}</b>\n\n"
                    f"استخدم كود العملية التالي: <code>{PAYMENT_CODE}</code>\n\n"
                    f"بعد التحويل اضغط زر <b>تم</b>."
                ),
                reply_markup=kb_done_back()
            )
        else:
            bot.send_message(chat_id, "❌ المبلغ غير صالح. جرّب مثل: 10000، 15000، 20000 ... حتى 1000000.", reply_markup=kb_back())
        return

    # تأكيد أنه حوّل
    if state == S_WAIT_CONFIRM_SENT:
        if text == "✅ تم":
            u["state"] = S_WAIT_TRANSFER_CODE
            save_data()
            bot.send_message(chat_id, "أدخل <b>رمز عملية التحويل</b> كما وصل في رسالة SMS من Syriatel:", reply_markup=kb_back())
        else:
            bot.send_message(chat_id, "اضغط زر <b>تم</b> بعد إجراء التحويل، أو <b>رجوع</b> للإلغاء.", reply_markup=kb_done_back())
        return

    # إدخال رمز العملية والتحقق مع SMS
    if state == S_WAIT_TRANSFER_CODE:
        # نتوقع الرمز أرقام وحروف (لا بأس بمرونة)
        trx_code = text
        amount = u.get("pending", {}).get("amount", 0)
        ok, sms_dbg = match_sms_with(trx_code, amount)

        if ok:
            u["successful_topups"] = int(u.get("successful_topups", 0)) + 1
            save_data()
            bot.send_message(
                chat_id,
                "✅ تمت العملية بنجاح.\nسيتم تعبئة حسابك خلال ربع ساعة.",
                reply_markup=kb_main()
            )
            profile = summarize_profile(u)
            send_admin_notification(uid, msg.from_user.username, profile, amount)
            u["state"] = S_MAIN_MENU
            u["pending"] = {}
            save_data()
        else:
            bot.send_message(
                chat_id,
                "❌ الرمز غير صحيح أو لا توجد رسالة مطابقة من Syriatel بالمبلغ المدخل.\n"
                "تحقق ثم أعد إدخال الرمز، أو اختر <b>رجوع</b> للإلغاء.",
                reply_markup=kb_back()
            )
        return

    # أي حالة أخرى
    bot.send_message(chat_id, "اختر من القائمة:", reply_markup=kb_main())

# ========= مطابقة الرسالة القصيرة =========
# ملاحظة: صيغة رسائل Syriatel تختلف، عدّل RegEx أدناه بما يتوافق مع الصيغة الفعلية.
# سنفترض وجود نص يشبه:
# "تم استلام 150,000 ل.س من الرقم 09xxxxxxxx بعملية تحويل ذات رمز ABC123"
AMOUNT_RE = r"(?P<amount>\d{1,3}(?:[\.,]\d{3})*|\d+)\s*"
CODE_RE = r"(?P<code>[A-Za-z0-9]+)"
SENDER_ALLOW = re.compile(r"syriatel", re.I)

def parse_amount_to_int(txt: str) -> int:
    # يحذف الفواصل ثم يحوّله لعدد
    t = re.sub(r"[^\d]", "", txt)
    return int(t) if t else 0

def extract_sms_info(body: str):
    # حاول استخراج المبلغ والرمز
    # أمثلة محتملة:
    # "تم استلام 150,000 ل.س ... بعملية تحويل ذات رمز ABC123"
    m1 = re.search(r"تم\s+استلام\s+" + AMOUNT_RE + r"(?:ل\.س|ليرة|ليرة\s+سورية).*?(?:رمز|رقم)\s+" + CODE_RE, body, flags=re.I|re.S)
    if m1:
        amount_txt = m1.group("amount")
        code = m1.group("code")
        return parse_amount_to_int(amount_txt), code
    # محاولات أخرى إن لزم
    return 0, None

def match_sms_with(user_code: str, user_amount: int):
    """
    يبحث في آخر الرسائل المخزّنة عن رسالة من Syriatel تحمل نفس الرمز ونفس المبلغ.
    """
    user_code_norm = user_code.strip().upper()
    for it in list(incoming_sms)[::-1]:  # الأحدث أولاً
        sender = it.get("sender", "")
        body = it.get("body", "")
        if not SENDER_ALLOW.search(sender or ""):
            continue
        amt, code = extract_sms_info(body or "")
        if code and code.strip().upper() == user_code_norm and amt == user_amount:
            return True, {"sender": sender, "amount": amt, "code": code}
    return False, None

# ========= مسار استقبال SMS من تطبيق SMS Gateway =========
@app.route("/sms", methods=["POST"])
def receive_sms():
    # أضف Header بسيط للحماية
    if request.headers.get("X-Secret") != SMS_SHARED_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    # حاول دعم حقول عامة: sender/from, message/body
    sender = data.get("sender") or data.get("from") or data.get("address") or ""
    body = data.get("message") or data.get("body") or ""

    incoming_sms.append({
        "ts": time.time(),
        "sender": sender,
        "body": body
    })
    return jsonify({"status": "ok"})

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# ========= تشغيل البوت و Flask معاً على Render =========
def run_bot():
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)

if __name__ == "__main__":
    load_data()
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
