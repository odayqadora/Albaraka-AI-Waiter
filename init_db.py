import sqlite3

# Connect to the database (it will be created if it doesn't exist)
conn = sqlite3.connect('bot.db')

# Create a cursor object
c = conn.cursor()

# Create the order_states table
# Stores the current state of an order for a given customer
# States could be: 'waiting_cashier', 'confirmed', 'rejected'
c.execute('''
    CREATE TABLE IF NOT EXISTS order_states (
        customer_id TEXT PRIMARY KEY,
        state TEXT NOT NULL
    )
''')

# Create the customer_mapping table
# Used to map a key (like "current") to the last customer who placed an order
# This is useful for the cashier to interact with the last pending order
c.execute('''
    CREATE TABLE IF NOT EXISTS customer_mapping (
        key TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL
    )
''')

# Commit the changes and close the connection
conn.commit()
conn.close()

print("Database 'bot.db' initialized successfully with 'order_states' and 'customer_mapping' tables.")
