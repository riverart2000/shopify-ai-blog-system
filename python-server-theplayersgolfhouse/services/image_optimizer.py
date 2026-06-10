import io
from PIL import Image

def optimize_image(img_bytes: bytes, max_width: int = 1200, max_height: int = 1200) -> bytes:
    """
    Optimizes image by resizing to fit within max_width x max_height while preserving
    aspect ratio, converting it to WebP format, and applying compression.
    Returns optimized bytes.
    """
    try:
        img = Image.open(io.BytesIO(img_bytes))
        
        # Keep transparency if formatting/converting, but for webp convert to RGBA first
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
            
        # Resize if width or height exceeds maximum constraints
        width, height = img.size
        if width > max_width or height > max_height:
            ratio = min(max_width / width, max_height / height)
            new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            
        # Save to bytes in WebP format
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=80, method=6)
        return out.getvalue()
    except Exception:
        # Fallback to original bytes if optimization fails
        return img_bytes
