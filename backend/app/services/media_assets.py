from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DemoActor, MediaAsset


MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


async def read_image(file: UploadFile) -> tuple[bytes, str]:
    content = await file.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail={"code": "IMAGE_EMPTY", "message": "图片文件不能为空。"})
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail={"code": "IMAGE_TOO_LARGE", "message": "图片不能超过 5 MiB。"})

    content_type = next((mime for signature, mime in IMAGE_SIGNATURES if content.startswith(signature)), None)
    if content_type is None and len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        content_type = "image/webp"
    if content_type is None:
        raise HTTPException(status_code=415, detail={"code": "IMAGE_FORMAT_UNSUPPORTED", "message": "仅支持 PNG、JPEG 或 WebP 图片。"})
    return content, content_type


def attach_image(db: Session, actor: DemoActor, entity, content: bytes, content_type: str) -> str:
    asset_id = create_image_asset(db, actor, content, content_type)
    old_id = entity.image_asset_id
    entity.image_asset_id = asset_id
    db.flush()
    if old_id:
        old_asset = db.scalar(select(MediaAsset).where(
            MediaAsset.id == old_id, MediaAsset.tenant_id == actor.tenant_id,
        ))
        if old_asset:
            db.delete(old_asset)
    db.commit()
    return asset_id


def create_image_asset(db: Session, actor: DemoActor, content: bytes, content_type: str) -> str:
    asset_id = str(uuid4())
    db.add(MediaAsset(
        id=asset_id, tenant_id=actor.tenant_id, uploaded_by=actor.id,
        content_type=content_type, content=content,
    ))
    db.flush()
    return asset_id


def remove_image(db: Session, actor: DemoActor, entity) -> None:
    old_id = entity.image_asset_id
    entity.image_asset_id = None
    db.flush()
    if old_id:
        old_asset = db.scalar(select(MediaAsset).where(
            MediaAsset.id == old_id, MediaAsset.tenant_id == actor.tenant_id,
        ))
        if old_asset:
            db.delete(old_asset)
    db.commit()
