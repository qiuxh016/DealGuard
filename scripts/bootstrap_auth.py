from getpass import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.database import SessionLocal  # noqa: E402
from app.db.migrations import upgrade_database  # noqa: E402
from app.db.models import AuthAccount, DemoActor  # noqa: E402
from app.db.seed import seed_demo_data  # noqa: E402
from app.services.actors import hash_password  # noqa: E402


def main() -> None:
    upgrade_database()
    with SessionLocal() as db:
        seed_demo_data(db)
        actors = db.query(DemoActor).order_by(DemoActor.id).all()
        for actor in actors:
            while True:
                password = getpass(f"Set password for {actor.id} ({actor.display_name}), minimum 12 characters: ")
                confirmation = getpass("Confirm password: ")
                if len(password) >= 12 and password == confirmation:
                    break
                print("Passwords must match and contain at least 12 characters.")
            account = db.get(AuthAccount, actor.id)
            if account is None:
                account = AuthAccount(actor_id=actor.id, password_hash=hash_password(password))
                db.add(account)
            else:
                account.password_hash = hash_password(password)
                account.is_active = True
        db.commit()
    print("Authentication accounts are ready. Passwords were not written to project files.")


if __name__ == "__main__":
    main()
