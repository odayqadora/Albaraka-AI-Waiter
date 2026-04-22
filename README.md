# 🍽️ Albaraka AI Waiter - FastAPI Edition

**An advanced, multilingual AI agent automating customer service, order taking, and dynamic delivery fee calculations for Albaraka Restaurant via WhatsApp.**

---

## 🟢🚀 TEST IT LIVE RIGHT NOW! (Interactive Demo)
Want to see the bot in action? You can chat with it directly on your WhatsApp!

1. **Add the Number:** Send a WhatsApp message to `+1 (415) 523-8886`.
2. **Enter the Sandbox:** Send this exact secret password: `join goes-after`
3. **Wait 1 Second:** You will receive a quick confirmation message from Twilio saying you are set up.
4. **Start Testing!** * Ask for the menu or try placing an order.
   * Speak in **Arabic** (Levantine dialect), **Turkish**, or **English** to see the context-switching magic.
   * 📍 **Crucial Test:** Send your **WhatsApp Location Pin** to see the Python Haversine formula calculate your delivery fee in real-time!

---

## ✨ Key Features

* 🧠 **Zero Hallucination (Direct Context Injection):** Reads the entire menu directly into Gemini 2.5 Flash's massive context window. This ensures 100% accurate prices and lightning-fast responses without the heavy overhead of vector databases.
* 🎛️ **Cashier Control Panel (Human-in-the-Loop):** Once an order is confirmed by the AI, it pauses the bot and sends a summary to the human cashier's WhatsApp. The cashier can instantly reply with:
  * `1` ➔ Approve the order.
  * `2 [Reason]` ➔ Reject the order (e.g., "2 We are out of Mansaf").
  * `3 [Message]` ➔ Send a custom message to the customer.
* 🗺️ **Geospatial Delivery Calculation:** Integrates the Haversine formula in Python to automatically calculate precise delivery distances and fees (40 TL/km) based on WhatsApp Location Pins shared by users. Rejects orders over 50 km automatically.
* 🛒 **Conversational Memory:** Maintains session-based memory using individual phone numbers, allowing the bot to intuitively accumulate orders and calculate final totals accurately.
* 🗣️ **Multilingual & Persona-Driven:** Dynamically switches between languages based on user input, while maintaining the authentic, warm "Abu Ali" (Levantine/Palestinian) cashier persona.

## 🛠️ Tech Stack

* **Framework:** FastAPI & Uvicorn (High-performance async backend)
* **Core AI:** LangChain, Google Gemini 2.5 Flash
* **Math & Logic:** Python (Haversine formula for geospatial logic)
* **Integration:** Twilio Sandbox API (WhatsApp Webhooks)
* **Deployment:** Ready for Cloud Hosting (Render, VPS, etc.)

## 💡 How It Works Under the Hood

1. **User Input:** A customer sends a text message or drops a location pin via WhatsApp.
2. **Webhook Intercept:** The FastAPI server intercepts the Twilio webhook asynchronously.
3. **Geospatial Logic:** If a location is detected, Python calculates the straight-line distance to the restaurant and appends a hidden system prompt with the exact delivery fee.
4. **AI Processing:** The user's query, the conversation history, and the full menu text are passed securely to Gemini.
5. **Response Generation:** Gemini processes the context to generate a highly accurate response. 
6. **Cashier Handoff:** Once the final invoice is approved by the customer, the system parses the `[FINAL_CONFIRMATION]`, mutes the AI, and alerts the human cashier to take action via WhatsApp.

---
**Developed to revolutionize the restaurant ordering experience.**

<img width="1195" height="1528" alt="555aeae9-cd5e-4829-be4e-415a4fecac1b" src="https://github.com/user-attachments/assets/a5fb8b69-f93f-4d5c-aeca-a5d6cfccff73" />

<img width="1200" height="1283" alt="01e5b003-d5ad-4a6f-aea3-d90c65fe0194" src="https://github.com/user-attachments/assets/a82000f6-73df-45c1-849b-0ad4ea71bb6a" />

<img width="600" height="440" alt="Screenshot 2026-04-22 234807" src="https://github.com/user-attachments/assets/be0d9590-ad51-4423-a545-f6c6d9fd7347" />



