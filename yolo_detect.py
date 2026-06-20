"""YOLO-World open-vocabulary helmet detection.

Stock YOLO (COCO) has no "helmet" class and we have no training data, so we use
YOLO-World, which detects arbitrary objects from text prompts. We just tell it
to look for a helmet and return the best box in the same blob-tuple format the
navigation code already uses: (cx, cy, area, (x, y, w, h)).
"""

import os

# Text prompts describing the target. More phrasings = more recall.
PROMPTS = ["helmet", "yellow helmet", "hard hat", "safety helmet"]

# Confidence floor. Top-down tiny helmets score low, so keep this permissive
# and let the navigation loop rely on the highest-scoring box.
DEFAULT_CONF = float(os.environ.get("HELMET_CONF", "0.05"))

_MODEL = None


def _get_model():
    """Lazily load YOLO-World once (weights auto-download on first use)."""
    global _MODEL
    if _MODEL is None:
        from ultralytics import YOLOWorld
        _MODEL = YOLOWorld("yolov8s-worldv2.pt")
        _MODEL.set_classes(PROMPTS)
    return _MODEL


def detect_helmet(img_bgr, conf=DEFAULT_CONF):
    """Run YOLO-World and return the highest-confidence helmet blob, or None.

    Blob format matches vision._largest_blob:
        (cx, cy, area, (x, y, w, h))
    """
    model = _get_model()
    result = model.predict(img_bgr, conf=conf, verbose=False)[0]
    if result.boxes is None or len(result.boxes) == 0:
        return None

    # Pick the most confident detection.
    best = max(result.boxes, key=lambda b: float(b.conf))
    x1, y1, x2, y2 = best.xyxy[0].tolist()
    w, h = x2 - x1, y2 - y1
    cx, cy = x1 + w / 2.0, y1 + h / 2.0
    area = w * h
    return cx, cy, area, (int(x1), int(y1), int(w), int(h))
