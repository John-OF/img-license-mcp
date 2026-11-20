"""The five MCP tools, end to end, with every HTTP call mocked."""

from __future__ import annotations

import base64
import io
import json
import os
from datetime import UTC, datetime

import httpx
import pytest
from PIL import Image

import imaging
import server
import sources as src
from licensing import parse_license
from tests.conftest import gradient_jpeg, noise_image
from wordpress import build_description

# --------------------------------------------------------------------------- #
# search_images
# --------------------------------------------------------------------------- #


def candidate(source, file_url, width=2000, code="cc-by-sa-4.0"):
    return {
        "source": source,
        "file_url": file_url,
        "source_page_url": file_url.replace("upload", "commons") + "/page",
        "width": width,
        "height": 1300,
        **parse_license(code),
    }


def test_search_rejects_empty_query():
    assert server.search_images.fn(query="  ")["ok"] is False


def test_search_rejects_unknown_sources():
    result = server.search_images.fn(query="kyoto", sources=["flickr"])
    assert result["ok"] is False
    assert "flickr" in result["error"]


def test_search_filters_dedupes_and_counts(monkeypatch):
    bamboo = "https://upload.wikimedia.org/a/Bamboo.jpg"
    commons = [
        candidate("commons", bamboo),
        candidate("commons", "https://upload.wikimedia.org/a/Small.jpg", width=1000),
        candidate("commons", "https://upload.wikimedia.org/a/NonCommercial.jpg", code="cc-by-nc-4.0"),
    ]
    openverse = [
        # The same Commons file, re-indexed by Openverse with a query string.
        candidate("openverse", bamboo.upper() + "?w=1"),
        candidate("openverse", "https://live.staticflickr.com/NoSize.jpg", width=None),
    ]
    monkeypatch.setattr(src, "commons_search", lambda query, limit: (commons, 5, 1))
    monkeypatch.setitem(src.ADAPTERS, "openverse", lambda query, limit, lf: (openverse, 2, 0))

    result = server.search_images.fn(query="kyoto bamboo")

    assert result["ok"] is True
    assert [r["file_url"] for r in result["results"]] == [bamboo]
    assert result["sources_used"] == ["commons", "openverse"]
    assert result["candidates_examined"] == 7
    assert result["discarded_unknown_license"] == 1  # 5 seen - 3 kept - 1 not an image
    assert result["discarded_not_image"] == 1
    assert result["discarded_by_license"] == 1
    assert result["discarded_too_small"] == 1
    assert result["discarded_unknown_size"] == 1
    assert result["duplicates_merged"] == 1
    # Keyed sources without a key are skipped and explained, not failed.
    assert result["sources_skipped"] == {
        "unsplash": "UNSPLASH_ACCESS_KEY is not set",
        "pexels": "PEXELS_API_KEY is not set",
        "pixabay": "PIXABAY_API_KEY is not set",
        "nps": "NPS_API_KEY is not set",
    }


def test_search_min_width_zero_keeps_unknown_sizes(monkeypatch):
    found = [candidate("commons", "https://upload.wikimedia.org/a/NoSize.jpg", width=None)]
    monkeypatch.setattr(src, "commons_search", lambda query, limit: (found, 1, 0))

    result = server.search_images.fn(query="kyoto", sources=["commons"], min_width=0)

    assert result["count"] == 1


def test_search_reports_a_failing_source(monkeypatch):
    def broken(query, limit):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(src, "commons_search", broken)
    monkeypatch.setitem(src.ADAPTERS, "openverse", lambda query, limit, lf: ([], 0, 0))

    result = server.search_images.fn(query="kyoto", sources=["commons", "openverse"])

    assert result["ok"] is True
    assert "commons" in result["source_errors"]
    assert result["degraded"]
    assert result["hint"]  # no results left


def test_search_fails_when_no_source_answers(monkeypatch):
    def broken(query, limit):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(src, "commons_search", broken)

    result = server.search_images.fn(query="kyoto", sources=["commons"])

    assert result == {
        "ok": False,
        "query": "kyoto",
        "error": "No source answered.",
        "sources_skipped": {},
        "source_errors": {"commons": "ConnectError: boom"},
    }


def test_interleave_alternates_sources():
    groups = [[1, 2, 3], ["a"], ["x", "y"]]
    assert server._interleave(groups) == [1, "a", "x", 2, "y", 3]


# --------------------------------------------------------------------------- #
# inspect_image
# --------------------------------------------------------------------------- #


def test_inspect_refuses_hosts_it_cannot_verify():
    # No network call is made: the licence is never inferred from the domain.
    result = server.inspect_image.fn(url="https://www.flickr.com/photos/x/1")
    assert result["ok"] is False
    assert result["host"] == "www.flickr.com"


def test_inspect_requires_http():
    assert server.inspect_image.fn(url="ftp://example.com/a.jpg")["ok"] is False


def test_inspect_reads_the_licence_from_commons(mock_http):
    page = {
        "title": "File:Bamboo.jpg",
        "imageinfo": [
            {
                "url": "https://upload.wikimedia.org/a/Bamboo.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Bamboo.jpg",
                "width": 6558,
                "height": 4372,
                "mime": "image/jpeg",
                "extmetadata": {"License": {"value": "cc-by-sa-4.0"}, "Artist": {"value": "Basile Morin"}},
            }
        ],
    }
    seen = mock_http(lambda request: httpx.Response(200, json={"query": {"pages": [page]}}))

    result = server.inspect_image.fn(url="https://upload.wikimedia.org/thumb/a/ab/Bamboo.jpg/1920px-Bamboo.jpg")

    assert result["ok"] is True
    assert result["license"] == "CC BY-SA 4.0"
    assert result["resolved_by"] == "upload.wikimedia.org thumbnail URL"
    assert seen[0].url.params["titles"] == "File:Bamboo.jpg"


def test_inspect_unreadable_licence_is_not_publishable(mock_http):
    page = {"title": "File:X.jpg", "imageinfo": [{"mime": "image/jpeg", "extmetadata": {}}]}
    mock_http(lambda request: httpx.Response(200, json={"query": {"pages": [page]}}))

    result = server.inspect_image.fn(url="https://commons.wikimedia.org/wiki/File:X.jpg")

    assert result["ok"] is False
    assert "license" not in result


# --------------------------------------------------------------------------- #
# prepare_image
# --------------------------------------------------------------------------- #


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(imaging, "output_dir", lambda: tmp_path)
    return tmp_path


def serve_bytes(payload, content_type="image/jpeg"):
    return lambda request: httpx.Response(200, content=payload, headers={"content-type": content_type})


def test_prepare_downscales_and_converts_to_webp(mock_http, out_dir):
    source = gradient_jpeg(2400, 1600)
    mock_http(serve_bytes(source))

    result = server.prepare_image.fn(url="https://example.com/photos/Kyoto%20Bamboo.jpg")

    assert result["ok"] is True
    assert (result["source_width"], result["width"], result["height"]) == (2400, 1600, 1067)
    assert result["resized"] is True
    assert result["bytes_before"] == len(source)
    assert result["saving_pct"] > 30
    assert result["warnings"] == []

    output = out_dir / "Kyoto-Bamboo-1600px.webp"
    assert result["output_path"] == str(output)
    with Image.open(output) as image:
        assert (image.format, image.size) == ("WEBP", (1600, 1067))


def test_prepare_never_upscales(mock_http, out_dir):
    mock_http(serve_bytes(gradient_jpeg(800, 600)))

    result = server.prepare_image.fn(url="https://example.com/small.jpg", max_width=1600)

    assert (result["width"], result["resized"]) == (800, False)
    assert any("not upscaled" in w for w in result["warnings"])


def test_prepare_warns_when_the_saving_is_small(mock_http, out_dir):
    # High-detail texture with no repeatable structure, already at the target
    # size: re-encoding it barely pays off, or even grows it.
    buffer = io.BytesIO()
    noise_image(1600, 1000).save(buffer, format="JPEG", quality=60)
    mock_http(serve_bytes(buffer.getvalue()))

    result = server.prepare_image.fn(url="https://example.com/compressed.jpg")

    assert result["saving_pct"] < 30
    assert any("threshold 30%" in w or "LARGER" in w for w in result["warnings"])


def test_prepare_uses_the_commons_thumbnail(mock_http, out_dir):
    thumb = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Bamboo.jpg/1920px-Bamboo.jpg"

    def handler(request):
        if request.url.path == "/w/api.php":
            info = {"thumburl": thumb, "width": 6558, "url": "https://upload.wikimedia.org/a/ab/Bamboo.jpg"}
            return httpx.Response(200, json={"query": {"pages": [{"imageinfo": [info]}]}})
        assert str(request.url) == thumb
        return httpx.Response(200, content=gradient_jpeg(1920, 1280))

    seen = mock_http(handler)

    result = server.prepare_image.fn(url="https://commons.wikimedia.org/wiki/File:Bamboo.jpg")

    assert result["download_url"] == thumb
    assert result["width"] == 1600
    assert seen[0].url.params["iiurlwidth"] == "1600"


def test_prepare_rejects_content_that_is_not_an_image(mock_http, out_dir):
    mock_http(serve_bytes(b"<html>Not found</html>", "text/html"))

    result = server.prepare_image.fn(url="https://example.com/a.jpg")

    assert result["ok"] is False
    assert result["content_type"] == "text/html"


def test_prepare_reports_http_errors(mock_http, out_dir):
    mock_http(lambda request: httpx.Response(403))

    result = server.prepare_image.fn(url="https://example.com/a.jpg")

    assert result["ok"] is False
    assert "IMG_LICENSE_USER_AGENT" in result["hint"]


def test_prepare_jpeg_drops_alpha(mock_http, out_dir):
    buffer = io.BytesIO()
    Image.new("RGBA", (400, 300), (10, 20, 30, 128)).save(buffer, format="PNG")
    mock_http(serve_bytes(buffer.getvalue(), "image/png"))

    result = server.prepare_image.fn(url="https://example.com/logo.png", max_width=400, format="jpeg")

    assert result["ok"] is True
    assert any("alpha" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# build_attribution
# --------------------------------------------------------------------------- #


def test_attribution_links_author_licence_and_source():
    result = server.build_attribution.fn(
        author="Basile Morin",
        license_name="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0",
        file_page_url="https://commons.wikimedia.org/wiki/File:Bamboo.jpg",
        author_url="https://commons.wikimedia.org/wiki/User:Basile_Morin",
    )

    assert result["html"] == (
        '<p>Photo: <a href="https://commons.wikimedia.org/wiki/User:Basile_Morin">Basile Morin</a>, '
        '<a href="https://creativecommons.org/licenses/by-sa/4.0" rel="license">CC BY-SA 4.0</a>, '
        'via <a href="https://commons.wikimedia.org/wiki/File:Bamboo.jpg">Wikimedia Commons</a></p>'
    )
    assert result["text"] == "Photo: Basile Morin, CC BY-SA 4.0, via Wikimedia Commons"
    assert result["attribution_required"] is True
    assert result["notes"] == []


def test_attribution_escapes_html_and_handles_missing_links():
    result = server.build_attribution.fn(
        author="<script>x</script>",
        license_name="CC0 1.0",
        license_url="",
        file_page_url="https://example.com/p",
        source="My Archive",
    )

    assert "<script>" not in result["html"]
    assert "&lt;script&gt;" in result["html"]
    assert result["source_display"] == "My Archive"
    assert result["attribution_required"] is False
    assert len(result["notes"]) == 3  # optional credit, no author link, no licence link


@pytest.mark.parametrize(("author", "licence"), [("", "CC BY 4.0"), ("Someone", " ")])
def test_attribution_requires_author_and_licence(author, licence):
    result = server.build_attribution.fn(author=author, license_name=licence, license_url="", file_page_url="")
    assert result["ok"] is False


# --------------------------------------------------------------------------- #
# upload_to_wordpress
# --------------------------------------------------------------------------- #

UPLOAD_ARGS = {
    "title": "Arashiyama bamboo grove",
    "alt_text": "A path through a dense bamboo forest in Kyoto",
    "source_url": "https://commons.wikimedia.org/wiki/File:Bamboo.jpg",
    "author": "Basile Morin",
    "license_name": "CC BY-SA 4.0",
    "original_width": 6558,
    "original_height": 4372,
}


@pytest.fixture
def wp_env(monkeypatch):
    monkeypatch.setenv("WP_BASE_URL", "https://blog.example.com/")
    monkeypatch.setenv("WP_USER", "editor")
    monkeypatch.setenv("WP_APP_PASSWORD", "abcd efgh ijkl")


@pytest.fixture
def prepared(tmp_path):
    path = tmp_path / "Bamboo-1600px.webp"
    Image.new("RGB", (160, 100), "green").save(path, format="WEBP")
    return path


def wordpress_handler(request):
    path = request.url.path
    if path == "/wp-json/wp/v2/media":
        return httpx.Response(201, json={"id": 42})
    if path == "/wp-json/wp/v2/media/42":
        body = json.loads(request.content)
        if "alt_text" not in body:
            return httpx.Response(200, json={"id": 42, "post": body.get("post")})
        return httpx.Response(
            200,
            json={
                "id": 42,
                "source_url": "https://blog.example.com/wp-content/uploads/Bamboo-1600px.webp",
                "alt_text": body["alt_text"],
                "title": {"rendered": body["title"]},
                "mime_type": "image/webp",
                "media_details": {"sizes": {"thumbnail": {"width": 150, "height": 150}}},
            },
        )
    if path == "/wp-json/wp/v2/posts/7":
        return httpx.Response(200, json={"id": 7})
    return httpx.Response(404)


def test_upload_needs_credentials(prepared):
    result = server.upload_to_wordpress.fn(file_path=str(prepared), **UPLOAD_ARGS)
    assert result["ok"] is False
    assert "WP_BASE_URL" in result["hint"]


def test_upload_writes_binary_then_metadata(wp_env, prepared, mock_http):
    seen = mock_http(wordpress_handler)

    result = server.upload_to_wordpress.fn(file_path=str(prepared), caption="Photo: credit", **UPLOAD_ARGS)

    assert result["ok"] is True
    assert result["media_id"] == 42
    assert result["edit_url"] == "https://blog.example.com/wp-admin/post.php?post=42&action=edit"
    assert result["sizes"] == {"thumbnail": "150x150"}
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/wp-json/wp/v2/media"),
        ("POST", "/wp-json/wp/v2/media/42"),
    ]

    token = base64.b64encode(b"editor:abcd efgh ijkl").decode()
    assert all(r.headers["Authorization"] == f"Basic {token}" for r in seen)
    assert b'filename="Bamboo-1600px.webp"' in seen[0].content

    # WordPress ignores alt_text and description in the multipart body, so
    # they travel in the second request, with the provenance block.
    metadata = json.loads(seen[1].content)
    assert metadata["alt_text"] == UPLOAD_ARGS["alt_text"]
    assert metadata["caption"] == "Photo: credit"
    for line in ("Source: https://commons.wikimedia.org", "Author: Basile Morin", "Licence: CC BY-SA 4.0",
                 "Original resolution: 6558x4372", "Downloaded: "):
        assert line in metadata["description"]


def test_upload_sets_the_featured_image_twice(wp_env, prepared, mock_http):
    seen = mock_http(wordpress_handler)

    result = server.upload_to_wordpress.fn(file_path=str(prepared), attach_to_post=7, set_featured=True, **UPLOAD_ARGS)

    assert result["attached_to_post"] == 7
    assert result["featured_media"]["double_post"] is True
    assert result["warnings"] == []
    paths = [r.url.path for r in seen]
    assert paths.count("/wp-json/wp/v2/posts/7") == 2
    assert json.loads(seen[-1].content) == {"featured_media": 42}


def test_upload_reports_a_failed_metadata_step(wp_env, prepared, mock_http):
    def handler(request):
        if request.url.path == "/wp-json/wp/v2/media":
            return httpx.Response(201, json={"id": 42})
        return httpx.Response(500, text="database error")

    mock_http(handler)

    result = server.upload_to_wordpress.fn(file_path=str(prepared), **UPLOAD_ARGS)

    assert (result["ok"], result["step"], result["media_id"]) == (False, "metadata", 42)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"alt_text": "  "}, "alt_text"),
        ({"set_featured": True}, "attach_to_post"),
        ({"file_path": "missing.webp"}, "File not found"),
    ],
)
def test_upload_validates_before_any_request(wp_env, prepared, overrides, error):
    args = {**UPLOAD_ARGS, "file_path": str(prepared), **overrides}
    result = server.upload_to_wordpress.fn(**args)
    assert result["ok"] is False
    assert error in result["error"]


def test_upload_rejects_unsupported_extensions(wp_env, tmp_path):
    gif = tmp_path / "anim.gif"
    gif.write_bytes(b"GIF89a")
    result = server.upload_to_wordpress.fn(file_path=str(gif), **UPLOAD_ARGS)
    assert "Unsupported extension" in result["error"]


def test_build_description_uses_the_file_date(tmp_path):
    path = tmp_path / "a.webp"
    path.write_bytes(b"x")
    stamp = datetime(2026, 9, 6, 12, 0, tzinfo=UTC).timestamp()
    os.utime(path, (stamp, stamp))

    text = build_description(path, "https://src", "Ann", "CC0 1.0", 100, 50, notes="Shot in spring.")

    assert text == (
        "Shot in spring.<br /><br />Source: https://src<br />Author: Ann<br />Licence: CC0 1.0<br />"
        "Original resolution: 100x50<br />Downloaded: 2026-09-06"
    )
