"""Pure helpers: HTTP identity, HTML parsing, Commons URLs and image maths."""

from __future__ import annotations

import pytest
from PIL import Image

import common
import imaging
from tests.conftest import noise_image

# --------------------------------------------------------------------------- #
# HTTP identity
# --------------------------------------------------------------------------- #


def test_default_user_agent_is_descriptive():
    # Wikimedia answers 403 to generic agents; the default must carry a contact.
    assert common.user_agent() == common.DEFAULT_USER_AGENT
    assert "https://" in common.DEFAULT_USER_AGENT


def test_user_agent_is_read_at_request_time(monkeypatch):
    # Regression: it used to be read on import, before load_dotenv() ran, so the
    # value from .env was silently ignored.
    monkeypatch.setenv("IMG_LICENSE_USER_AGENT", "MyBlog/2.0 (https://example.com)")
    assert common.headers()["User-Agent"] == "MyBlog/2.0 (https://example.com)"


def test_blank_user_agent_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("IMG_LICENSE_USER_AGENT", "   ")
    assert common.user_agent() == common.DEFAULT_USER_AGENT


def test_headers_merge_extra_values():
    merged = common.headers({"Authorization": "Client-ID abc"})
    assert merged["Authorization"] == "Client-ID abc"
    assert merged["User-Agent"] == common.DEFAULT_USER_AGENT


# --------------------------------------------------------------------------- #
# HTML fragments (Commons' Artist field is HTML)
# --------------------------------------------------------------------------- #

ARTIST = '<a href="//commons.wikimedia.org/wiki/User:Basile_Morin" title="User">Basile&nbsp;Morin</a>\n'


def test_strip_tags():
    assert common.strip_tags(ARTIST) == "Basile Morin"
    assert common.strip_tags("<span>  </span>") is None
    assert common.strip_tags(None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (ARTIST, "https://commons.wikimedia.org/wiki/User:Basile_Morin"),
        ('<a href="/wiki/User:Someone">x</a>', "https://commons.wikimedia.org/wiki/User:Someone"),
        ("<a href='https://www.flickr.com/people/x'>x</a>", "https://www.flickr.com/people/x"),
        ('<a href="mailto:someone@example.com">x</a>', None),
        ("Plain name, no link", None),
        (None, None),
    ],
)
def test_first_href(raw, expected):
    assert common.first_href(raw) == expected


# --------------------------------------------------------------------------- #
# Commons URL shapes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "title", "pageid"),
    [
        ("https://commons.wikimedia.org/wiki/File:Bamboo_Forest.jpg", "File:Bamboo_Forest.jpg", None),
        ("https://commons.wikimedia.org/wiki/Archivo:Bamboo.jpg", "File:Bamboo.jpg", None),
        ("https://commons.wikimedia.org/wiki/File:Caf%C3%A9_Paris.jpg", "File:Café_Paris.jpg", None),
        (
            "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Bamboo.jpg/1920px-Bamboo.jpg",
            "File:Bamboo.jpg",
            None,
        ),
        ("https://upload.wikimedia.org/wikipedia/commons/7/7a/Bamboo.jpg", "File:Bamboo.jpg", None),
        ("https://commons.wikimedia.org/w/index.php?curid=12345", None, 12345),
        ("https://commons.wikimedia.org/w/index.php?title=File:Bamboo.jpg", "File:Bamboo.jpg", None),
    ],
)
def test_commons_title_from_url(url, title, pageid):
    got_title, got_pageid, how = common.commons_title_from_url(url)
    assert (got_title, got_pageid) == (title, pageid)
    assert how


@pytest.mark.parametrize(
    "url",
    [
        "https://commons.wikimedia.org/wiki/Category:Kyoto",
        "https://www.flickr.com/photos/someone/123",
        "https://images.unsplash.com/photo-1",
    ],
)
def test_commons_title_from_url_rejects_non_files(url):
    title, pageid, _ = common.commons_title_from_url(url)
    assert title is None and pageid is None


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "stem"),
    [
        ("https://upload.wikimedia.org/a/b/File:Bamboo_Forest,_Kyoto.jpg", "File-Bamboo_Forest-_Kyoto"),
        ("https://example.com/photo.jpg?w=2400", "photo"),
        ("https://example.com/", "image"),
        ("https://example.com/%20%20.png", "image"),
    ],
)
def test_safe_stem(url, stem):
    assert imaging.safe_stem(url) == stem


def test_safe_stem_is_capped():
    assert len(imaging.safe_stem("https://example.com/" + "a" * 300 + ".jpg")) == 80


def test_scaled_to_fit_caps_the_longest_side():
    assert imaging.scaled_to_fit(4000, 3000, 1280) == (1280, 960)
    assert imaging.scaled_to_fit(3000, 4000, 1280) == (960, 1280)  # portrait
    assert imaging.scaled_to_fit(800, 600, 1280) == (800, 600)  # never upscales
    assert imaging.scaled_to_fit(None, 600, 1280) == (None, None)


def test_scaled_to_width_caps_only_the_width():
    assert imaging.scaled_to_width(6000, 4000, 2400) == (2400, 1600)
    # Portrait: the width is capped, the height may stay above the cap.
    assert imaging.scaled_to_width(3000, 4500, 2400) == (2400, 3600)
    assert imaging.scaled_to_width(1200, 800, 2400) == (1200, 800)
    assert imaging.scaled_to_width(0, 800, 2400) == (None, None)


def test_looks_like_photo():
    assert imaging.looks_like_photo(noise_image(200, 200))
    assert not imaging.looks_like_photo(Image.new("RGB", (200, 200), "white"))  # flat graphic
    assert not imaging.looks_like_photo(noise_image(200, 200).convert("RGBA"))  # has alpha


def test_save_options():
    assert imaging.save_options_for("webp", 82) == {"quality": 82, "method": 6}
    assert imaging.save_options_for("jpeg", 82)["progressive"] is True
    assert imaging.save_options_for("png", 82) == {"optimize": True}
