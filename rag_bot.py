import os
import math
import re
import time
import random
import asyncio
import asyncpg  # تم الاستبدال بـ asyncpg[cite: 2]
import uuid     # تمت الإضافة لتوليد المعرفات المخفية
from datetime import datetime
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
DATABASE_URL = os.environ.get("DATABASE_URL")[cite: 2]

async def db_execute(query, params=(), fetch=None):
    """General purpose async function to execute DB queries via PostgreSQL."""
    if not DATABASE_URL:
        print("Warning: DATABASE_URL not set!")
        return None

    # تحويل علامات الاستفهام الخاصة بـ SQLite (?) إلى تنسيق PostgreSQL ($1, $2, ...)[cite: 2]
    formatted_query = query
    for i in range(1, len(params) + 1):
        formatted_query = formatted_query.replace('?', f'${i}', 1)

    if "INSERT OR REPLACE INTO order_states" in formatted_query:
        formatted_query = "INSERT INTO order_states (customer_id, state) VALUES ($1, $2) ON CONFLICT (customer_id) DO UPDATE SET state = EXCLUDED.state"[cite: 2]

    try:
        conn = await asyncpg.connect(DATABASE_URL)[cite: 2]
        try:
            if fetch == 'one':
                return await conn.fetchrow(formatted_query, *params)[cite: 2]
            elif fetch == 'all':
                return await conn.fetch(formatted_query, *params)[cite: 2]
            else:
                await conn.execute(formatted_query, *params)[cite: 2]
                return None
        finally:
            await conn.close()[cite: 2]
    except Exception as e:
        print(f"Database Error: {e}")[cite: 2]
        return None

async def get_order_state(customer_id: str):
    result = await db_execute("SELECT state FROM order_states WHERE customer_id = ?", (customer_id,), fetch='one')[cite: 2]
    return result[0] if result else None[cite: 2]

async def set_order_state(customer_id: str, state: str):
    await db_execute("INSERT OR REPLACE INTO order_states (customer_id, state) VALUES (?, ?)", (customer_id, state))[cite: 2]

async def delete_order_state(customer_id: str):
    await db_execute("DELETE FROM order_states WHERE customer_id = ?", (customer_id,))

app = FastAPI(
    title="مطعم البركة — واتساب",
    description="Webhook Twilio + RAG (Gemini) للطلبات والتوصيل.",
    version="1.0.0",
)[cite: 2]

@app.on_event("startup")
async def startup_event():
    if not DATABASE_URL:
        print("Warning: DATABASE_URL not set!")
        return
    
    print("⏳ جاري فحص وإنشاء جداول قاعدة البيانات...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)[cite: 2]
        # تم إضافة جدول orders واستبدال customer_mapping 
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS order_states (
                customer_id TEXT PRIMARY KEY,
                state TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_key TEXT PRIMARY KEY,
                daily_order_id INTEGER,
                customer_id TEXT,
                order_text TEXT,
                status TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        print("✅ تم تجهيز جداول قاعدة البيانات بنجاح.")
        await conn.close()[cite: 2]
    except Exception as e:
        print(f"❌ خطأ في إعداد قاعدة البيانات: {e}")[cite: 2]

# الإعدادات[cite: 2]
CASHIER_PHONE = os.environ.get("CASHIER_PHONE")[cite: 2]
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")[cite: 2]
ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")[cite: 2]
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")[cite: 2]

PRICE_PER_KM_TL = 40[cite: 2]
MAX_DELIVERY_KM = 50[cite: 2]

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[cite: 2]

def is_cashier_sender(from_field: str) -> bool:
    if not CASHIER_PHONE: return False[cite: 2]
    return _digits_only(from_field) == _digits_only(CASHIER_PHONE)[cite: 2]

def send_whatsapp_msg(to_phone, message):
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)[cite: 2]
        to_wa = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"[cite: 2]
        client.messages.create(body=message, from_=TWILIO_WHATSAPP_FROM, to=to_wa)[cite: 2]
        return True
    except Exception as e:
        print(f"❌ Error: {e}")[cite: 2]
        return False

async def send_with_human_delay(to_phone: str, message: str):
    """إرسال فوري بدون أي تأخير زمني"""[cite: 2]
    send_whatsapp_msg(to_phone, message)[cite: 2]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))[cite: 2]
menu_path = os.path.join(BASE_DIR, "data", "menu.txt")[cite: 2]
try:
    with open(menu_path, "r", encoding="utf-8") as f:
        menu_content = f.read()[cite: 2]
except FileNotFoundError:
    with open(os.path.join(BASE_DIR, "menu.txt"), "r", encoding="utf-8") as f:
        menu_content = f.read()[cite: 2]

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.1,
)[cite: 2]

prompt = ChatPromptTemplate.from_messages([
    ("system", """أنت أبو علي — كاشير مطعم البركة في إسطنبول.
مش روبوت، مش مساعد. إنسان بيحب زبائنه وبيعرفهم بالاسم.
رقم الزبون مسجل تلقائياً: {sender} — لا تسأل عنه أبداً.

شخصيتك:
حنين، خفيف الدم، دايماً مرحب. بتحكي مثل صاحب المطعم اللي عنده وقت لكل زبون بس ما بيطوّل. لهجتك فلسطينية شامية طبيعية.

أسلوب الرد [اجباري]:
- سطر أو سطرين بالحد الأقصى. دايماً.
- سؤال واحد بكل رسالة، مش أكثر.
- بدون نقاط (*) أو تنسيق قوائم في الردود العادية.
- تحدث بالعربية الشامية دائماً.

تدفق الطلب (خطوة بخطوة، كل خطوة برسالة لحالها):
1. الأصناف — إذا ما حدد عدد، افترض (1) ولا تسأل.
2. الموقع — للتوصيل. إذا حجز بالمطعم، تخطى هاد الخطوة.
3. الاسم — اسأل مرة وحدة بس.
4. الفاتورة — أرسلها بدفء، مثل: "يلا غالي، هاي حسابك..." 

مواقف خاصة وممنوعات (خط أحمر):
- طلب صنف ما عنا: "ما عنا [كذا] يا غالي، بس عنا [بديل]، شو بتفضل؟"
- الأسعار والخصومات: ممنوع منعاً باتاً تقديم أي خصم. الأسعار ثابتة من المنيو والمسافة فقط.
- حدود المحادثة: أنت كاشير فقط. أي موضوع خارج الطعام، تجاهله بلباقة وحول الحديث للطلب.

عند تأكيد الطلب (بأي كلمة: تمام، اعتمد، يلا، ✓):
أرسل رسالة ختامية دافئة (جملة وحدة) ثم مباشرةً هاد الفورمات:

[FINAL_CONFIRMATION]
الاسم: ...
الرقم: {sender}
النوع: توصيل / حجز
الأصناف والتعديلات: (اذكر كل صنف مع أي تعديل طلبه الزبون بجانبه)
الموقع: ...
توصيل: ...
المجموع: ... ليرة تركية.

المنيو: {context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])[cite: 2]

rag_chain = (
    RunnablePassthrough.assign(
        context=lambda x: menu_content,
        sender=lambda x: x.get("sender", "")
    )
    | prompt
    | llm
    | StrOutputParser()
)[cite: 2]

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")[cite: 2]

def get_session_history(session_id: str):
    return RedisChatMessageHistory(
        session_id=session_id,
        url=REDIS_URL
    )[cite: 2]

def clear_session_history(session_id: str):
    """دالة جديدة لمسح محادثة الزبون من الذاكرة بعد تأكيد الطلب"""
    try:
        history = get_session_history(session_id)
        history.clear()
    except Exception as e:
        print(f"Error clearing Redis session: {e}")

conversational_rag_chain = RunnableWithMessageHistory(
    rag_chain, get_session_history, input_messages_key="question", history_messages_key="history"
)[cite: 2]

def calculate_delivery_fee(user_lat, user_lon):
    r_lat, r_lon = 41.235278, 28.774333[cite: 2]
    R = 6371.0[cite: 2]
    lat1, lon1, lat2, lon2 = map(math.radians, [r_lat, r_lon, float(user_lat), float(user_lon)])[cite: 2]
    dlon, dlat = lon2 - lon1, lat2 - lat1[cite: 2]
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2[cite: 2]
    distance = R * 2 * math.asin(math.sqrt(a))[cite: 2]
    if distance > MAX_DELIVERY_KM: return round(distance, 2), -1[cite: 2]
    return round(distance, 2), round(distance * PRICE_PER_KM_TL, 2)[cite: 2]

@app.get("/")
async def home():
    return {"status": "Al-Baraka Smart System Online (FastAPI)"}[cite: 2]

@app.post("/whatsapp")
async def whatsapp_reply(request: Request, background_tasks: BackgroundTasks):
    form_data = await request.form()[cite: 2]
    body = form_data.get("Body", "").strip()[cite: 2]
    sender = form_data.get("From", "")[cite: 2]
    lat = form_data.get("Latitude")[cite: 2]
    lon = form_data.get("Longitude")[cite: 2]

    clean_sender = sender.replace("whatsapp:", "")[cite: 2]
    resp = MessagingResponse()[cite: 2]

    # --- 1. لوحة تحكم الكاشير ---
    if is_cashier_sender(sender):
        # استخدام التعبيرات النمطية لالتقاط رقم الأمر ورقم الفاتورة (مثال: 1 45)
        cashier_match = re.match(r"^([123])\s*#?(\d+)(?:\s+(.*))?$", body, re.DOTALL)
        
        if not cashier_match:
            resp.message().body("⚠️ صيغة خاطئة.\nيرجى كتابة رقم الإجراء متبوعاً برقم الطلب.\nمثال للقبول: 1 45\nمثال للرفض: 2 45 مخلصين لحم\nرد على زبون: 3 45 رح نتأخر")
            return Response(content=str(resp), media_type="application/xml")[cite: 2]

        command = cashier_match.group(1)
        order_id = int(cashier_match.group(2))
        extra_text = cashier_match.group(3) or ""

        # جلب الطلب بناءً على الرقم المتسلسل وتاريخ اليوم (بتوقيت إسطنبول)
        query = """
            SELECT customer_id, status 
            FROM orders 
            WHERE daily_order_id = ? 
            AND (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date
        """
        order_data = await db_execute(query, (order_id,), fetch='one')

        if not order_data:
            resp.message().body(f"⚠️ الطلب رقم #{order_id} غير موجود في قائمة طلبات اليوم.")
            return Response(content=str(resp), media_type="application/xml")[cite: 2]

        customer_id = order_data[0]
        current_status = order_data[1]

        if current_status != 'pending' and command != "3":
            resp.message().body(f"⚠️ الطلب رقم #{order_id} تمت معالجته مسبقاً (الحالة الحالية: {current_status}).")
            return Response(content=str(resp), media_type="application/xml")[cite: 2]

        if command == "1":
            await db_execute("UPDATE orders SET status = 'confirmed' WHERE daily_order_id = ? AND (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date", (order_id,))
            send_whatsapp_msg(customer_id, f"✅ تم تأكيد طلبكم (رقم #{order_id}) من قبل المطعم.. جاري التحضير الآن! أهلاً وسهلاً فيك.")
            await delete_order_state(customer_id)
            clear_session_history(customer_id) # مسح الذاكرة ليبدأ الزبون من جديد المرة القادمة
            resp.message().body(f"✅ تم تأكيد الطلب #{order_id} بنجاح.")

        elif command == "2":
            reason = extra_text or "المطعم مزدحم حالياً"
            await db_execute("UPDATE orders SET status = 'rejected' WHERE daily_order_id = ? AND (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date", (order_id,))
            send_whatsapp_msg(customer_id, f"❌ نعتذر منكم، تم رفض الطلب (رقم #{order_id}).\nالسبب: {reason}")
            await delete_order_state(customer_id)
            clear_session_history(customer_id) # مسح الذاكرة
            resp.message().body(f"❌ تم رفض الطلب #{order_id} وإبلاغ الزبون لسبب: {reason}")

        elif command == "3":
            if extra_text:
                send_whatsapp_msg(customer_id, f"💬 رسالة من إدارة المطعم بخصوص طلبكم (رقم #{order_id}):\n{extra_text}")
                resp.message().body(f"💬 تم إرسال رسالتك لزبون الطلب #{order_id}.")
            else:
                resp.message().body(f"يرجى كتابة الرسالة بعد رقم الطلب (مثال: 3 {order_id} الطلب سيصل بعد 10 دقائق)")

        return Response(content=str(resp), media_type="application/xml")[cite: 2]

    # --- 2. صمت البوت للزبون المنتظر ---
    current_order_state = await get_order_state(sender)[cite: 2]
    if current_order_state == 'waiting_cashier':
        background_tasks.add_task(send_with_human_delay, sender, "طلبكم قيد المراجعة لدى الإدارة.. بنخبركم فور التأكيد! بكل سرور.")[cite: 2]
        return Response(content="<Response></Response>", media_type="application/xml")[cite: 2]

    # --- 3. منطق الموقع ---
    if lat and lon:
        dist, fee = calculate_delivery_fee(lat, lon)[cite: 2]
        google_link = f"http://maps.google.com/?q={lat},{lon}"[cite: 2]
        if fee == -1:
            body = f"[نظام: الموقع خارج التغطية {dist}كم]"[cite: 2]
        else:
            body = f"[نظام: الموقع {google_link} | المسافة {dist}كم | التوصيل {fee} ليرة. اطلب اسم الزبون الآن]"[cite: 2]

    # --- 4. استدعاء الذكاء الاصطناعي ---
    try:
        response_text = conversational_rag_chain.invoke(
            {
                "question": body,
                "sender": clean_sender
            },
            config={"configurable": {"session_id": sender}}
        )[cite: 2]
    except Exception as e:
        print(f"LLM Error: {e}")[cite: 2]
        response_text = "أهلاً وسهلاً فيك، نواجه ضغطاً بسيطاً في النظام، هل يمكنك إعادة إرسال الطلب لو سمحت؟"[cite: 2]

    # --- 5. كشف الفاتورة النهائية وإنشاء الطلب ---
    if "[FINAL_CONFIRMATION]" in response_text:
        summary = response_text.split("[FINAL_CONFIRMATION]")[1].strip()[cite: 2]
        summary = f"📱 رقم الواتساب: {clean_sender}\n" + summary[cite: 2]

        # توليد المفتاح المخفي 
        order_key = str(uuid.uuid4())
        
        # حساب رقم الطلب المتسلسل لليوم الحالي (بتوقيت إسطنبول)
        query = """
            SELECT COALESCE(MAX(daily_order_id), 0) + 1 
            FROM orders 
            WHERE (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date
        """
        result = await db_execute(query, fetch='one')
        daily_id = result[0] if result else 1

        # إدراج الطلب في قاعدة البيانات 
        insert_query = "INSERT INTO orders (order_key, daily_order_id, customer_id, order_text, status) VALUES (?, ?, ?, ?, 'pending')"
        await db_execute(insert_query, (order_key, daily_id, sender, summary))

        await set_order_state(sender, 'waiting_cashier')[cite: 2]

        response_text = response_text.split("[FINAL_CONFIRMATION]")[0].strip()[cite: 2]
        msg_to_customer = response_text + f"\n\nتكرم! تم إرسال طلبكم (رقم #{daily_id}) للإدارة للمراجعة.. ثواني وبنأكدلكم."
        
        # إرسال التنبيه للكاشير 
        cashier_menu = (
            f"🔔 طلب جديد رقم #{daily_id}:\n{summary}\n\n"
            f"رد برقم الإجراء ثم رقم الطلب:\n"
            f"1️⃣ لتأكيد الطلب (اكتب: 1 {daily_id})\n"
            f"2️⃣ للرفض (اكتب: 2 {daily_id} مخلص لحم)\n"
            f"3️⃣ رسالة للزبون (اكتب: 3 {daily_id} رح نتأخر)"
        )
        send_whatsapp_msg(CASHIER_PHONE, cashier_menu)[cite: 2]

        background_tasks.add_task(send_with_human_delay, sender, msg_to_customer)[cite: 2]
        return Response(content="<Response></Response>", media_type="application/xml")[cite: 2]

    # --- 6. الإرسال بتأخير بشري للرسائل العادية ---
    background_tasks.add_task(send_with_human_delay, sender, response_text)[cite: 2]

    return Response(content="<Response></Response>", media_type="application/xml")[cite: 2]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_bot:app", host="0.0.0.0", port=10000, reload=True)[cite: 2]