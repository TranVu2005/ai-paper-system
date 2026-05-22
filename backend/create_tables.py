from app.db.session import engine
from app.db.base import Base


print("⏳ Creating tables...")
Base.metadata.create_all(bind=engine)
print("✅ Tables created successfully!")
