"""Small image helpers shared by the API, the classic RAG and the tools."""

import base64
import io
import uuid
from pathlib import Path

from PIL import Image

from .config import settings


def decode_base64_image(data: str) -> Image.Image:
    """Accepts plain base64 or a 'data:image/png;base64,...' string."""
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
    except Exception as e:
        raise ValueError(f"image_base64 is not a valid image: {e}") from e


def to_base64(image: Image.Image | Path, max_side: int = 1024) -> str:
    """Image → base64 JPEG (resized: small images are faster for the VLM)."""
    img = Image.open(image) if isinstance(image, Path) else image
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


def save_upload(image: Image.Image) -> str:
    """Store a photo sent by the app; returns its id ('upload_xxxx').
    The agent's tools use this id to look at the photo later."""
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    upload_id = f"upload_{uuid.uuid4().hex[:10]}"
    image.save(settings.uploads_dir / f"{upload_id}.jpg", format="JPEG", quality=90)
    return upload_id


def find_image_file(image_id: str) -> Path | None:
    """Path of a document image ('Brochure-EMSI_p3_0') or an upload."""
    if image_id.startswith("upload_"):
        path = settings.uploads_dir / f"{image_id}.jpg"
        return path if path.exists() else None
    for path in settings.images_dir.glob(f"{image_id}.*"):
        return path
    return None


def image_url(file_name: str) -> str:
    """URL path served by FastAPI (see main.py: app.mount('/images', ...))."""
    return f"/images/{file_name}"
