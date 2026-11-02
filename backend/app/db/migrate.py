from pathlib import Path
from alembic.config import Config
from alembic import command
from app.db.database import engine
from app.db.migrations import upgrade_database

if __name__ == "__main__":
    upgrade_database()
    print("Database schema is at the latest revision.")
