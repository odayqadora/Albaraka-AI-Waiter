# test_ai_brain.py
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables from .env file before importing the bot's modules
load_dotenv()

# Re-encode stdout to handle emojis
import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from rag_bot import conversational_rag_chain

# --- Test Configuration ---
TEST_SESSION_ID = "whatsapp:+905550001122"
CLEAN_SENDER_ID = TEST_SESSION_ID.replace("whatsapp:", "")

# The sequence of simulated user messages
CONVERSATION_STEPS = [
    "مرحبا، شو في عندكم أكل؟",
    "بدي مسخن لو سمحت",
    "[نظام: الموقع http://maps.google.com/041.0,28.0 | المسافة 5كم | التوصيل 200 ليرة. اطلب اسم الزبون الآن]",
    "اسمي عدي",
    "تأكيد"
]

async def run_test():
    """Runs the simulated conversation and prints the results."""
    print("--- Starting AI Brain Test ---")
    print(f"User ID: {TEST_SESSION_ID}\n")

    # Check if the API key is loaded
    if not os.getenv("GOOGLE_API_KEY"):
        print("🛑 Error: GOOGLE_API_KEY not found.")
        print("Please make sure you have a .env file in the same directory with GOOGLE_API_KEY=your_key")
        return

    for i, step in enumerate(CONVERSATION_STEPS):
        print(f"--- Step {i+1} ---")
        print(f"User: {step}")

        # Invoke the chain with the user's message and session ID for memory
        response = await conversational_rag_chain.ainvoke(
            {
                "question": step,
                "sender": CLEAN_SENDER_ID
            },
            config={"configurable": {"session_id": TEST_SESSION_ID}}
        )

        print(f"AI: {response}\n")

    print("--- Test Complete ---")

if __name__ == "__main__":
    # Ensure the environment is set up for async execution
    asyncio.run(run_test())
