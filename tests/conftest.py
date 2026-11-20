"""Shared fixtures: every test runs offline and with a clean environment.

server.py calls load_dotenv() on import, so a developer's real .env would leak
API keys and WordPress credentials into the tests. They are removed before
each test, and any HTTP request that a test did not mock fails loudly.
"""

from __future__ import annotations

import functools
import io
import random

import httpx
import pytest
from PIL import Image

REAL_CLIENT = httpx.Client

ENV_VARS = (
    "IMG_LICENSE_USER_AGENT",
    "UNSPLASH_ACCESS_KEY",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "NPS_API_KEY",
    "WP_BASE_URL",
    "WP_USER",
    "WP_APP_PASSWORD",
)


def _route(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", functools.partial(REAL_CLIENT, transport=transport))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network call: {request.method} {request.url}")

    _route(monkeypatch, refuse)


@pytest.fixture
def mock_http(monkeypatch):
    """Route every httpx.Client through `handler`; returns the list of requests seen."""

    def install(handler):
        seen: list[httpx.Request] = []

        def recorder(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        _route(monkeypatch, recorder)
        return seen

    return install


def gradient_jpeg(width: int, height: int, quality: int = 100) -> bytes:
    """A smooth, photo-sized JPEG: compresses well, like a real landscape."""
    red = Image.linear_gradient("L").resize((width, height))
    green = Image.radial_gradient("L").resize((width, height))
    blue = red.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    buffer = io.BytesIO()
    Image.merge("RGB", (red, green, blue)).save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def noise_image(width: int, height: int, seed: int = 1) -> Image.Image:
    """Random RGB pixels: tens of thousands of distinct colours, like a photo."""
    rng = random.Random(seed)
    return Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))
