from app.db.session import engine

def test_connection():
    try:
        connection = engine.connect()
        print("✅ Connected to PostgreSQL successfully!")
        connection.close()
    except Exception as e:
        print("❌ Database connection failed")
        print(e)

if __name__ == "__main__":
    test_connection()
