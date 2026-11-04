"""Attach bundled showcase catalog images to the persistent showcase workspace."""

from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.migrations import upgrade_database
from app.db.models import Customer, DemoActor, Product
from app.services.media_assets import attach_image


TENANT_ID = "dealguard-showcase-workspace"
ADMIN_ID = "dealguard-showcase-admin"
ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "showcase"
TARGETS = {
    "DG-C-001": (Customer, "customer"),
    "DG-C-002": (Customer, "customer"),
    "DG-C-003": (Customer, "customer"),
    "DG-C-004": (Customer, "customer"),
    "DG-C-005": (Customer, "customer"),
    "P-100": (Product, "product"),
    "P-200": (Product, "product"),
    "P-300": (Product, "product"),
    "P-400": (Product, "product"),
    "P-500": (Product, "product"),
    "P-600": (Product, "product"),
    "P-700": (Product, "product"),
    "P-800": (Product, "product"),
}


def main() -> None:
    upgrade_database()
    attached = 0
    skipped = 0
    with SessionLocal() as db:
        actor = db.scalar(select(DemoActor).where(DemoActor.id == ADMIN_ID, DemoActor.tenant_id == TENANT_ID))
        if actor is None:
            raise SystemExit("Showcase administrator not found in DATABASE_URL; initialize the showcase workspace first.")

        for target_id, (model, _) in TARGETS.items():
            image_path = ASSET_DIR / f"{target_id}.png"
            row = db.scalar(select(model).where(model.id == target_id, model.tenant_id == TENANT_ID)) if model is Customer else db.scalar(select(model).where(model.sku == target_id, model.tenant_id == TENANT_ID))
            if row is None:
                print(f"Skipped {target_id}: catalog item not found.")
                skipped += 1
                continue
            if row.image_asset_id:
                print(f"Skipped {target_id}: image already exists.")
                skipped += 1
                continue
            if not image_path.is_file():
                print(f"Skipped {target_id}: missing asset {image_path.name}.")
                skipped += 1
                continue
            content = image_path.read_bytes()
            image_id = attach_image(db, actor, row, content, "image/png")
            print(f"Attached {image_path.name} to {target_id} ({image_id}).")
            attached += 1

    print(f"Showcase media import complete: {attached} attached, {skipped} skipped.")


if __name__ == "__main__":
    main()
