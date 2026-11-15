"""Screenshot encoding stays under the SDK message-buffer budget (regression for the 1 MB JSON overrun)."""
from __future__ import annotations

import base64

from PIL import Image

from jarvis.computer.engine import images


def _busy(w, h):
    # a high-entropy image (worst case for PNG; ~what a dense webpage screenshot looks like)
    import random
    random.seed(1)
    img = Image.new("RGB", (w, h))
    img.putdata([(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for _ in range(w * h)])
    return img


def test_fit_encode_is_jpeg_and_under_budget():
    img = _busy(1920, 1200)
    data, mime, scale, size = images.fit_encode(img, max_bytes=700_000, max_dim=1568)
    assert mime == "image/jpeg"
    assert len(data) <= 700_000
    assert len(base64.b64encode(data)) < 1_048_576          # base64 fits under the SDK's default 1 MB buffer
    assert max(size) <= 1568


def test_fit_encode_huge_image_still_fits():
    # a 4K high-entropy image must still be brought under budget (steps down dimension if needed)
    data, mime, scale, size = images.fit_encode(_busy(3840, 2160), max_bytes=700_000)
    assert len(data) <= 700_000 and mime == "image/jpeg"


def test_fit_encode_small_image_unchanged_dim():
    img = Image.new("RGB", (400, 300), (10, 20, 30))
    data, mime, scale, size = images.fit_encode(img)
    assert size == (400, 300) and scale == 1.0 and len(data) < 700_000
