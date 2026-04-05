# 🍽️ Albaraka AI Waiter 
**An advanced, multilingual AI agent automating customer service, order taking, and dynamic delivery fee calculations for Albaraka Restaurant via WhatsApp.**

---

## 🟢🚀 TEST IT LIVE RIGHT NOW! (Interactive Demo)
Want to see the bot in action? You can chat with it directly on your WhatsApp!

1. **Add the Number:** Send a WhatsApp message to `+1 (415) 523-8886`.
2. **Enter the Sandbox:** Send this exact secret password: `join teeth-forget`
3. **Wait 1 Second:** You will receive a quick confirmation message from Twilio saying you are set up.
4. **Start Testing!** * Ask for the menu or try placing an order.
   * Speak in **Arabic** (Levantine dialect), **Turkish**, or **English** to see the context-switching magic.
   * 📍 **Crucial Test:** Send your **WhatsApp Location Pin** to see the Python Haversine formula calculate your delivery fee in real-time!

---

## ✨ Key Features

* 🧠 **Zero Hallucination (Direct Context Injection):** Reads the entire menu directly into Gemini 2.5 Flash's massive context window. This ensures 100% accurate prices and lightning-fast responses without the heavy overhead of vector databases.
* 🗺️ **Geospatial Delivery Calculation:** Integrates the Haversine formula in Python to automatically calculate precise delivery distances and fees based on WhatsApp Location Pins (Latitude/Longitude) shared by users.
* 🛒 **Conversational Memory:** Maintains session-based memory using individual phone numbers, allowing the bot to intuitively accumulate orders and calculate final totals accurately.
* 🗣️ **Multilingual & Persona-Driven:** Dynamically switches between Arabic (authentic Levantine/Palestinian dialect), Turkish, and English based on the user's input, while maintaining a strictly professional and culturally appropriate persona.
* 🤝 **Human-in-the-Loop:** Automatically escalates out-of-range delivery requests or payment processing to a human cashier smoothly.

## 🛠️ Tech Stack

* **Core AI:** LangChain, Google Gemini 2.5 Flash
* **Backend & Math:** Python, Flask (Haversine formula for geospatial logic)
* **Integration:** Twilio Sandbox API (WhatsApp Webhooks)
* **Deployment:** Render (Cloud Hosting)

## 💡 How It Works Under the Hood

1. **User Input:** A customer sends a text message or drops a location pin via WhatsApp.
2. **Webhook Intercept:** The Flask server intercepts the Twilio webhook.
3. **Geospatial Logic:** **If a location is detected**, Python calculates the straight-line distance to the restaurant and appends a hidden system prompt with the exact delivery fee.
4. **AI Processing:** The user's query, the conversation history, and the full menu text are passed securely to Gemini.
5. **Response Generation:** Gemini processes the context and hidden instructions to generate a highly accurate, persona-aligned response back to the user's WhatsApp.

---
**Developed to revolutionize the restaurant ordering experience.**
