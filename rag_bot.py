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

CASHIER_PHONE = os.environ.get("CASHIER_PHONE")

PRICE_PER_KM_TL = 40
MAX_DELIVERY_KM = 50


def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def is_cashier_sender(from_field: str) -> bool:
    if not CASHIER_PHONE:
        return False
    return _digits_only(from_field) == _digits_only(CASHIER_PHONE) and bool(_digits_only(CASHIER_PHONE))


def get_twilio_client():
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        return None
    return Client(sid, token)


def send_summary_to_cashier(summary_text: str) -> bool:
    client = get_twilio_client()
    from_wa = os.environ.get("TWILIO_WHATSAPP_FROM")
    if not client or not from_wa or not CASHIER_PHONE:
        print("Missing TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, or CASHIER_PHONE")
        return False
    to_wa = CASHIER_PHONE if CASHIER_PHONE.strip().lower().startswith("whatsapp:") else f"whatsapp:{CASHIER_PHONE}"
    client.messages.create(body=summary_text, from_=from_wa, to=to_wa)
    return True


print("📚 جاري قراءة منيو مطعم البركة...")
with open("data/menu.txt", "r", encoding="utf-8") as f:
    menu_content = f.read()

print("⚡ جاري الاتصال بمحرك Gemini...")
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.environ.get("GEMINI_API_KEY"),
    temperature=0.2,
)

prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """أنت نادل فلسطيني في مطعم "Al-Baraka" (البركة). مهمتك: كاشير مساعد للعرض الحي.
قواعد صارمة:
1) إيجاز شديد جداً — جمل قصيرة، بدون حشو.
2) خذ الطلب واحسب المجموع: ثمن الأكل من قائمة الطعام فقط + تكلفة التوصيل (40 ليرة تركية لكل كم، حسب المسافة التي يذكرها النظام في رسائل التوصيل).
3) إذا لم يُرسل الموقع بعد، اطلب دبوس الموقع لحساب التوصيل قبل الإغلاق.
4) عند اكتمال الطلب وجاهزية الملخص، اكتب سطراً واحداً بالضبط يبدأ بـ:
ثواني، ببعت طلبك للكاشير ليأكده
5) مباشرة بعد ذلك، في نفس الرد، أضف سطراً يبدأ بالضبط بـ:
[ORDER_SUMMARY]
ثم ملخصاً قصيراً: الأصناف، الكميات، المجموع الغذائي، مسافة التوصيل إن وُجدت، رسوم التوصيل، والإجمالي النهائي.
6) اللغة: عربية فلسطينية محكية للزبائن العرب؛ إن كتب بلغة أخرى رد بلغته باختصار.

قائمة الطعام:
{context}""",
        ),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{question}"),
    ]
)

rag_chain = (
    RunnablePassthrough.assign(context=lambda x: menu_content)
    | prompt
    | llm
    | StrOutputParser()
)

store = {}


def get_session_history(session_id: str):
    if session_id not in store:
        store[session_id] = ChatMessageHistory()
    return store[session_id]


conversational_rag_chain = RunnableWithMessageHistory(
    rag_chain,
    get_session_history,
    input_messages_key="question",
    history_messages_key="history",
)


def calculate_delivery_fee(user_lat, user_lon):
    restaurant_lat = float(os.environ.get("RESTAURANT_LAT", "41.235278"))
    restaurant_lon = float(os.environ.get("RESTAURANT_LON", "28.774333"))

    r_earth_km = 6371.0

    lat1 = math.radians(restaurant_lat)
    lon1 = math.radians(restaurant_lon)
    lat2 = math.radians(float(user_lat))
    lon2 = math.radians(float(user_lon))

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = r_earth_km * c

    if distance > MAX_DELIVERY_KM:
        fee = -1
    else:
        fee = round(distance * PRICE_PER_KM_TL, 2)

    return round(distance, 2), fee


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = request.values.get("Body", "").strip()
    sender_number = request.values.get("From", "unknown")

    latitude = request.values.get("Latitude")
    longitude = request.values.get("Longitude")

    resp = MessagingResponse()

    if is_cashier_sender(sender_number):
        if incoming_msg == "1":
            resp.message().body("Order Confirmed")
        else:
            resp.message().body("Send 1 to confirm an order.")
        return str(resp)

    if latitude and longitude:
        print(f"\n📍 استلام موقع من {sender_number}: {latitude}, {longitude}")
        distance, fee = calculate_delivery_fee(latitude, longitude)

        if fee == -1:
            incoming_msg = (
                f"[رسالة نظام: المسافة {distance} كم — خارج نطاق التوصيل (الحد الأقصى {MAX_DELIVERY_KM} كم). اعتذر بلطف ولا تكمل الطلب للتوصيل لهذا العنوان.]"
            )
        else:
            delivery_tl = fee
            incoming_msg = (
                f"[رسالة نظام: موقع الزبون يبعد {distance} كم. التوصيل {PRICE_PER_KM_TL} ل.ت/كم — المجموع توصيل {delivery_tl} ل.ت. أضفها للفاتورة وأعطِ الإجمالي.]"
            )
    else:
        print(f"\n👤 رسالة من {sender_number}: {incoming_msg}")

    try:
        response_text = conversational_rag_chain.invoke(
            {"question": incoming_msg},
            config={"configurable": {"session_id": sender_number}},
        )
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            response_text = "يا هلا فيك.. في ضغط طلبات كبير حالياً، ممكن تعيد رسالتك بعد دقيقة؟ تكرم عينك!"
            print("⚠️ تحذير: تم تجاوز حد Gemini API.")
        else:
            response_text = "يا هلا، صار في خلل فني بسيط بالنظام. ثواني وبنكون معك!"
            print(f"❌ خطأ غير متوقع: {error_msg}")

    print(f"🤖 رد النادل: {response_text}")

    if "[ORDER_SUMMARY]" in response_text:
        idx = response_text.find("[ORDER_SUMMARY]")
        summary_block = response_text[idx:].strip()
        try:
            send_summary_to_cashier(summary_block)
        except Exception as ex:
            print(f"Twilio forward failed: {ex}")
        response_text = "تم تحويل طلبك للكاشير للمراجعة ⏳"

    msg = resp.message()
    msg.body(response_text)
    return str(resp)


if __name__ == "__main__":
    print("\n🚀 سيرفر مطعم البركة جاهز للعمل السحابي!")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
