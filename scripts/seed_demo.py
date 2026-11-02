import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.database import SessionLocal
from app.db.migrations import upgrade_database
from app.db.seed import seed_demo_data

upgrade_database()
with SessionLocal() as db:
    seed_demo_data(db)
print("DealGuard demo data is ready.")
