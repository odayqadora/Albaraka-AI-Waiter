import os
import math
import re
import time
import random
import asyncio
import asyncpg
import uuid
import requests  # تمت إضافة هذه المكتبة للاتصال بالـ API بدلاً من Twilio
from datetime import datetime
from langchain_community.chat_message_histories import RedisChatMessageHistory
from fastapi import FastAPI, Request, Response, BackgroundTasks
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

# --- Database setup ---
DATABASE_URL = os.environ.get("DATABASE_URL")

async def db_execute(query, params=(), fetch=None):
    if not DATABASE_URL:
        print("Warning: DATABASE_URL not set!")
        return None

    formatted_query = query
    for i in range(1, len(params) + 1):
        formatted_query = formatted_query.replace('?', f'${i}', 1)

    if "INSERT OR REPLACE INTO order_states" in formatted_query:
        formatted_query = "INSERT INTO order_states (customer_id, state) VALUES ($1, $2) ON CONFLICT (customer_id) DO UPDATE SET state = EXCLUDED.state"

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

async def get_order_state(customer_id: str):
    result = await db_execute("SELECT state FROM order_states WHERE customer_id = ?", (customer_id,), fetch='one')
    return result[0] if result else None

async def set_order_state(customer_id: str, state: str):
    await db_execute("INSERT OR REPLACE INTO order_states (customer_id, state) VALUES (?, ?)", (customer_id, state))

async def delete_order_state(customer_id: str):
    await db_execute("DELETE FROM order_states WHERE customer_id = ?", (customer_id,))

app = FastAPI(
    title="مطعم البركة — واتساب",
    description="Webhook SaaS (Evolution API) + RAG (Gemini) للطلبات والتوصيل.",
    version="2.0.0",
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
        await conn.close()
    except Exception as e:
        print(f"❌ خطأ في إعداد قاعدة البيانات: {e}")

# --- الإعدادات الجديدة (بدون Twilio) ---
CASHIER_PHONE = os.environ.get("CASHIER_PHONE") # رقمك أنت (الكاشير)
WA_API_URL = os.environ.get("WA_API_URL", "http://localhost:8080/message/sendText/YourInstance") # رابط Evolution API
WA_API_KEY = os.environ.get("WA_API_KEY", "your_global_apikey") # مفتاح Evolution API

PRICE_PER_KM_TL = 40
MAX_DELIVERY_KM = 50

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")

def is_cashier_sender(from_field: str) -> bool:
    if not CASHIER_PHONE: return False
    return _digits_only(from_field) == _digits_only(CASHIER_PHONE)

def send_whatsapp_msg(to_phone, message):
    """دالة الإرسال الجديدة التي تتصل بـ API مسح الـ QR"""
    try:
        headers = {
            "apikey": WA_API_KEY,
            "Content-Type": "application/json"
        }
        clean_number = re.sub(r"\D", "", to_phone) 
        
        payload = {
            "number": clean_number,
            "options": {"delay": 1200, "presence": "composing"}, # يجعل البوت يظهر كأنه "يكتب..."
            "textMessage": {
                "text": message
            }
        }
        
        response = requests.post(WA_API_URL, json=payload, headers=headers)
        if response.status_code in [200, 201]:
            return True
        else:
            print(f"❌ خطأ في الإرسال: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False

async def send_with_human_delay(to_phone: str, message: str):
    send_whatsapp_msg(to_phone, message)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
menu_path = os.path.join(BASE_DIR, "data", "menu.txt")
try:
    with open(menu_path, "r", encoding="utf-8") as f:
        menu_content = f.read()
except FileNotFoundError:
    with open(os.path.join(BASE_DIR, "menu.txt"), "r", encoding="utf-8") as f:
        menu_content = f.read()

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.1,
)

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
    ("human", "{history}\nالزبون: {question}"),
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

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

def get_session_history(session_id: str):
    return RedisChatMessageHistory(
        session_id=session_id,
        url=REDIS_URL
    )

def clear_session_history(session_id: str):
    try:
        history = get_session_history(session_id)
        history.clear()
    except Exception as e:
        print(f"Error clearing Redis session: {e}")

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
    return {"status": "Al-Baraka Smart System Online (SaaS Mode)"}

@app.post("/whatsapp")
async def whatsapp_reply(request: Request, background_tasks: BackgroundTasks):
    try:
        # 1. استقبال البيانات كـ JSON بدلاً من Form الخاص بـ Twilio
        payload = await request.json()
        message_data = payload.get("data", {})
        
        # تجاهل الرسائل التي يرسلها البوت لنفسه
        if message_data.get("key", {}).get("fromMe"):
            return Response(status_code=200)

        # استخراج النص
        body = message_data.get("message", {}).get("conversation", "").strip()
        if not body:
            body = message_data.get("message", {}).get("extendedTextMessage", {}).get("text", "").strip()
            
        # استخراج الرقم
        sender_full = message_data.get("key", {}).get("remoteJid", "")
        sender = sender_full.split("@")[0]
        
        lat, lon = None, None 

    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return Response(status_code=200)

    if not body or not sender:
        return Response(status_code=200)

    clean_sender = sender

    # --- 1. لوحة تحكم الكاشير ---
    if is_cashier_sender(sender):
        cashier_match = re.match(r"^([123])\s*#?(\d+)(?:\s+(.*))?$", body, re.DOTALL)
        
        if not cashier_match:
            send_whatsapp_msg(sender, "⚠️ صيغة خاطئة.\nيرجى كتابة رقم الإجراء متبوعاً برقم الطلب.\nمثال للقبول: 1 45\nمثال للرفض: 2 45 مخلصين لحم\nرد على زبون: 3 45 رح نتأخر")
            return Response(status_code=200)

        command = cashier_match.group(1)
        order_id = int(cashier_match.group(2))
        extra_text = cashier_match.group(3) or ""

        query = """
            SELECT customer_id, status 
            FROM orders 
            WHERE daily_order_id = ? 
            AND (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date
        """
        order_data = await db_execute(query, (order_id,), fetch='one')

        if not order_data:
            send_whatsapp_msg(sender, f"⚠️ الطلب رقم #{order_id} غير موجود في قائمة طلبات اليوم.")
            return Response(status_code=200)

        customer_id = order_data[0]
        current_status = order_data[1]

        if current_status != 'pending' and command != "3":
            send_whatsapp_msg(sender, f"⚠️ الطلب رقم #{order_id} تمت معالجته مسبقاً (الحالة الحالية: {current_status}).")
            return Response(status_code=200)

        if command == "1":
            await db_execute("UPDATE orders SET status = 'confirmed' WHERE daily_order_id = ? AND (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date", (order_id,))
            send_whatsapp_msg(customer_id, f"✅ تم تأكيد طلبكم (رقم #{order_id}) من قبل المطعم.. جاري التحضير الآن! أهلاً وسهلاً فيك.")
            await delete_order_state(customer_id)
            clear_session_history(customer_id)
            send_whatsapp_msg(sender, f"✅ تم تأكيد الطلب #{order_id} بنجاح.")

        elif command == "2":
            reason = extra_text or "المطعم مزدحم حالياً"
            await db_execute("UPDATE orders SET status = 'rejected' WHERE daily_order_id = ? AND (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date", (order_id,))
            send_whatsapp_msg(customer_id, f"❌ نعتذر منكم، تم رفض الطلب (رقم #{order_id}).\nالسبب: {reason}")
            await delete_order_state(customer_id)
            clear_session_history(customer_id)
            send_whatsapp_msg(sender, f"❌ تم رفض الطلب #{order_id} وإبلاغ الزبون لسبب: {reason}")

        elif command == "3":
            if extra_text:
                send_whatsapp_msg(customer_id, f"💬 رسالة من إدارة المطعم بخصوص طلبكم (رقم #{order_id}):\n{extra_text}")
                send_whatsapp_msg(sender, f"💬 تم إرسال رسالتك لزبون الطلب #{order_id}.")
            else:
                send_whatsapp_msg(sender, f"يرجى كتابة الرسالة بعد رقم الطلب (مثال: 3 {order_id} الطلب سيصل بعد 10 دقائق)")

        return Response(status_code=200)

    # --- 2. صمت البوت للزبون المنتظر ---
    current_order_state = await get_order_state(sender)
    if current_order_state == 'waiting_cashier':
        background_tasks.add_task(send_with_human_delay, sender, "طلبكم قيد المراجعة لدى الإدارة.. بنخبركم فور التأكيد! بكل سرور.")
        return Response(status_code=200)

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

    # --- 5. كشف الفاتورة النهائية وإنشاء الطلب ---
    if "[FINAL_CONFIRMATION]" in response_text:
        summary = response_text.split("[FINAL_CONFIRMATION]")[1].strip()
        summary = f"📱 رقم الواتساب: {clean_sender}\n" + summary

        order_key = str(uuid.uuid4())
        
        query = """
            SELECT COALESCE(MAX(daily_order_id), 0) + 1 
            FROM orders 
            WHERE (created_at AT TIME ZONE 'Europe/Istanbul')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')::date
        """
        result = await db_execute(query, fetch='one')
        daily_id = result[0] if result else 1

        insert_query = "INSERT INTO orders (order_key, daily_order_id, customer_id, order_text, status) VALUES (?, ?, ?, ?, 'pending')"
        await db_execute(insert_query, (order_key, daily_id, sender, summary))

        await set_order_state(sender, 'waiting_cashier')

        response_text = response_text.split("[FINAL_CONFIRMATION]")[0].strip()
        msg_to_customer = response_text + f"\n\nتكرم! تم إرسال طلبكم (رقم #{daily_id}) للإدارة للمراجعة.. ثواني وبنأكدلكم."
        
        cashier_menu = (
            f"🔔 طلب جديد رقم #{daily_id}:\n{summary}\n\n"
            f"رد برقم الإجراء ثم رقم الطلب:\n"
            f"1️⃣ لتأكيد الطلب (اكتب: 1 {daily_id})\n"
            f"2️⃣ للرفض (اكتب: 2 {daily_id} مخلص لحم)\n"
            f"3️⃣ رسالة للزبون (اكتب: 3 {daily_id} رح نتأخر)"
        )
        send_whatsapp_msg(CASHIER_PHONE, cashier_menu)

        background_tasks.add_task(send_with_human_delay, sender, msg_to_customer)
        return Response(status_code=200)

    # --- 6. الإرسال بتأخير بشري للرسائل العادية ---
    background_tasks.add_task(send_with_human_delay, sender, response_text)

    return Response(status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_bot:app", host="0.0.0.0", port=10000, reload=True)