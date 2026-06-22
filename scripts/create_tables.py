import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import Base, engine
import app.models  # noqa: F401 - imports models so metadata is registered


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully.")
