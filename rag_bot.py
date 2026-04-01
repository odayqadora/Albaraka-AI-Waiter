import os
import math
import re
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

app = Flask(__name__)

# جلب الإعدادات من Render
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

def send_summary_to_cashier(summary_text: str):
    if not all([ACCOUNT_SID, AUTH_TOKEN, TWILIO_WHATSAPP_FROM, CASHIER_PHONE]):
        print("❌ خطأ: بيانات Twilio أو رقم الكاشير ناقصة في إعدادات Render!")
        return False
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        to_wa = CASHIER_PHONE if CASHIER_PHONE.startswith("whatsapp:") else f"whatsapp:{CASHIER_PHONE}"
        client.messages.create(body=summary_text, from_=TWILIO_WHATSAPP_FROM, to=to_wa)
        print(f"✅ تم إرسال الملخص بنجاح للكاشير: {to_wa}")
        return True
    except Exception as e:
        print(f"❌ فشل إرسال الرسالة للكاشير: {e}")
        return False

with open("data/menu.txt", "r", encoding="utf-8") as f:
    menu_content = f.read()

# التبديل لموديل 1.5-flash لحل مشكلة توقف البوت عن الرد
llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash", 
    google_api_key=os.environ.get("GEMINI_API_KEY"),
    temperature=0.1,
)

# تصحيح الجملة المقطوعة في البرومبت وتحسين ترتيب العرض
prompt = ChatPromptTemplate.from_messages([
    ("system", """أنت مساعد كاشير فلسطيني ذكي في مطعم "البركة". 
مهمتك جمع الطلب بدقة وحساب الفاتورة النهائية.
يستطيع الزبون طلب الطعام او حجز مكان في المطعم.
انت تتحدث العربية والانجليزية والتركية بلباقة وأدب.
عندما يطلب الزبون وجبة اعرض عليه مقبلات مع الوجبة او مشروب او الاثنين معا حسب الوجبة التي يطلبها وتأكد انها في المنيو.
اسأل الزبون عن تفاصيل التوصيل (الموقع) أو الحجز (الوقت والأشخاص) إذا لم يذكرها.

قواعد الحساب (إجبارية):
1) احسب سعر الأكل من المنيو.
2) أضف سعر التوصيل (40 ليرة لكل كيلومتر) للمجموع.
3) يجب أن تذكر المجموع النهائي بوضوح (سعر الأكل + التوصيل = المجموع الكلي).

عند الانتهاء تماماً، اسأله عن اسمه لتسجيل الطلبية على اسمه ثم اعرض الطلب عليه للموافقة.
بعد موافقته النهائية، اتبع هذا التنسيق حرفياً:
ثواني، ببعت طلبك للكاشير ليأكده
[ORDER_SUMMARY]
الاسم: (اسم الزبون)
النوع: (توصيل / حجز)
الأصناف: (اذكرها مع أسعارها)
الموقع: (ضع رابط الخرائط المرسل لك إن وجد)
تكلفة التوصيل: (المسافة * 40)
المجموع النهائي: (مجموع الأكل + التوصيل) ليرة تركية.

المنيو:
{context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])

rag_chain = (RunnablePassthrough.assign(context=lambda x: menu_content) | prompt | llm | StrOutputParser())
store = {}

def get_session_history(session_id: str):
    if session_id not in store: 
        store[session_id] = ChatMessageHistory()
    # تفريغ الذاكرة القديمة لتفادي خطأ امتلاء التوكنز
    if len(store[session_id].messages) > 10:
        store[session_id].clear()
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
def home(): return "Al-Baraka AI is online!", 200

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    body = request.values.get("Body", "").strip()
    sender = request.values.get("From", "")
    lat, lon = request.values.get("Latitude"), request.values.get("Longitude")
    resp = MessagingResponse()

    if is_cashier_sender(sender):
        if body == "1": resp.message().body("✅ تم تأكيد الطلب بنجاح!")
        else: resp.message().body("أهلاً كاشير عدي. أرسل 1 لتأكيد الطلب.")
        return str(resp)

    if lat and lon:
        dist, fee = calculate_delivery_fee(lat, lon)
        google_link = f"https://www.google.com/maps?q={lat},{lon}"
        if fee == -1: 
            body = f"[نظام: المسافة {dist}كم - خارج نطاق التوصيل. اعتذر منه]"
        else: 
            body = f"[نظام: الموقع {google_link} | المسافة {dist}كم | التوصيل {fee} ليرة. احسب المجموع الآن واعرضه على الزبون مع طلب اسمه]"

    try:
        response_text = conversational_rag_chain.invoke(
            {"question": body}, 
            config={"configurable": {"session_id": sender}}
        )
    except Exception as e:
        print(f"❌ Error: {e}")
        response_text = "يا هلا.. صار ضغط بسيط ع النظام، ممكن تبعت رسالتك كمان مرة؟"

    if "[ORDER_SUMMARY]" in response_text:
        try:
            summary_data = response_text.split("[ORDER_SUMMARY]")[1].strip()
            final_summary = f"🔔 طلب/حجز جديد من {sender.replace('whatsapp:', '')}:\n{summary_data}"
            send_summary_to_cashier(final_summary)
            response_text = "تم تحويل طلبك للكاشير للمراجعة، ثواني وبأكدلك إياه! ⏳"
        except IndexError:
            pass

    resp.message().body(response_text)
    return str(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))