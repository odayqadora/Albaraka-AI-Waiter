import os
import math
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

# تحويل الكود إلى خادم ويب
app = Flask(__name__)

# 1. قراءة المنيو (الاعتماد على الذاكرة الكاملة لـ Gemini بدلاً من قواعد البيانات المعقدة)
print("📚 جاري قراءة منيو مطعم البركة...")
with open("data/menu.txt", "r", encoding="utf-8") as f:
    menu_content = f.read()

# 2. العقل المدبر (Gemini)
print("⚡ جاري الاتصال بمحرك Gemini...")
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    google_api_key=os.environ.get("GEMINI_API_KEY"),
    temperature=0.2
)

# 3. البرومبت المحدث (شخصية فلسطينية حقيقية، اقتصاد شديد في الكلام، ذاكرة، وإرسال الموقع)
prompt = ChatPromptTemplate.from_messages([
    ("system", """أنت نادل فلسطيني شهم ومحترف في "مطعم البركة".
تعليمات صارمة جداً لأسلوبك:
1. استخرج الإجابة والأسعار حصراً من قائمة الطعام المرفقة.
2. الإيجاز الشديد (كلمة ورد غطاها): أجب بإجابات قصيرة جداً ومباشرة. لا تطل في الترحيب أو الشرح. أعطِ الزبون ما يطلبه فوراً بدون حشو كلام.
3. اللغات واللهجة (قاعدة ذهبية):
   - إذا تحدث بالعربية: إياك والتحدث بالعربية الفصحى. تحدث حصراً باللهجة الفلسطينية اليومية المحكية، باختصار شديد ودفء. (استخدم عبارات مثل: يا هلا، تكرم عينك، على راسي، للأسف ما عندنا، طلبك جاهز).
   - إذا تحدث بالتركية: أجب بلغة تركية مهذبة ومختصرة جداً (Kısa ve kibar).
   - إذا تحدث بالإنجليزية: أجب بلغة إنجليزية ودودة ومختصرة جداً (Short and welcoming).
4. تذكر طلبات الزبون السابقة لكي تستطيع حساب الإجمالي بدقة.
5. رسائل النظام المخفية (التوصيل): إذا استلمت رسالة عن التوصيل، أضف المبلغ فوراً، ورد باختصار شديد (مثال: "موقعك وصلنا ، المسافة كذا وتوصيلها كذا، حسابك الكلي صار كذا").
6. الدفع: لا تملك IBAN. إذا سأل عن الدفع، قل له باختصار: "الكاشير بيتواصل معك فوراً يبعتلك الرابط".
7. الموقع (Location): إذا سأل الزبون عن مكانكم، موقعكم، أو عنوانكم، قم فوراً بإرسال هذا النص المختصر بلغته مع الرابط:
   - بالعربية: "يا هلا فيك، مطعمنا في باشاك شهير، تفضل اللوكيشن: https://maps.app.goo.gl/xYzAbC123"
   - بالتركية: "Hoş geldiniz, Başakşehir'deyiz. Konumumuz: https://maps.app.goo.gl/xYzAbC123"
   - بالإنجليزية: "Welcome! We are in Başakşehir. Here is our location: https://maps.app.goo.gl/xYzAbC123"

قائمة الطعام:
{context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}")
])

# 4. بناء السلسلة الذكية (حقن المنيو مباشرة)
rag_chain = (
    RunnablePassthrough.assign(context=lambda x: menu_content)
    | prompt
    | llm
    | StrOutputParser()
)

# 5. مستودع الذكريات (لكل رقم هاتف ذاكرة مستقلة)
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

# 6. دالة حساب المسافة (معادلة هافيرسين الرياضية)
def calculate_delivery_fee(user_lat, user_lon):
    # 📍 إحداثيات مطعم البركة الفلسطيني (باشاك شهير)
    restaurant_lat = 41.235278
    restaurant_lon = 28.774333
    
    R = 6371.0 # نصف قطر الأرض بالكيلومترات
    
    lat1 = math.radians(restaurant_lat)
    lon1 = math.radians(restaurant_lon)
    lat2 = math.radians(float(user_lat))
    lon2 = math.radians(float(user_lon))
    
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c # المسافة بالكيلومتر
    
    if distance <= 3:
        fee = 0
    elif distance <= 7:
        fee = 50
    elif distance <= 15:
        fee = 100
    else:
        fee = -1 
        
    return round(distance, 1), fee

# 7. نقطة استقبال رسائل الواتساب
@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = request.values.get('Body', '').strip()
    sender_number = request.values.get('From', 'unknown')
    
    latitude = request.values.get('Latitude')
    longitude = request.values.get('Longitude')
    
    if latitude and longitude:
        print(f"\n📍 استلام موقع من {sender_number}: {latitude}, {longitude}")
        distance, fee = calculate_delivery_fee(latitude, longitude)
        
        if fee == -1:
            incoming_msg = f"[رسالة نظام مخفية: الزبون أرسل موقعه ويبعد {distance} كم. هذه مسافة بعيدة جداً. اعتذر منه بلطف وأخبره أن الكاشير سيتواصل معه لتحديد إمكانية التوصيل وتكلفتها]."
        elif fee == 0:
            incoming_msg = f"[رسالة نظام مخفية: الزبون أرسل موقعه ويبعد {distance} كم. التوصيل مجاني! أضف 0 ليرة للفاتورة وأخبر الزبون]."
        else:
            incoming_msg = f"[رسالة نظام مخفية: الزبون أرسل موقعه ويبعد {distance} كم. تكلفة التوصيل هي {fee} ليرة. أضف هذا المبلغ لفاتورته السابقة وأعطه المجموع النهائي]."
            
    else:
        print(f"\n👤 رسالة من {sender_number}: {incoming_msg}")
    
    response_text = conversational_rag_chain.invoke(
        {"question": incoming_msg},
        config={"configurable": {"session_id": sender_number}} 
    )
    
    print(f"🤖 رد النادل: {response_text}")
    
    resp = MessagingResponse()
    msg = resp.message()
    msg.body(response_text)
    
    return str(resp)

if __name__ == "__main__":
    print("\n🚀 سيرفر مطعم البركة جاهز للعمل السحابي!")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)