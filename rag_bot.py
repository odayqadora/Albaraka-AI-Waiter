import os
import math
import re
import time
import random
import asyncio
import asyncpg  # تم الاستبدال بـ asyncpg
from langchain_community.chat_message_histories import RedisChatMessageHistory
from fastapi import FastAPI, Request, Response, BackgroundTasks
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

# --- Database setup ---
DATABASE_URL = os.environ.get("DATABASE_URL")

async def db_execute(query, params=(), fetch=None):
    """General purpose async function to execute DB queries via PostgreSQL."""
    if not DATABASE_URL:
        print("Warning: DATABASE_URL not set!")
        return None

    # تحويل علامات الاستفهام الخاصة بـ SQLite (?) إلى تنسيق PostgreSQL ($1, $2, ...)
    formatted_query = query
    for i in range(1, len(params) + 1):
        formatted_query = formatted_query.replace('?', f'${i}', 1)

    # تحويل استعلامات الإدراج لتتوافق مع بيئة PostgreSQL
    if "INSERT OR REPLACE INTO order_states" in formatted_query:
        formatted_query = "INSERT INTO order_states (customer_id, state) VALUES ($1, $2) ON CONFLICT (customer_id) DO UPDATE SET state = EXCLUDED.state"
    elif "INSERT OR REPLACE INTO customer_mapping" in formatted_query:
        formatted_query = "INSERT INTO customer_mapping (key, customer_id) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET customer_id = EXCLUDED.customer_id"

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            if fetch == 'one':
                return await conn.fetchrow(formatted_query, *params)
            elif fetch == 'all':
                return await conn.fetch(formatted_query, *params)
            else:
                await conn.execute(formatted_query, *params)
                return None
        finally:
            await conn.close()
    except Exception as e:
        print(f"Database Error: {e}")
        return None

# --- Async DB functions to replace dictionaries ---
async def get_order_state(customer_id: str):
    result = await db_execute("SELECT state FROM order_states WHERE customer_id = ?", (customer_id,), fetch='one')
    return result[0] if result else None

async def set_order_state(customer_id: str, state: str):
    await db_execute("INSERT OR REPLACE INTO order_states (customer_id, state) VALUES (?, ?)", (customer_id, state))

async def get_customer_mapping(key: str):
    result = await db_execute("SELECT customer_id FROM customer_mapping WHERE key = ?", (key,), fetch='one')
    return result[0] if result else None

async def set_customer_mapping(key: str, customer_id: str):
    await db_execute("INSERT OR REPLACE INTO customer_mapping (key, customer_id) VALUES (?, ?)", (key, customer_id))

async def delete_customer_mapping(key: str):
    await db_execute("DELETE FROM customer_mapping WHERE key = ?", (key,))


app = FastAPI(
    title="مطعم البركة — واتساب",
    description="Webhook Twilio + RAG (Gemini) للطلبات والتوصيل.",
    version="1.0.0",
)

@app.on_event("startup")
async def startup_event():
    if not DATABASE_URL:
        print("Warning: DATABASE_URL not set!")
        return
    
    print("⏳ جاري فحص وإنشاء جداول قاعدة البيانات...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS order_states (
                customer_id TEXT PRIMARY KEY,
                state TEXT
            );
            CREATE TABLE IF NOT EXISTS customer_mapping (
                key TEXT PRIMARY KEY,
                customer_id TEXT
            );
        ''')
        print("✅ تم تجهيز جداول قاعدة البيانات بنجاح.")
        await conn.close()
    except Exception as e:
        print(f"❌ خطأ في إعداد قاعدة البيانات: {e}")

# الإعدادات
CASHIER_PHONE = os.environ.get("CASHIER_PHONE")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")
ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

PRICE_PER_KM_TL = 40
MAX_DELIVERY_KM = 50

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")

def is_cashier_sender(from_field: str) -> bool:
    if not CASHIER_PHONE: return False
    return _digits_only(from_field) == _digits_only(CASHIER_PHONE)

def send_whatsapp_msg(to_phone, message):
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        to_wa = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"
        client.messages.create(body=message, from_=TWILIO_WHATSAPP_FROM, to=to_wa)
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

# تم تحويل الدالة إلى async واستخدام asyncio.sleep بدلاً من time.sleep
async def send_with_human_delay(to_phone: str, message: str):
    """محاكاة السلوك البشري — تأخير عشوائي قبل الرد"""
    # تأخير أولي (وكأنه شاف الرسالة وبدأ يفكر)
    thinking_delay = random.uniform(1.5, 4.0)
    await asyncio.sleep(thinking_delay)

    # تأخير الكتابة بناءً على طول الرسالة
    words = len(message.split())
    typing_speed_wpm = random.uniform(35, 55)
    typing_delay = (words / typing_speed_wpm) * 60
    typing_delay = max(2.0, min(typing_delay, 9.0))
    await asyncio.sleep(typing_delay)

    # إرسال الرسالة
    send_whatsapp_msg(to_phone, message)

# قراءة المنيو (تأكد من وجود الملف في المسار الصحيح بناءً على هيكل مشروعك)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# ملاحظة: تأكد أن ملف menu.txt موجود بالفعل داخل مجلد data إذا استخدمت هذا السطر
menu_path = os.path.join(BASE_DIR, "data", "menu.txt")
try:
    with open(menu_path, "r", encoding="utf-8") as f:
        menu_content = f.read()
except FileNotFoundError:
    # محاولة قراءته من المجلد الرئيسي إذا لم يكن في مجلد data
    with open(os.path.join(BASE_DIR, "menu.txt"), "r", encoding="utf-8") as f:
        menu_content = f.read()

# تهيئة Gemini 2.5 Flash
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.3,
)

prompt = ChatPromptTemplate.from_messages([
    ("system", """أنت أبو علي — كاشير مطعم البركة في إسطنبول.
مش روبوت، مش مساعد. إنسان بيحب زبائنه وبيعرفهم بالاسم.
رقم الزبون مسجل تلقائياً: {sender} — لا تسأل عنه أبداً.

شخصيتك:
حنين، خفيف الدم، دايماً مرحب. بتحكي مثل صاحب المطعم اللي عنده وقت لكل زبون بس ما بيطوّل. لهجتك فلسطينية شامية طبيعية: يا هلا، تكرم، من عيوني، يسلمو، يلا حبيبي.

أسلوب الرد [اجباري]:
- سطر أو سطرين بالحد الأقصى. دايماً.
- سؤال واحد بكل رسالة، مش أكثر.
- بدون نقاط (*) أو تنسيق قوائم في الردود العادية.
- تحدث بالعربية الشامية دائماً. (استثناء وحيد: إذا كتب الزبون رسالة كاملة واضحة بالإنجليزية أو التركية، جاوبه بلغته. لكن إذا قال فقط "hi" أو كلمة أجنبية واحدة، ابقَ على اللهجة الشامية).

تدفق الطلب (خطوة بخطوة، كل خطوة برسالة لحالها):
1. الأصناف — إذا ما حدد عدد، افترض (1) ولا تسأل.
2. الموقع — للتوصيل. إذا حجز بالمطعم، تخطى هاد الخطوة.
3. الاسم — اسأل مرة وحدة بس.
4. الفاتورة — أرسلها بدفء، مثل: "يلا غالي، هاي حسابك..."

مواقف خاصة:
- طلب صنف ما عنا: "ما عنا [كذا] يا غالي، بس عنا [بديل]، شو بتفضل؟"
- تناقض (حجز + لوكيشن): "وصل اللوكيشن، نعتمد توصيل ولا حجز بالمطعم؟"
- سؤال غير متوقع أو غير واضح: اسأل توضيح بجملة واحدة فقط.
- رسالة غير مفهومة أو صورة: "آسف ما وصلني شي واضح، شو بتحتاج؟"

عند تأكيد الطلب (بأي كلمة: تمام، اعتمد، يلا، ✓):
أرسل رسالة ختامية دافئة (جملة وحدة) ثم مباشرةً هاد الفورمات:

[FINAL_CONFIRMATION]
الاسم: ...
الرقم: {sender}
النوع: توصيل / حجز
الأصناف: ...
الموقع: ...
توصيل: ...
المجموع: ... ليرة تركية.

روابط المنيو (عند الطلب فقط):
العربية: https://baraka-restoran.com/ar/?branch=baraka-restoran
الإنجليزية: https://baraka-restoran.com/en/?branch=baraka-restoran
التركية: https://baraka-restoran.com/tr/?branch=baraka-restoran

المنيو: {context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])

rag_chain = (
    RunnablePassthrough.assign(
        context=lambda x: menu_content,
        sender=lambda x: x.get("sender", "")
    )
    | prompt
    | llm
    | StrOutputParser()
)

# جلب رابط رديس من متغيرات البيئة
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

def get_session_history(session_id: str):
    # ربط محادثة كل زبون (رقم الواتساب) بقاعدة بيانات رديس الخارجية
    return RedisChatMessageHistory(
        session_id=session_id,
        url=REDIS_URL
    )

conversational_rag_chain = RunnableWithMessageHistory(
    rag_chain, get_session_history, input_messages_key="question", history_messages_key="history"
)

def calculate_delivery_fee(user_lat, user_lon):
    r_lat, r_lon = 41.235278, 28.774333
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [r_lat, r_lon, float(user_lat), float(user_lon)])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    distance = R * 2 * math.asin(math.sqrt(a))
    if distance > MAX_DELIVERY_KM: return round(distance, 2), -1
    return round(distance, 2), round(distance * PRICE_PER_KM_TL, 2)

@app.get("/")
async def home():
    return {"status": "Al-Baraka Smart System Online (FastAPI)"}

@app.post("/whatsapp")
async def whatsapp_reply(request: Request, background_tasks: BackgroundTasks):
    form_data = await request.form()
    body = form_data.get("Body", "").strip()
    sender = form_data.get("From", "")
    lat = form_data.get("Latitude")
    lon = form_data.get("Longitude")

    clean_sender = sender.replace("whatsapp:", "")

    resp = MessagingResponse()

    # --- 1. لوحة تحكم الكاشير ---
    if is_cashier_sender(sender):
        customer_id = await get_customer_mapping("current")
        if not customer_id:
            resp.message().body("لا توجد طلبات معلقة حالياً.")
            return Response(content=str(resp), media_type="application/xml")

        if body == "1":
            send_whatsapp_msg(customer_id, "✅ تم تأكيد طلبكم من قبل المطعم.. جاري التحضير الآن! أهلاً وسهلاً فيك.")
            await set_order_state(customer_id, 'confirmed')
            await delete_customer_mapping("current")
            resp.message().body("تم إرسال التأكيد للزبون.")

        elif body.startswith("2"):
            reason = body[1:].strip() or "المطعم مزدحم حالياً"
            send_whatsapp_msg(customer_id, f"❌ نعتذر منكم، تم رفض الطلب.\nالسبب: {reason}")
            await set_order_state(customer_id, 'rejected')
            await delete_customer_mapping("current")
            resp.message().body(f"تم إبلاغ الزبون بالرفض لسبب: {reason}")

        elif body.startswith("3"):
            custom_msg = body[1:].strip()
            if custom_msg:
                send_whatsapp_msg(customer_id, f"💬 رسالة من إدارة المطعم:\n{custom_msg}")
                resp.message().body("تم إرسال رسالتك للزبون.")
            else:
                resp.message().body("يرجى كتابة الرسالة بعد الرقم 3 (مثال: 3 الطلب سيصل بعد 10 دقائق)")

        else:
            resp.message().body("خيارات الكاشير:\n1. تأكيد\n2. رفض (اكتب 2 والسبب)\n3. رد (اكتب 3 والرسالة)")

        return Response(content=str(resp), media_type="application/xml")

    # --- 2. صمت البوت للزبون المنتظر ---
    current_order_state = await get_order_state(sender)
    if current_order_state == 'waiting_cashier':
        background_tasks.add_task(send_with_human_delay, sender, "طلبكم قيد المراجعة لدى الإدارة.. بنخبركم فور التأكيد! بكل سرور.")
        return Response(content="<Response></Response>", media_type="application/xml")

    # --- 3. منطق الموقع ---
    if lat and lon:
        dist, fee = calculate_delivery_fee(lat, lon)
        google_link = f"http://maps.google.com/?q={lat},{lon}"
        if fee == -1:
            body = f"[نظام: الموقع خارج التغطية {dist}كم]"
        else:
            body = f"[نظام: الموقع {google_link} | المسافة {dist}كم | التوصيل {fee} ليرة. اطلب اسم الزبون الآن]"

    # --- 4. استدعاء الذكاء الاصطناعي ---
    try:
        response_text = conversational_rag_chain.invoke(
            {
                "question": body,
                "sender": clean_sender
            },
            config={"configurable": {"session_id": sender}}
        )
    except Exception as e:
        print(f"LLM Error: {e}")
        response_text = "أهلاً وسهلاً فيك، نواجه ضغطاً بسيطاً في النظام، هل يمكنك إعادة إرسال الطلب لو سمحت؟"

    # --- 5. كشف الفاتورة النهائية ---
    if "[FINAL_CONFIRMATION]" in response_text:
        summary = response_text.split("[FINAL_CONFIRMATION]")[1].strip()
        summary = f"📱 رقم الواتساب: {clean_sender}\n" + summary

        await set_customer_mapping("current", sender)
        await set_order_state(sender, 'waiting_cashier')

        response_text = response_text.split("[FINAL_CONFIRMATION]")[0].strip()
        if not response_text:
            response_text = "تكرم! تم إرسال الطلب للإدارة للمراجعة.. ثواني وبنأكدلكم."
        else:
            response_text += "\n\nتكرم! تم إرسال الطلب للإدارة للمراجعة.. ثواني وبنأكدلكم."

        cashier_menu = (
            f"🔔 طلب جديد:\n{summary}\n\n"
            f"رد برقم الإجراء:\n"
            f"1️⃣ لتأكيد الطلب\n"
            f"2️⃣ للرفض (مثال: 2 المنسف خلص)\n"
            f"3️⃣ للرد على الزبون (مثال: 3 رح نتأخر)"
        )
        send_whatsapp_msg(CASHIER_PHONE, cashier_menu)

    # --- 6. الإرسال بتأخير بشري باستخدام BackgroundTasks ---
    background_tasks.add_task(send_with_human_delay, sender, response_text)

    # رد صالح لـ Twilio لمنع الأخطاء
    return Response(content="<Response></Response>", media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_bot:app", host="0.0.0.0", port=10000, reload=True)