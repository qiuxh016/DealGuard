from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.services.media_assets import read_image

MAX_IMPORT_IMAGE_FILES = 40
MAX_IMPORT_IMAGE_TOTAL_BYTES = 30 * 1024 * 1024


async def read_import_images(files: list[UploadFile] | None) -> dict[str, tuple[str, bytes, str]]:
    if not files:
        return {}
    if len(files) > MAX_IMPORT_IMAGE_FILES:
        raise HTTPException(status_code=413, detail={
            "code": "IMPORT_TOO_MANY_IMAGES",
            "message": f"每次最多关联 {MAX_IMPORT_IMAGE_FILES} 张图片。",
        })

    result: dict[str, tuple[str, bytes, str]] = {}
    total_bytes = 0
    for file in files:
        filename = Path(file.filename or "").name
        key = Path(filename).stem.strip().upper()
        if not filename or not key:
            raise HTTPException(status_code=422, detail={
                "code": "IMPORT_IMAGE_FILENAME_INVALID",
                "message": "图片文件名必须包含用于匹配的客户编号或 SKU。",
            })
        if key in result:
            raise HTTPException(status_code=422, detail={
                "code": "IMPORT_IMAGE_FILENAME_DUPLICATE",
                "message": f"图片文件名冲突：{key}。每个编号只能关联一张图片。",
            })
        content, content_type = await read_image(file)
        total_bytes += len(content)
        if total_bytes > MAX_IMPORT_IMAGE_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail={
                "code": "IMPORT_IMAGES_TOO_LARGE",
                "message": "导入图片总大小不能超过 30 MiB。",
            })
        result[key] = (filename, content, content_type)
    return result


def image_preview(rows: list[dict], images: dict[str, tuple[str, bytes, str]], key_name: str) -> tuple[list[dict], list[str]]:
    matched = set()
    preview_rows = []
    for row in rows:
        key = (row.get(key_name) or "").upper()
        image = images.get(key)
        if image and not row.get("errors"):
            matched.add(key)
        preview_rows.append({**row, "image_filename": image[0] if image and not row.get("errors") else None})
    unmatched = [filename for key, (filename, _, _) in images.items() if key not in matched]
    return preview_rows, unmatched
