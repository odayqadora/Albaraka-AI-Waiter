import asyncio
import asyncpg
import os
from dotenv import load_dotenv

# تحميل المتغيرات للتشغيل المحلي (في Render سيتم قراءتها تلقائياً)
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def init_db():
    if not DATABASE_URL:
        print("Error: DATABASE_URL is not set.")
        return

    print("Connecting to PostgreSQL...")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # إنشاء جدول order_states
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS order_states (
                customer_id TEXT PRIMARY KEY,
                state TEXT
            );
        ''')
        print("Database tables initialized successfully.")
    except Exception as e:
        print(f"Database Initialization Error: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(init_db())