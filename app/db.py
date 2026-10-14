import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL)
# expire_on_commit=False: objects stay readable after commit (we log them afterwards).
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
