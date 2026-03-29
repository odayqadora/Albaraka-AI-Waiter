from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
import math

# تحويل الكود إلى خادم ويب
app = Flask(__name__)

# 1. قراءة المنيو وبناء قاعدة البيانات
print("📚 جاري قراءة منيو مطعم البركة...")
loader = TextLoader("data/menu.txt", encoding="utf-8")
docs = loader.load()

text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
final_documents = text_splitter.split_documents(docs)

print("🧠 جاري بناء قاعدة البيانات...")
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = FAISS.from_documents(final_documents, embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 6})

# 2. العقل المدبر (Gemini)
print("⚡ جاري الاتصال بمحرك Gemini...")
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    google_api_key=os.environ.get("GEMINI_API_KEY"),
    temperature=0.2
)

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

# 3. البرومبت المحدث (متعدد اللغات ولهجة عربية بيضاء ويحتوي على الذاكرة وضبط اللهجة الصارم)
prompt = ChatPromptTemplate.from_messages([
    ("system", """أنت نادل محترف وراقي في "مطعم البركة" للمأكولات الفلسطينية.
تعليمات هامة وصارمة جداً:
1. استخرج الإجابة والأسعار حصراً من قائمة الطعام المرفقة.
2. قدم إجابات قصيرة ومباشرة. اذكر 3 أو 4 خيارات مميزة فقط مع أسعارها إذا سألك عن قسم كامل.
3. اللغات واللهجة (قاعدة ذهبية): أجب الزبون بنفس اللغة التي يتحدث بها:
   - إذا تحدث بالعربية: أجب بلهجة عربية "بيضاء" (مفهومة ولبقة لجميع الجنسيات العربية). كن ودوداً ومضيافاً، وتجنب الكلمات المحلية المعقدة أو المبالغة في الترحيب.
   - إذا تحدث بالتركية: أجب بلغة تركية رسمية، صحيحة، ومهذبة جداً (Kibar ve profesyonel bir dil kullan).
   - إذا تحدث بالإنجليزية: أجب بلغة إنجليزية احترافية، دافئة، ولبقة (Polite, welcoming, and professional).
4. تذكر طلبات الزبون السابقة في المحادثة لكي تستطيع حساب الإجمالي بدقة.
5. إذا تلقيت "رسالة نظام مخفية" تخبرك بتكلفة التوصيل، قم فوراً بإضافة هذا المبلغ إلى إجمالي الفاتورة، وأخبر الزبون بلباقة (بلغته) أن موقعه قد وصلنا، وأن المسافة كذا، وتكلفة التوصيل كذا، والمجموع النهائي كذا.
6. أنت لا تمتلك رقم حساب (IBAN). إذا طلب الزبون الدفع، أخبره (بلغته) أن الكاشير سيتواصل معه فوراً لإرسال رابط الدفع.

قائمة الطعام:
{context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}")
])

# 4. بناء السلسلة الذكية
rag_chain = (
    RunnablePassthrough.assign(context=(lambda x: format_docs(retriever.invoke(x["question"]))))
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
    
    # تحديد السعر بناءً على المسافة المقطوعة (يمكنكم تعديل التسعيرة لاحقاً حسب سياسة المطعم)
    if distance <= 3:
        fee = 0 # توصيل مجاني للمناطق القريبة جداً
    elif distance <= 7:
        fee = 50
    elif distance <= 15:
        fee = 100
    else:
        fee = -1 # خارج نطاق التوصيل التلقائي
        
    return round(distance, 1), fee

# 7. نقطة استقبال رسائل الواتساب
@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = request.values.get('Body', '').strip()
    sender_number = request.values.get('From', 'unknown')
    
    latitude = request.values.get('Latitude')
    longitude = request.values.get('Longitude')
    
    # 🎯 إذا أرسل الزبون "لوكيشن" من الواتساب
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
    
    # تمرير الرسالة (أو رسالة النظام المخفية) للعقل المدبر
    response_text = conversational_rag_chain.invoke(
        {"question": incoming_msg},
        config={"configurable": {"session_id": sender_number}} 
    )
    
    print(f"🤖 رد النادل: {response_text}")
    
    resp = MessagingResponse()
    msg = resp.message()
    msg.body(response_text)
    
    return str(resp)

import os

if __name__ == "__main__":
    print("\n🚀 سيرفر مطعم البركة جاهز للعمل السحابي!")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)