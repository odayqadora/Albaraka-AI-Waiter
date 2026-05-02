import os
import math
import re
import json
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.messages import HumanMessage, AIMessage

app = FastAPI(
    title="مطعم البركة — واتساب",
    description="Webhook Twilio + RAG (Gemini) للطلبات والتوصيل.",
    version="2.0.0",
)

# ─────────────────────────────────────────────────────────────
# ENVIRONMENT VARIABLES
# ─────────────────────────────────────────────────────────────
CASHIER_PHONE         = os.environ.get("CASHIER_PHONE")
TWILIO_WHATSAPP_FROM  = os.environ.get("TWILIO_WHATSAPP_FROM")
ACCOUNT_SID           = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN            = os.environ.get("TWILIO_AUTH_TOKEN")
PRICE_PER_KM_TL       = 40
MAX_DELIVERY_KM       = 50
DB_PATH               = "albaraka_state.db"

# ─────────────────────────────────────────────────────────────
# FIX #4 — Twilio client: initialise ONCE globally, reuse everywhere
# ─────────────────────────────────────────────────────────────
if ACCOUNT_SID and AUTH_TOKEN:
    twilio_client = Client(ACCOUNT_SID, AUTH_TOKEN)
else:
    twilio_client = None
    print("⚠️  WARNING: Twilio credentials missing — send_whatsapp_msg will be disabled.")


# ─────────────────────────────────────────────────────────────
# FIX #3 — SQLite persistence layer
#           Replaces in-memory dicts that were lost on restart
# ─────────────────────────────────────────────────────────────
def _db_conn() -> sqlite3.Connection:
    """Return a thread-safe SQLite connection."""
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db() -> None:
    """Create all tables if they don't already exist."""
    with _db_conn() as conn:
        conn.executescript("""
            -- Per-customer order state (waiting_cashier / confirmed / rejected)
            CREATE TABLE IF NOT EXISTS order_states (
                phone      TEXT PRIMARY KEY,
                state      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- FIX #1 — pending_orders keyed by last-4 digits
            --           Supports multiple simultaneous pending orders
            CREATE TABLE IF NOT EXISTS pending_orders (
                last4      TEXT PRIMARY KEY,
                phone      TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Serialised LangChain chat history (survives server restarts)
            CREATE TABLE IF NOT EXISTS chat_history (
                session_id TEXT PRIMARY KEY,
                messages   TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)


# ── order_states helpers ──────────────────────────────────────
def get_order_state(phone: str) -> str | None:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT state FROM order_states WHERE phone = ?", (phone,)
        ).fetchone()
    return row[0] if row else None


def set_order_state(phone: str, state: str) -> None:
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO order_states (phone, state, updated_at) VALUES (?, ?, ?)",
            (phone, state, datetime.utcnow().isoformat()),
        )


def delete_order_state(phone: str) -> None:
    with _db_conn() as conn:
        conn.execute("DELETE FROM order_states WHERE phone = ?", (phone,))


# ── pending_orders helpers (FIX #1) ──────────────────────────
def add_pending_order(phone: str) -> str:
    """Store phone keyed by its last 4 digits. Returns last4."""
    last4 = _digits_only(phone)[-4:]
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_orders (last4, phone, created_at) VALUES (?, ?, ?)",
            (last4, phone, datetime.utcnow().isoformat()),
        )
    return last4


def get_pending_phone(last4: str) -> str | None:
    """Resolve last-4-digit key → full customer phone."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT phone FROM pending_orders WHERE last4 = ?", (last4,)
        ).fetchone()
    return row[0] if row else None


def remove_pending_order(last4: str) -> None:
    with _db_conn() as conn:
        conn.execute("DELETE FROM pending_orders WHERE last4 = ?", (last4,))


def list_pending_orders() -> dict[str, str]:
    """Return {last4: phone} for all currently pending orders."""
    with _db_conn() as conn:
        rows = conn.execute("SELECT last4, phone FROM pending_orders").fetchall()
    return {r[0]: r[1] for r in rows}


# ── chat_history helpers ─────────────────────────────────────
class SQLiteChatHistory(ChatMessageHistory):
    """
    ChatMessageHistory backed by SQLite.
    Loads existing messages on construction; persists on every add_message call.
    """

    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id
        self._load()

    # ── private ──────────────────────────────────────────────
    def _load(self) -> None:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT messages FROM chat_history WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
        if not row:
            return
        try:
            for m in json.loads(row[0]):
                if m.get("type") == "human":
                    self.messages.append(HumanMessage(content=m["content"]))
                elif m.get("type") == "ai":
                    self.messages.append(AIMessage(content=m["content"]))
        except Exception as e:
            print(f"⚠️  Failed to load chat history for {self.session_id}: {e}")

    def _save(self) -> None:
        serialised = []
        for m in self.messages:
            if isinstance(m, HumanMessage):
                serialised.append({"type": "human", "content": m.content})
            elif isinstance(m, AIMessage):
                serialised.append({"type": "ai", "content": m.content})
        with _db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chat_history (session_id, messages, updated_at) VALUES (?, ?, ?)",
                (self.session_id, json.dumps(serialised, ensure_ascii=False),
                 datetime.utcnow().isoformat()),
            )

    # ── override so every new message is immediately persisted ─
    def add_message(self, message) -> None:
        super().add_message(message)
        self._save()


# In-memory cache: avoids re-instantiating SQLiteChatHistory on every request
_history_cache: dict[str, SQLiteChatHistory] = {}


def get_session_history(session_id: str) -> SQLiteChatHistory:
    if session_id not in _history_cache:
        _history_cache[session_id] = SQLiteChatHistory(session_id)
    return _history_cache[session_id]


# ─────────────────────────────────────────────────────────────
# FIX #5 — menu.txt: safe loading with fallback
# ─────────────────────────────────────────────────────────────
MENU_FALLBACK = (
    "⚠️  قائمة الطعام غير متوفرة مؤقتاً. يرجى التواصل مع الكاشير مباشرة."
)

try:
    with open("menu.txt", "r", encoding="utf-8") as _f:
        menu_content = _f.read()
    print("✅ menu.txt loaded successfully.")
except FileNotFoundError:
    menu_content = MENU_FALLBACK
    print("❌ ERROR: menu.txt not found — using fallback menu text.")
except Exception as _e:
    menu_content = MENU_FALLBACK
    print(f"❌ ERROR reading menu.txt: {_e} — using fallback menu text.")


# ─────────────────────────────────────────────────────────────
# LLM + CHAIN
# ─────────────────────────────────────────────────────────────
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.3,
)

SYSTEM_PROMPT = """You are the smart AI assistant for "Al-Baraka Restaurant" (مطعم البركة). Your job is to welcome customers, present the menu, take orders, ask for special notes, and confirm delivery details — all with professionalism and warmth.

━━━━━━━━━━━━━━━━━━━━━━━━
🌐 LANGUAGE DETECTION — CRITICAL RULE
━━━━━━━━━━━━━━━━━━━━━━━━
You MUST detect the customer's language from their VERY FIRST message and reply ONLY in that language throughout the entire conversation.

- If the customer writes in ARABIC → reply ONLY in Arabic (Palestinian/Levantine dialect, formal and polite)
- If the customer writes in TURKISH → reply ONLY in Turkish (formal and polite)
- If the customer writes in ENGLISH → reply ONLY in English (formal and polite)

NEVER mix languages. NEVER default to Arabic if the customer wrote in Turkish or English.
If you cannot detect the language clearly, respond in all three languages briefly and ask which they prefer.

━━━━━━━━━━━━━━━━━━━━━━━━
📋 ORDER FLOW (follow this exact sequence)
━━━━━━━━━━━━━━━━━━━━━━━━
Step 1 → Greet the customer warmly
Step 2 → Take their order (items from the menu only)
Step 3 → Ask: "Do you have any special notes or requests for your order?" (in their language)
         - Accept reasonable notes (no onions, extra sauce, allergy requests, etc.)
         - Politely decline unreasonable notes (requests unrelated to food, offensive requests, impossible demands) with a brief, kind explanation
Step 4 → Ask for their delivery location (WhatsApp location pin or address)
Step 5 → Ask for their name
Step 6 → Show the full invoice (items + prices + delivery fee + total)
Step 7 → Ask for "تأكيد" / "confirm" / "onayla" to finalize

━━━━━━━━━━━━━━━━━━━━━━━━
🗣️ TONE & VOCABULARY
━━━━━━━━━━━━━━━━━━━━━━━━
Arabic: Use warm, formal Palestinian/Levantine expressions.
  ✅ Allowed: أهلاً وسهلاً، تفضل، شرفتنا، بكل سرور، تكرم، جاهزين لخدمتك
  ❌ Forbidden: يابا، على راسي، يا غالي، حبيب قلبي، معلم (too informal/excessive)
  ❌ Forbidden: dry formal Arabic (الفصحى الجافة)
  Always speak as "نحن" (we)

Turkish: Formal, polite. E.g. "Buyurun", "Memnuniyetle", "Teşekkür ederiz"
English: Formal, polite. E.g. "Welcome!", "Of course", "We'd be happy to help"

━━━━━━━━━━━━━━━━━━━━━━━━
🧾 FINAL CONFIRMATION FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━
When the customer confirms, include this block verbatim at the END of your message:

[FINAL_CONFIRMATION]
الاسم: (customer name)
الرقم: [تعديل: اترك هذا الحقل فارغاً، النظام سيتكفل بوضعه بناءً على رقم المرسل]
النوع: (توصيل / حجز)
الأصناف: (items and prices)
الملاحظات: (special notes, or "لا يوجد")
الموقع: (maps link)
توصيل: (fee)
المجموع: (total) ليرة تركية.

━━━━━━━━━━━━━━━━━━━━━━━━
📌 OTHER RULES & GUARDRAILS (تم إضافة حواجز الحماية هنا)
━━━━━━━━━━━━━━━━━━━━━━━━
- Only take orders from the menu. If an item is not on the menu, apologize and suggest the closest alternative.
- For out-of-range delivery, escalate to human cashier smoothly.
- Never break character or mention that you are an AI.

[إضافة: منع الخروج عن النص وإجابة الأسئلة العامة]
- ROLE BOUNDARIES: You are strictly a restaurant assistant. NEVER answer questions unrelated to the restaurant, food, menu, or delivery. If asked to write code, solve math, or discuss general topics, politely decline and redirect the conversation back to the menu.

[إضافة: منع اختراع الأسعار أو إعطاء خصومات وهمية]
- STRICT PRICING: NEVER invent items, guess prices, or offer unauthorized discounts. You must ONLY use the exact items and prices provided in the MENU.

[إضافة: التعامل مع تغيير الزبون لرأيه]
- ORDER MODIFICATIONS: If the customer changes their mind (adds/removes items), ALWAYS recalculate and confirm the new total before proceeding to checkout.

MENU:
{context}"""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])

rag_chain = (
    RunnablePassthrough.assign(context=lambda x: menu_content)
    | prompt
    | llm
    | StrOutputParser()
)

conversational_rag_chain = RunnableWithMessageHistory(
    rag_chain,
    get_session_history,
    input_messages_key="question",
    history_messages_key="history",
)


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def is_cashier_sender(from_field: str) -> bool:
    if not CASHIER_PHONE:
        return False
    return _digits_only(from_field) == _digits_only(CASHIER_PHONE)


# FIX #4 — reuse the single global twilio_client
def send_whatsapp_msg(to_phone: str, message: str) -> bool:
    if not twilio_client:
        print("⚠️  Twilio client not initialised — cannot send message.")
        return False
    try:
        to_wa = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"
        twilio_client.messages.create(
            body=message,
            from_=TWILIO_WHATSAPP_FROM,
            to=to_wa,
        )
        return True
    except Exception as e:
        print(f"❌ Twilio send error: {e}")
        return False


def calculate_delivery_fee(user_lat, user_lon) -> tuple[float, float]:
    r_lat, r_lon = 41.235278, 28.774333
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(
        math.radians, [r_lat, r_lon, float(user_lat), float(user_lon)]
    )
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = R * 2 * math.asin(math.sqrt(a))
    if distance > MAX_DELIVERY_KM:
        return round(distance, 2), -1
    return round(distance, 2), round(distance * PRICE_PER_KM_TL, 2)


# ─────────────────────────────────────────────────────────────
# FIX #2 — Cashier command parser
#
# New format:  <action> <last4> [optional text]
#   1 4567              → confirm order for customer ending in 4567
#   2 4567 المطعم مزدحم → reject with reason
#   3 4567 سيصل بعد 10د → send custom message
#   list                → show all pending orders
# ─────────────────────────────────────────────────────────────
def parse_cashier_command(body: str) -> tuple[str | None, str | None, str]:
    """
    Returns (action, last4, extra_text).
    action ∈ {'1','2','3','list'} or None if unrecognised.
    last4  is the 4-digit customer identifier, or None if missing/invalid.
    extra  is the trailing text (reason / message), may be empty string.
    """
    parts = body.strip().split(None, 2)   # at most 3 tokens
    if not parts:
        return None, None, ""

    action = parts[0]

    if action == "list":
        return "list", None, ""

    if action not in ("1", "2", "3"):
        return None, None, ""

    if len(parts) < 2:
        return action, None, ""          # action recognised but last4 missing

    last4 = parts[1]
    if not last4.isdigit() or len(last4) != 4:
        return action, None, ""          # last4 present but invalid format

    extra = parts[2] if len(parts) > 2 else ""
    return action, last4, extra


CASHIER_HELP = (
    "📋 أوامر الكاشير (مع آخر 4 أرقام من رقم الزبون):\n"
    "1️⃣ XXXX — تأكيد الطلب\n"
    "   مثال: 1 4567\n\n"
    "2️⃣ XXXX السبب — رفض مع ذكر السبب\n"
    "   مثال: 2 4567 المنسف خلص\n\n"
    "3️⃣ XXXX الرسالة — إرسال رسالة مخصصة\n"
    "   مثال: 3 4567 الطلب سيصل بعد 10 دقائق\n\n"
    "list — عرض جميع الطلبات المعلقة"
)


# ─────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    init_db()
    print("✅ SQLite database initialised.")


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────
@app.get("/")
async def home():
    return {"status": "Al-Baraka Smart System Online (FastAPI v2 — Production)"}


@app.post("/whatsapp")
async def whatsapp_reply(request: Request):
    form_data = await request.form()
    body      = form_data.get("Body", "").strip()
    sender    = form_data.get("From", "")
    lat       = form_data.get("Latitude")
    lon       = form_data.get("Longitude")

    resp = MessagingResponse()

    # ── 1. CASHIER DASHBOARD ─────────────────────────────────
    if is_cashier_sender(sender):
        action, last4, extra = parse_cashier_command(body)

        # Show all pending orders
        if action == "list":
            pending = list_pending_orders()
            if not pending:
                resp.message().body("لا توجد طلبات معلقة حالياً.")
            else:
                lines = [f"🔔 الطلبات المعلقة ({len(pending)}):"]
                for l4 in pending:
                    lines.append(f"  • آخر 4 أرقام: {l4}")
                lines.append("\nاستخدم الرقم للإجراء — مثال: 1 " + list(pending.keys())[0])
                resp.message().body("\n".join(lines))
            return Response(content=str(resp), media_type="application/xml")

        # Unrecognised command → show help
        if action is None:
            resp.message().body(CASHIER_HELP)
            return Response(content=str(resp), media_type="application/xml")

        # Action recognised but last4 missing or malformed
        if last4 is None:
            resp.message().body(
                "⚠️ يرجى إضافة آخر 4 أرقام من رقم الزبون بعد الأمر.\n"
                f"مثال: {action} 4567\n\n"
                "اكتب 'list' لعرض الطلبات المعلقة."
            )
            return Response(content=str(resp), media_type="application/xml")

        # Resolve last4 → full customer phone
        customer_id = get_pending_phone(last4)
        if not customer_id:
            resp.message().body(
                f"⚠️ لم يُعثر على طلب معلق للرقم المنتهي بـ {last4}.\n"
                "اكتب 'list' لعرض الطلبات المعلقة."
            )
            return Response(content=str(resp), media_type="application/xml")

        # ── FIX #1: all actions now operate on the correct customer ──
        if action == "1":
            send_whatsapp_msg(
                customer_id,
                "✅ تم تأكيد طلبكم من قبل المطعم.. جاري التحضير الآن! أهلاً وسهلاً فيك.",
            )
            set_order_state(customer_id, "confirmed")
            remove_pending_order(last4)
            resp.message().body(f"✅ تم إرسال التأكيد للزبون ...{last4}")

        elif action == "2":
            reason = extra or "المطعم مزدحم حالياً"
            send_whatsapp_msg(
                customer_id,
                f"❌ نعتذر منكم، تم رفض الطلب.\nالسبب: {reason}",
            )
            set_order_state(customer_id, "rejected")
            remove_pending_order(last4)
            resp.message().body(f"تم إبلاغ الزبون ...{last4} بالرفض.\nالسبب: {reason}")

        elif action == "3":
            if not extra:
                resp.message().body(
                    "يرجى كتابة الرسالة بعد رقم الزبون.\n"
                    f"مثال: 3 {last4} الطلب سيصل بعد 10 دقائق"
                )
            else:
                send_whatsapp_msg(
                    customer_id,
                    f"💬 رسالة من إدارة المطعم:\n{extra}",
                )
                resp.message().body(f"تم إرسال رسالتك للزبون ...{last4}")

        return Response(content=str(resp), media_type="application/xml")

    # ── 2. SILENCE BOT FOR WAITING CUSTOMER ─────────────────
    if get_order_state(sender) == "waiting_cashier":
        resp.message().body(
            "طلبكم قيد المراجعة لدى الإدارة.. بنخبركم فور التأكيد! بكل سرور."
        )
        return Response(content=str(resp), media_type="application/xml")

    # ── 3. LOCATION HANDLING ─────────────────────────────────
    if lat and lon:
        dist, fee = calculate_delivery_fee(lat, lon)
        google_link = f"http://maps.google.com/?q={lat},{lon}"
        if fee == -1:
            body = (
                f"[SYSTEM: Customer location is out of delivery range — {dist}km away. "
                "Please inform the customer politely and offer to escalate to the cashier.]"
            )
        else:
            body = (
                f"[SYSTEM: Customer location received — {google_link} | "
                f"Distance: {dist}km | Delivery fee: {fee} TL. "
                "Now ask for the customer's name.]"
            )

    # ── 4. CALL AI ───────────────────────────────────────────
    try:
        response_text = conversational_rag_chain.invoke(
            {"question": body},
            config={"configurable": {"session_id": sender}},
        )
    except Exception as e:
        print(f"❌ LLM Error: {e}")
        response_text = (
            "أهلاً وسهلاً فيك، نواجه ضغطاً بسيطاً في النظام، "
            "هل يمكنك إعادة إرسال الطلب لو سمحت؟"
        )

    # ── 5. DETECT FINAL CONFIRMATION ─────────────────────────
    if "[FINAL_CONFIRMATION]" in response_text:
        parts    = response_text.split("[FINAL_CONFIRMATION]", 1)
        # FIX #6 — guard against empty pre-confirmation text
        pre_text = parts[0].strip()
        summary  = parts[1].strip() if len(parts) > 1 else ""

        if pre_text:
            response_text = pre_text + "\n\nتكرم! تم إرسال الطلب للإدارة للمراجعة.. ثواني وبنأكدلكم."
        else:
            response_text = "تكرم! تم إرسال طلبكم للإدارة للمراجعة.. ثواني وبنأكدلكم."

        # FIX #1 — store by last4, not by overwriting a single "current" key
        last4 = add_pending_order(sender)
        set_order_state(sender, "waiting_cashier")

        # FIX #2 — cashier message now shows last4 so cashier knows which customer
        cashier_msg = (
            f"🔔 طلب جديد — آخر 4 أرقام: *{last4}*\n"
            f"{'─'*30}\n"
            f"{summary}\n"
            f"{'─'*30}\n"
            f"للرد استخدم آخر 4 الأرقام ({last4}):\n"
            f"1️⃣ {last4} — تأكيد الطلب\n"
            f"2️⃣ {last4} السبب — رفض مع سبب\n"
            f"3️⃣ {last4} الرسالة — رد مخصص\n"
            f"list — عرض كل الطلبات المعلقة"
        )
        send_whatsapp_msg(CASHIER_PHONE, cashier_msg)

    resp.message().body(response_text)
    return Response(content=str(resp), media_type="application/xml")


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("rag_bot:app", host="0.0.0.0", port=10000, reload=True)
