import os
import math
import re
from fastapi import FastAPI, Request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from langchain_google_genai import ChatGoogleGenerativeAI  # requires GOOGLE_API_KEY in env
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

app = FastAPI(
    title="مطعم البركة — واتساب",
    description="Webhook Twilio + RAG (Gemini) للطلبات والتوصيل.",
    version="1.0.0",
)

# الإعدادات
CASHIER_PHONE = os.environ.get("CASHIER_PHONE")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")
ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

PRICE_PER_KM_TL = 40
MAX_DELIVERY_KM = 50

# مخزن الحالات (يفضل Redis للإنتاج لاحقاً)
order_states = {}
last_customer_mapping = {}  # لربط الكاشير بآخر زبون أرسل طلباً

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

# قراءة المنيو
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
menu_path = os.path.join(BASE_DIR, "data", "menu.txt")
with open(menu_path, "r", encoding="utf-8") as f:
    menu_content = f.read()

# تهيئة Gemini 2.5 Flash
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.3,
)

prompt = ChatPromptTemplate.from_messages([
    ("system", """أنت المساعد الذكي لـ "مطعم البركة". مهمتك استقبال الزبائن، تقديم قائمة الطعام، وأخذ الطلبات باحترافية عالية.
رقم واتساب الزبون الحالي هو: {sender} — لا تسأله عن رقمه أبداً، هو مسجل تلقائياً في النظام.
تحدث دائماً بصيغة الجمع "نحن".

روابط المنيو — أرسل الرابط المناسب حسب لغة الزبون تلقائياً عند طلب المنيو:
- إذا تحدث بالعربية: https://baraka-restoran.com/ar/?branch=baraka-restoran
- إذا تحدث بالإنجليزية: https://baraka-restoran.com/en/?branch=baraka-restoran
- إذا تحدث بالتركية: https://baraka-restoran.com/tr/?branch=baraka-restoran

قواعد الحوار:
1. اللغات: أجب بلغة عربية فلسطينية اللهجة ولا تستعمل العربية الفصحى اطلاقا (فلسطيني، تركي، إنجليزي).
2. النبرة (للعربية): لهجة فلسطينية/شامية رسمية، مهذبة، ولبقة.
3. مفردات إجبارية: استخدم (أهلاً وسهلاً فيك، تفضل، شرفتنا، بكل سرور، تكرم، جاهزين لخدمتك، لو سمحت).
4. مفردات ممنوعة قطعيًا: الفصحى الجافة، والكلمات الشعبية المبالغ فيها مثل (يابا، على راسي، حبيب قلبي، معلم).
5. التدفق: اطلب الأصناف -> اطلب الموقع -> اطلب الاسم (أخيراً) -> اعرض الفاتورة.
6. الفاتورة: يجب أن تشمل (الأصناف وأسعارها، سعر التوصيل، المجموع النهائي).
7. التأكيد: اطلب من الزبون كتابة "تأكيد" لإرسال الطلب للكاشير.

عند موافقة الزبون، اتبع هذا التنسيق حرفياً في نهاية رسالتك:
[FINAL_CONFIRMATION]
الاسم: (اسم الزبون)
الرقم: (مسجل تلقائياً من واتساب)
النوع: (توصيل / حجز)
الأصناف: (التفاصيل والأسعار)
الموقع: (رابط الخرائط)
توصيل: (السعر)
المجموع: (الإجمالي) ليرة تركية.

المنيو: {context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])

# ✅ التعديل الرئيسي: rag_chain يمرر context و sender معاً للـ prompt
rag_chain = (
    RunnablePassthrough.assign(
        context=lambda x: menu_content,
        sender=lambda x: x.get("sender", "")
    )
    | prompt
    | llm
    | StrOutputParser()
)

store = {}

def get_session_history(session_id: str):
    if session_id not in store: store[session_id] = ChatMessageHistory()
    return store[session_id]

conversational_rag_chain = RunnableWithMessageHistory(
    rag_chain, get_session_history, input_messages_key="question", history_messages_key="history"
)

def calculate_delivery_fee(user_lat, user_lon):
    r_lat, r_lon = 41.235278, 28.774333  # إحداثيات مطعم البركة
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
async def whatsapp_reply(request: Request):
    form_data = await request.form()
    body = form_data.get("Body", "").strip()
    sender = form_data.get("From", "")
    lat = form_data.get("Latitude")
    lon = form_data.get("Longitude")

    # رقم الزبون نظيف بدون "whatsapp:"
    clean_sender = sender.replace("whatsapp:", "")

    resp = MessagingResponse()

    # --- 1. لوحة تحكم الكاشير ---
    if is_cashier_sender(sender):
        customer_id = last_customer_mapping.get("current")
        if not customer_id:
            resp.message().body("لا توجد طلبات معلقة حالياً.")
            return Response(content=str(resp), media_type="application/xml")

        if body == "1":  # تأكيد
            send_whatsapp_msg(customer_id, "✅ تم تأكيد طلبكم من قبل المطعم.. جاري التحضير الآن! أهلاً وسهلاً فيك.")
            order_states[customer_id] = 'confirmed'
            last_customer_mapping.pop("current", None)
            resp.message().body("تم إرسال التأكيد للزبون.")

        elif body.startswith("2"):  # رفض مع سبب
            reason = body[1:].strip() or "المطعم مزدحم حالياً"
            send_whatsapp_msg(customer_id, f"❌ نعتذر منكم، تم رفض الطلب.\nالسبب: {reason}")
            order_states[customer_id] = 'rejected'
            last_customer_mapping.pop("current", None)
            resp.message().body(f"تم إبلاغ الزبون بالرفض لسبب: {reason}")

        elif body.startswith("3"):  # رد مخصص
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
    if order_states.get(sender) == 'waiting_cashier':
        resp.message().body("طلبكم قيد المراجعة لدى الإدارة.. بنخبركم فور التأكيد! بكل سرور.")
        return Response(content=str(resp), media_type="application/xml")

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
        # ✅ التعديل: إرسال sender مع question
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

        # ✅ إضافة رقم الواتساب تلقائياً في رسالة الكاشير
        summary = f"📱 رقم الواتساب: {clean_sender}\n" + summary

        last_customer_mapping["current"] = sender
        order_states[sender] = 'waiting_cashier'

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

    resp.message().body(response_text)
    return Response(content=str(resp), media_type="application/xml")

# للتشغيل المحلي:
#   uvicorn rag_bot:app --host 0.0.0.0 --port 10000 --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_bot:app", host="0.0.0.0", port=10000, reload=True)