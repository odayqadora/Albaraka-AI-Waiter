import os
import math
import re
import requests
import speech_recognition as sr
from io import BytesIO
from pydub import AudioSegment
from flask import Flask, request, render_template_string
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
BASE_URL = os.environ.get("BASE_URL") # 💡 رابط السيرفر (Render URL) بدون "/"

PRICE_PER_KM_TL = 40
MAX_DELIVERY_KM = 50

# مخزن الحالات (يفضل Redis للإنتاج)
order_states = {} 
last_customer_mapping = {}

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
        print(f"❌ Error send_msg: {e}")
        return False

# 💡 دالة معالجة الرسائل الصوتية وتحويلها لنص
def process_audio(audio_url):
    print(f"🎤 جارٍ معالجة رسالة صوتية من: {audio_url}")
    try:
        # تحميل الملف الصوتي من Twilio
        response = requests.get(audio_url, auth=(ACCOUNT_SID, AUTH_TOKEN))
        audio_data = BytesIO(response.content)
        
        # تحويل ملف OGG (تنسيق واتساب) إلى WAV باستخدام pydub (يحتاج ffmpeg)
        ogg_audio = AudioSegment.from_file(audio_data, format="ogg")
        wav_data = BytesIO()
        ogg_audio.export(wav_data, format="wav")
        wav_data.seek(0) # إعادة المؤشر لبداية الملف
        
        # التعرف على الكلام باستخدام محرك جوجل المجاني
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_data) as source:
            audio_content = recognizer.record(source)
            # التعرف على اللغة العربية المحكية
            text = recognizer.recognize_google(audio_content, language="ar-SA")
            print(f"✅ تم تحويل الصوت لنص: {text}")
            return text
            
    except sr.UnknownValueError:
        print("❌ لم يتمكن المحرك من فهم الصوت.")
        return "[نظام: لم أفهم الرسالة الصوتية، أرجو الإعادة بوضوح يا غالي]"
    except Exception as e:
        print(f"❌ خطأ في معالجة الصوت: {e}")
        return "[نظام: عطل فني في معالجة الصوت]"

with open("data/menu.txt", "r", encoding="utf-8") as f:
    menu_content = f.read()

llm = ChatGroq(
    groq_api_key=os.environ.get("GROQ_API_KEY"),
    model_name="llama-3.3-70b-versatile",
    temperature=0.1,
)

# 💡 تحديث البرومبت لإرسال رابط المنيو بدلاً من النص الطويل
menu_link = f"{BASE_URL}/menu" # 💡 رابط المنيو الحي

prompt = ChatPromptTemplate.from_messages([
    ("system", f"""أنت المساعد الذكي لـ "مطعم البركة". تحدث دائماً بصيغة الجمع "نحن".

قواعد الحوار:
1. اللغات: أجب بلغة ولهجة الزبون (فلسطيني، تركي، سوري، إنجليزي).
2. تدفق الطلب: الأصناف -> الموقع -> الاسم (أخيراً) -> اعرض الفاتورة.
3. المنيو (هام جداً): إذا طلب الزبون المنيو أو القائمة، لا تشرحها له أبداً. فقط أرسل له هذا الرابط بالضبط: {menu_link} وقُل له "تفضلوا المنيو الحي لمطعمنا يا غالي، شوفوه وأخبرونا شو بتحبوا تطلبوا!".
4. الفاتورة: تشمل الأصناف، اسم الطلبية، سعر التوصيل، المجموع.
5. التأكيد: اطلب "تأكيد" لإرسال الطلب.

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

# --- 💡 صفحة المنيو الحي (Live Web Menu) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>منيو مطعم البركة الحي 🍔</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f9f9f9; color: #333; padding: 20px; }
        .container { max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
        h1 { text-align: center; color: #e67e22; border-bottom: 2px solid #e67e22; padding-bottom: 10px; }
        .menu-content { white-space: pre-wrap; line-height: 1.8; font-size: 1.1em; color: #555; }
        .footer { text-align: center; margin-top: 30px; font-size: 0.9em; color: #888; }
    </style>
</head>
<body>
    <div class="container">
        <h1>منيو مطعم البركة الحي 🍔</h1>
        <div class="menu-content">{{ menu_content }}</div>
        <div class="footer">تفضلوا بزيارتنا! مطعم البركة، إسطنبول</div>
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def home(): return "Al-Baraka Smart Platform Online", 200

# 💡 رابط صفحة المنيو الجديد
@app.route("/menu", methods=["GET"])
def render_menu():
    try:
        with open("data/menu.txt", "r", encoding="utf-8") as f:
            content = f.read()
        return render_template_string(HTML_TEMPLATE, menu_content=content)
    except Exception as e:
        return f"❌ خطأ في تحميل المنيو: {e}", 500

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    global last_customer_for_cashier
    body = request.values.get("Body", "").strip()
    sender = request.values.get("From", "")
    lat, lon = request.values.get("Latitude"), request.values.get("Longitude")
    media_url = request.values.get("MediaUrl0") # 💡 رابط ملف الصوت من Twilio
    media_content_type = request.values.get("MediaContentType0") # 💡 نوع ملف الميديا

    resp = MessagingResponse()

    # --- 💡 التعامل مع الرسائل الصوتية ---
    if media_url and media_content_type and media_content_type.startswith("audio/"):
        voice_text = process_audio(media_url)
        body = voice_text # تمرير النص المستخرج من الصوت للبوت كأنه رسالة مكتوبة

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