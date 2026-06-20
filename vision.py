"""Color-based detection for the rescue-drone demo.

The drone flies with the gimbal pointed straight down (-90°), so frames are a
top-down view of the floor. We detect two things by HSV color:

  * the YELLOW helmet  -> the rescue target
  * the BLUE carpet    -> the "catastrophe area"

Everything is plain OpenCV color thresholding so it stays fast and dependency
light, and is easy to retune from a saved frame.
"""

import base64
import os

import cv2
import numpy as np

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class NoFrameError(Exception):
    """The backend returned a 'no frame available' response, not an image."""

# --- HSV color ranges (OpenCV H is 0..179) -------------------------------
# Tune these against a real saved frame.png if lighting differs.
YELLOW_LOWER = np.array([20, 90, 90])
YELLOW_UPPER = np.array([35, 255, 255])

BLUE_LOWER = np.array([95, 80, 50])
BLUE_UPPER = np.array([130, 255, 255])

# Ignore tiny specks of color (noise) below this many pixels.
MIN_AREA = 150


def _to_raw_image_bytes(data):
    """Normalize a frame payload to raw JPEG/PNG bytes.

    Handles three shapes the backend may return:
      * raw JPEG/PNG bytes (passthrough)
      * a base64 string/bytes of the image
      * a ``data:image/...;base64,<...>`` data URI
    Returns the best-guess raw bytes; the caller still validates by decoding.
    """
    if isinstance(data, str):
        data = data.encode("ascii", "ignore")
    s = data.strip()

    # data: URI -> keep only the part after the comma
    if s[:5] == b"data:":
        comma = s.find(b",")
        if comma != -1:
            s = s[comma + 1:]

    # Already a real image -> use as-is.
    if s.startswith(_JPEG_MAGIC) or s.startswith(_PNG_MAGIC):
        return s

    # Otherwise try base64; accept it only if it yields a real image header.
    try:
        decoded = base64.b64decode(s, validate=False)
        if decoded.startswith(_JPEG_MAGIC) or decoded.startswith(_PNG_MAGIC):
            return decoded
    except Exception:
        pass
    return s


def decode_frame(img_bytes):
    """Decode a drone frame payload into a BGR image array.

    Accepts raw JPEG/PNG, base64, or data-URI payloads. Tries OpenCV, then PIL.
    Raises ValueError with a byte-signature diagnostic if nothing decodes.
    """
    if not img_bytes:
        raise NoFrameError("camera returned no frame yet (empty payload)")

    # The backend returns a JSON error body (e.g. {"detail": "No frame ..."})
    # when the camera isn't streaming. Recognize it instead of trying to decode.
    head = img_bytes.lstrip()[:1] if isinstance(img_bytes, (bytes, bytearray)) else b""
    if head == b"{":
        try:
            text = bytes(img_bytes).decode("utf-8", "ignore")
        except Exception:
            text = repr(img_bytes[:80])
        raise NoFrameError(f"camera reported no frame: {text}")

    raw = _to_raw_image_bytes(img_bytes)

    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is not None:
        return img

    # Fallback: decode via PIL, then convert RGB -> BGR for OpenCV.
    try:
        import io
        from PIL import Image
        pil = Image.open(io.BytesIO(raw)).convert("RGB")
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    except Exception as e:
        head = bytes(img_bytes[:16])
        raise ValueError(
            f"Could not decode frame ({len(img_bytes)} bytes, "
            f"first16={head!r}): {e}"
        )


def _largest_blob(mask):
    """Return (cx, cy, area, (x, y, w, h)) of the biggest blob, or None."""
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)
    )
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < MIN_AREA:
        return None
    x, y, w, h = cv2.boundingRect(c)
    cx, cy = x + w / 2.0, y + h / 2.0
    return cx, cy, area, (x, y, w, h)


def _detect(img_bgr, lower, upper):
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    return _largest_blob(mask)


def detect_helmet_color(img_bgr):
    """Detect the yellow helmet by HSV color. Returns blob tuple or None."""
    return _detect(img_bgr, YELLOW_LOWER, YELLOW_UPPER)


# Toggle YOLO off with HELMET_USE_YOLO=0 to fall back to pure color detection.
USE_YOLO = os.environ.get("HELMET_USE_YOLO", "1").strip().lower() not in {
    "0", "false", "no", "off"
}


def detect_helmet(img_bgr):
    """Detect the helmet, preferring YOLO-World, falling back to color.

    YOLO-World handles a small / desaturated helmet far better than HSV
    thresholding. If YOLO is unavailable (import/model error) or disabled,
    we fall back to the color detector.
    """
    if USE_YOLO:
        try:
            import yolo_detect
            blob = yolo_detect.detect_helmet(img_bgr)
            if blob is not None:
                return blob
        except Exception as e:  # model load / inference failure
            print(f"[vision] YOLO unavailable ({e}); using color detection.")
    return detect_helmet_color(img_bgr)


def detect_carpet(img_bgr):
    """Detect the blue catastrophe-area carpet. Returns blob tuple or None."""
    return _detect(img_bgr, BLUE_LOWER, BLUE_UPPER)


def normalized_offset(blob, img_shape):
    """Offset of a blob's center from the image center, in [-1, 1].

    Returns (dx, dy, fill) where:
      dx < 0 -> target is to the LEFT,  dx > 0 -> to the RIGHT
      dy < 0 -> target is FORWARD/up,   dy > 0 -> behind/down
      fill   -> blob area as a fraction of the whole frame (proximity proxy)
    """
    h, w = img_shape[:2]
    cx, cy, area, _ = blob
    dx = (cx - w / 2.0) / (w / 2.0)
    dy = (cy - h / 2.0) / (h / 2.0)
    fill = area / float(w * h)
    return dx, dy, fill


def annotate(img_bgr, helmet=None, carpet=None):
    """Draw detection boxes for debugging; returns a copy."""
    out = img_bgr.copy()
    if carpet is not None:
        x, y, w, h = carpet[3]
        cv2.rectangle(out, (x, y), (x + w, y + h), (255, 0, 0), 2)
        cv2.putText(out, "carpet", (x, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
    if helmet is not None:
        x, y, w, h = helmet[3]
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 255), 2)
        cv2.putText(out, "helmet", (x, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    return out
