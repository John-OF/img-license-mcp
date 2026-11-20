"""Source adapters, fed with recorded-shape API responses (no real network)."""

from __future__ import annotations

import httpx

import sources as src
from common import DEFAULT_USER_AGENT


def commons_page(title, index, license_code, *, mime="image/jpeg", width=6558, height=4372, **extra):
    extmetadata = {"License": {"value": license_code}} if license_code else {}
    for key, value in extra.items():
        extmetadata[key] = {"value": value}
    return {
        "title": f"File:{title}",
        "index": index,
        "imageinfo": [
            {
                "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{title}",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}",
                "width": width,
                "height": height,
                "size": 17_000_000,
                "mime": mime,
                "extmetadata": extmetadata,
            }
        ],
    }


BAMBOO = commons_page(
    "Bamboo_Forest.jpg",
    2,
    "cc-by-sa-4.0",
    LicenseShortName="CC BY-SA 4.0",
    LicenseUrl="https://creativecommons.org/licenses/by-sa/4.0",
    Artist='<a href="//commons.wikimedia.org/wiki/User:Basile_Morin">Basile Morin</a>',
    AttributionRequired="true",
)


def json_response(payload, status=200):
    return httpx.Response(status, json=payload)


# --------------------------------------------------------------------------- #
# Wikimedia Commons
# --------------------------------------------------------------------------- #


def test_commons_search_reads_the_licence_per_file(mock_http):
    pages = [
        BAMBOO,
        commons_page("Temple.jpg", 1, "cc0"),
        commons_page("Unknown.jpg", 3, "GFDL"),  # unreadable licence: dropped
        commons_page("Scan.tif", 4, None, mime="image/tiff"),  # not a web bitmap
    ]
    seen = mock_http(lambda request: json_response({"query": {"pages": pages}}))

    entries, examined, not_bitmap = src.commons_search("arashiyama bamboo", 10)

    assert (examined, not_bitmap) == (4, 1)
    # Sorted by search relevance (`index`), not by the generator's order.
    assert [e["title"] for e in entries] == ["Temple.jpg", "Bamboo_Forest.jpg"]

    bamboo = entries[1]
    assert bamboo["license"] == "CC BY-SA 4.0"
    assert bamboo["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0"
    assert bamboo["author"] == "Basile Morin"
    assert bamboo["author_url"] == "https://commons.wikimedia.org/wiki/User:Basile_Morin"
    assert bamboo["attribution_required"] is True
    assert (bamboo["width"], bamboo["original_width"]) == (6558, 6558)

    request = seen[0]
    assert request.url.params["gsrsearch"] == "filetype:bitmap arashiyama bamboo"
    assert request.headers["User-Agent"] == DEFAULT_USER_AGENT


def test_commons_explicit_attribution_flag_wins(mock_http):
    page = commons_page("Logo.png", 1, "cc-by-4.0", mime="image/png", AttributionRequired="false")
    mock_http(lambda request: json_response({"query": {"pages": [page]}}))

    entries, _, _ = src.commons_search("logo", 5)

    assert entries[0]["attribution_required"] is False


def test_commons_details_missing_file(mock_http):
    mock_http(lambda request: json_response({"query": {"pages": [{"title": "File:Nope.jpg", "missing": True}]}}))
    assert src.commons_details(title="File:Nope.jpg") is None


def test_commons_details_by_pageid(mock_http):
    seen = mock_http(lambda request: json_response({"query": {"pages": [BAMBOO]}}))

    entry = src.commons_details(pageid=12345)

    assert entry["source"] == "commons"
    assert seen[0].url.params["pageids"] == "12345"


# --------------------------------------------------------------------------- #
# Openverse
# --------------------------------------------------------------------------- #


def openverse_result(n, license_code="by", version="2.0"):
    return {
        "title": f"Photo {n}",
        "url": f"https://live.staticflickr.com/{n}.jpg",
        "foreign_landing_url": f"https://www.flickr.com/photos/x/{n}",
        "creator": "Someone",
        "creator_url": "https://www.flickr.com/people/x",
        "license": license_code,
        "license_version": version,
        "license_url": f"https://creativecommons.org/licenses/{license_code}/{version}/",
        "width": 4000,
        "height": 3000,
        "filetype": "jpg",
        "source": "flickr",
    }


def test_openverse_paginates_in_pages_of_20(mock_http):
    # Anonymous requests with page_size > 20 get a 401, so asking for more
    # means paging, not a bigger page.
    def handler(request):
        page = int(request.url.params["page"])
        count = 20 if page == 1 else 3
        return json_response({"results": [openverse_result(page * 100 + i) for i in range(count)]})

    seen = mock_http(handler)

    entries, examined, _ = src.openverse_search("kyoto", 30, "any")

    assert [r.url.params["page"] for r in seen] == ["1", "2"]
    assert {r.url.params["page_size"] for r in seen} == {"20"}
    assert (len(entries), examined) == (23, 23)
    assert entries[0]["license"] == "CC BY 2.0"
    assert entries[0]["provider"] == "flickr"
    assert entries[0]["mime"] == "image/jpg"


def test_openverse_filters_on_the_server_and_drops_unknown(mock_http):
    seen = mock_http(
        lambda request: json_response({"results": [openverse_result(1, "cc0", ""), openverse_result(2, "other", "")]})
    )

    entries, examined, _ = src.openverse_search("kyoto", 5, "no_attribution")

    assert seen[0].url.params["license"] == "cc0,pdm"
    assert examined == 2
    assert [e["license"] for e in entries] == ["CC0 1.0"]


def test_openverse_commercial_filter_param(mock_http):
    seen = mock_http(lambda request: json_response({"results": []}))
    src.openverse_search("kyoto", 5, "commercial")
    assert seen[0].url.params["license_type"] == "commercial,modification"


# --------------------------------------------------------------------------- #
# Keyed sources
# --------------------------------------------------------------------------- #


def test_source_key_reads_the_environment(monkeypatch):
    assert src.source_key("unsplash") is None
    assert src.source_key("commons") is None
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", " abc ")
    assert src.source_key("unsplash") == "abc"


def test_unsplash_declares_the_size_it_actually_serves(mock_http, monkeypatch):
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "key123")
    photo = {
        "width": 6000,
        "height": 4000,
        "description": "Bamboo",
        "urls": {"raw": "https://images.unsplash.com/photo-1?ixid=abc"},
        "links": {"html": "https://unsplash.com/photos/1", "download_location": "https://api.unsplash.com/d/1"},
        "user": {"name": "Jane Doe", "links": {"html": "https://unsplash.com/@jane"}},
    }
    seen = mock_http(lambda request: json_response({"results": [photo]}))

    entries, _, _ = src.unsplash_search("bamboo", 5, "commercial")

    assert seen[0].headers["Authorization"] == "Client-ID key123"
    entry = entries[0]
    assert (entry["width"], entry["height"]) == (2400, 1600)
    assert (entry["original_width"], entry["original_height"]) == (6000, 4000)
    assert entry["file_url"].endswith("&w=2400&q=85&fm=jpg&fit=max")
    assert entry["license"] == "Unsplash License"


def test_pixabay_declares_the_capped_size(mock_http, monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "key")
    hit = {
        "pageURL": "https://pixabay.com/photos/kyoto-1/",
        "largeImageURL": "https://pixabay.com/get/large.jpg",
        "imageWidth": 6000,
        "imageHeight": 4000,
        "user": "someone",
        "user_id": 42,
        "tags": "kyoto, temple",
    }
    mock_http(lambda request: json_response({"hits": [hit]}))

    entries, _, _ = src.pixabay_search("kyoto", 5, "commercial")

    assert (entries[0]["width"], entries[0]["height"]) == (1280, 853)
    assert entries[0]["author_url"] == "https://pixabay.com/users/someone-42/"


def test_nps_keeps_only_photos_credited_to_the_nps(mock_http, monkeypatch):
    monkeypatch.setenv("NPS_API_KEY", "key")
    monkeypatch.setattr(src, "probe_dimensions", lambda url: (2048, 1365))
    park = {
        "url": "https://www.nps.gov/yose/index.htm",
        "images": [
            {"url": "https://www.nps.gov/a.jpg", "credit": "NPS Photo / Jane Ranger", "title": "Half Dome"},
            {"url": "https://www.nps.gov/b.jpg", "credit": "Courtesy of a private photographer"},
            {"url": "https://www.nps.gov/c.jpg", "credit": ""},
        ],
    }
    mock_http(lambda request: json_response({"data": [park]}))

    entries, examined, _ = src.nps_search("yosemite", 10, "commercial")

    assert examined == 3
    assert [e["file_url"] for e in entries] == ["https://www.nps.gov/a.jpg"]
    assert entries[0]["license"] == "Public domain"
    assert (entries[0]["width"], entries[0]["dimensions_probed"]) == (2048, True)
