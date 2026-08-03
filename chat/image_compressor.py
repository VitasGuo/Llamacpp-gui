"""聊天图片压缩工具。"""
import base64
from io import BytesIO

from PIL import Image, ImageOps

MAX_IMAGE_SIDE = 768
JPEG_QUALITY = 70


def compress_image_data_url(data_url):
    """把 data URL 图片压缩为 JPEG data URL；无法处理时返回 None。"""
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        return None
    try:
        _, encoded = data_url.split(",", 1)
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                output = background
            else:
                output = image.convert("RGB")
            buffer = BytesIO()
            output.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
