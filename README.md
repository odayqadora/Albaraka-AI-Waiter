# Albaraka AI Waiter - Intelligent WhatsApp Agent

An advanced, multilingual AI agent built for "Albaraka Restaurant" to automate customer service, order taking, and delivery fee calculation via WhatsApp. 

## Key Features

* **RAG-Powered Menu Retrieval:** Utilizes FAISS vector database to strictly retrieve menu items and prices, completely eliminating AI hallucinations.
* **Geospatial Delivery Calculation:** Integrates the Haversine formula to automatically calculate precise delivery distances and fees based on WhatsApp Location Pins (Latitude/Longitude) shared by users.
* **Conversational Memory:** Maintains session-based memory using individual phone numbers, allowing the bot to accumulate orders and calculate final totals accurately.
* **Multilingual & Persona Driven:** Dynamically switches between Arabic, Turkish, and English based on the user's input, while maintaining a strictly professional and culturally appropriate persona.
* **Human-in-the-Loop:** Automatically escalates out-of-range delivery requests or payment processing to a human cashier smoothly.

## Tech Stack

* **Core AI:** LangChain, Gemini 2.5 Flash, HuggingFace Embeddings (`all-MiniLM-L6-v2`)
* **Vector Store:** FAISS (Facebook AI Similarity Search)
* **Backend:** Python, Flask
* **Integration:** Twilio API (WhatsApp)
* **Deployment:** Render (Cloud Hosting)

## 💡 How It Works

1. Customer sends a message or drops a location pin via WhatsApp.
2. The Flask server intercepts the Twilio webhook.
3. If a location is detected, Python calculates the straight-line distance to the restaurant and appends a hidden system prompt with the calculated fee.
4. The user's query is embedded and matched against the local `menu.txt` using FAISS.
5. Gemini processes the context, the user's history, and the hidden system prompts to generate a highly accurate, persona-aligned response.
