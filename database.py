import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# 1. Load environment variables from the .env file
load_dotenv()

# 2. Get the database URL from the environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set.")

# 3. Create the SQLAlchemy engine
engine = create_engine(DATABASE_URL)

# 4. Create a session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 5. Create a declarative base for your models
# Your ORM models will inherit from this class.
Base = declarative_base()

# 6. Dependency to get a DB session for each request
def get_db():
    """
    FastAPI dependency that provides a SQLAlchemy database session.
    It ensures the session is always closed after the request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

  # ---- End of database.py ----
