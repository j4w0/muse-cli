"""Local image attachments for chat.stream; no image conversion or network access."""
import base64
import os
import stat


# Leave room for base64 expansion, JSON and framing under the gateway envelope limit.
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def image_items(paths):
    items, total = [], 0
    for path in paths:
        path = os.path.expanduser(path)
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise ValueError(f"image is not a regular file: {path}")
            with open(path, "rb") as fh:
                info = os.fstat(fh.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"image is not a regular file: {path}")
                if info.st_size > MAX_IMAGE_BYTES - total:
                    raise ValueError("images exceed the 10 MiB combined upload limit; send fewer or smaller images")
                data = fh.read(MAX_IMAGE_BYTES - total + 1)
        except OSError as e:
            raise ValueError(f"cannot read image {path}: {e.strerror}") from e
        if len(data) > MAX_IMAGE_BYTES - total:
            raise ValueError("images exceed the 10 MiB combined upload limit; send fewer or smaller images")
        if not data:
            raise ValueError(f"image is empty: {path}")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise ValueError(f"unsupported image {path}; use PNG, JPEG, GIF or WebP")
        total += len(data)
        items.append({"type": "image", "mime_type": mime,
                      "data_base64": base64.b64encode(data).decode("ascii"),
                      "filename": os.path.basename(path)})
    return items
