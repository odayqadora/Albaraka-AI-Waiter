from langchain_community.llms import Ollama
import arabic_reshaper
from bidi.algorithm import get_display

# 1. الاتصال بالنموذج
llm = Ollama(model="qwen3:8b")

# 2. إعطاء الروبوت شخصيته (الـ Prompt)
prompt = """
أنت نادل ذكي ومرح في مطعم فلسطيني في إسطنبول، وتتحدث بـ "لهجة أهل غزة" الأصلية بدقة.
رحب بالزبون بحرارة باستخدام مصطلحات غزاوية أصيلة مثل (يا هلا يا غالي، حياك الله يا طيب، نورتنا يا حوت، إيش يا عمي).
اقترح عليه تجربة طبق "المسخن" اليوم بطريقة تفتح النفس.
الرد يجب أن يكون قصيراً جداً، دافئاً، ومرحباً، وبلهجة أهل غزة حصراً دون أي كلمات من اللغة العربية الفصحى.
"""

print("⏳ جاري استدعاء النادل... (كرت الشاشة RX 9060 XT يعمل الآن!)")

# 3. استلام الرد من النموذج
response = llm.invoke(prompt)

# 4. إصلاح الحروف العربية لتظهر في شاشة الويندوز السوداء
reshaped_text = arabic_reshaper.reshape(response)  # تشبيك الحروف
bidi_text = get_display(reshaped_text)             # تعديل الاتجاه من اليمين لليسار

print("\n💬 رد النادل:")
print(bidi_text)