import os
import math
import re
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

app = Flask(__name__)

# الإعدادات
CASHIER_PHONE = os.environ.get("CASHIER_PHONE")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")
ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

PRICE_PER_KM_TL = 40
MAX_DELIVERY_KM = 50

# مخزن الحالات (يفضل Redis للإنتاج)
order_states = {} 
last_customer_mapping = {} # لربط الكاشير بآخر زبون أرسل طلباً

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

with open("data/menu.txt", "r", encoding="utf-8") as f:
    menu_content = f.read()

llm = ChatGroq(
    groq_api_key=os.environ.get("GROQ_API_KEY"),
    model_name="llama-3.3-70b-versatile",
    temperature=0.1,
)

prompt = ChatPromptTemplate.from_messages([
    ("system", """أنت المساعد الذكي لـ "مطعم البركة". تحدث دائماً بصيغة الجمع "نحن".

قواعد الحوار:
1. اللغات: أجب بلغة الزبون (فلسطيني، تركي، إنجليزي).
2. التدفق: اطلب الأصناف -> اطلب الموقع -> اطلب الاسم (أخيراً) -> اعرض الفاتورة.
3. الفاتورة: يجب أن تشمل (الأصناف وأسعارها، اسم الطلبية، سعر التوصيل، المجموع النهائي).
4. التأكيد: اطلب من الزبون كتابة "تأكيد" لإرسال الطلب.

عند موافقة الزبون، اتبع هذا التنسيق حرفياً:
[FINAL_CONFIRMATION]
الاسم: (اسم الزبون)
النوع: (توصيل / حجز)
الأصناف: (التفاصيل والأسعار)
الموقع: (رابط الخرائط)
توصيل: (السعر)
المجموع: (الإجمالي) ليرة تركية.

المنيو: {context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])

rag_chain = (RunnablePassthrough.assign(context=lambda x: menu_content) | prompt | llm | StrOutputParser())
store = {}

def get_session_history(session_id: str):
    if session_id not in store: store[session_id] = ChatMessageHistory()
    return store[session_id]

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

@app.route("/", methods=["GET"])
def home(): return "Al-Baraka Smart System Online", 200

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    body = request.values.get("Body", "").strip()
    sender = request.values.get("From", "")
    lat, lon = request.values.get("Latitude"), request.values.get("Longitude")
    resp = MessagingResponse()

    # --- 1. لوحة تحكم الكاشير ---
    if is_cashier_sender(sender):
        customer_id = last_customer_mapping.get("current")
        if not customer_id:
            resp.message().body("لا توجد طلبات معلقة حالياً.")
            return str(resp)

        if body == "1": # تأكيد
            send_whatsapp_msg(customer_id, "✅ تم تأكيد طلبكم من قبل مطعم البركة.. جاري التحضير الآن! مية هلا.")
            order_states[customer_id] = 'confirmed'
            last_customer_mapping.pop("current", None)
            resp.message().body("تم إرسال التأكيد للزبون.")
        
        elif body.startswith("2"): # رفض مع سبب
            reason = body[1:].strip() or "المطعم مزدحم حالياً"
            send_whatsapp_msg(customer_id, f"❌ نعتذر منكم، تم رفض الطلب.\nالسبب: {reason}")
            order_states[customer_id] = 'rejected'
            last_customer_mapping.pop("current", None)
            resp.message().body(f"تم إبلاغ الزبون بالرفض لسبب: {reason}")

        elif body.startswith("3"): # رد مخصص
            custom_msg = body[1:].strip()
            if custom_msg:
                send_whatsapp_msg(customer_id, f"💬 رسالة من إدارة المطعم:\n{custom_msg}")
                resp.message().body("تم إرسال رسالتك للزبون.")
            else:
                resp.message().body("يرجى كتابة الرسالة بعد الرقم 3 (مثال: 3 الطلب سيصل بعد 10 دقائق)")
        
        else:
            resp.message().body("خيارات الكاشير:\n1. تأكيد\n2. رفض (اكتب 2 والسبب)\n3. رد (اكتب 3 والرسالة)")
        return str(resp)

    # --- 2. صمت البوت للزبون المنتظر ---
    if order_states.get(sender) == 'waiting_cashier':
        resp.message().body("طلبكم قيد المراجعة لدى الكاشير.. بنخبركم فور التأكيد! 🙏")
        return str(resp)

    # --- 3. منطق الموقع ---
    if lat and lon:
        dist, fee = calculate_delivery_fee(lat, lon)
        google_link = f"http://maps.google.com/?q={lat},{lon}"
        if fee == -1: body = f"[نظام: خارج التغطية {dist}كم]"
        else: body = f"[نظام: الموقع {google_link} | المسافة {dist}كم | التوصيل {fee} ليرة. اطلب اسم الزبون الآن]"

    # --- 4. استدعاء الذكاء الاصطناعي ---
    try:
        response_text = conversational_rag_chain.invoke(
            {"question": body}, config={"configurable": {"session_id": sender}}
        )
    except Exception as e:
        response_text = "مية هلا، صار ضغط بسيط، ممكن تعيد الطلب؟"

    # --- 5. كشف الفاتورة النهائية ---
    if "[FINAL_CONFIRMATION]" in response_text:
        summary = response_text.split("[FINAL_CONFIRMATION]")[1].strip()
        last_customer_mapping["current"] = sender
        order_states[sender] = 'waiting_cashier'
        
        # رسالة الكاشير (أزرار نصية)
        cashier_menu = (
            f"🔔 طلب جديد:\n{summary}\n\n"
            f"رد برقم الإجراء:\n"
            f"1️⃣ لتأكيد الطلب\n"
            f"2️⃣ للرفض (مثال: 2 المنسف خلص)\n"
            f"3️⃣ للرد على الزبون (مثال: 3 رح نتأخر)"
        )
        send_whatsapp_msg(CASHIER_PHONE, cashier_menu)
        response_text = "يسلموا! تم إرسال الطلب للكاشير للمراجعة.. ثواني وبنأكدلكم. ⏳"

    resp.message().body(response_text)
    return str(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))