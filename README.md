# Img-License MCP

[![CI](https://github.com/John-OF/img-license-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/John-OF/img-license-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An MCP server (FastMCP, stdio) that lets an AI agent illustrate blog articles
with images whose licence has been **verified at the source**: it searches with
a licence filter, converts to WebP at the right size, builds the attribution
line when the licence requires one, and uploads the file to WordPress with its
provenance metadata.

<p align="center">
  <img src="assets/demo.svg" width="800" alt="An agent calls search_images, prepare_image, build_attribution and upload_to_wordpress: it finds a CC BY-SA photo whose licence was read at the source, converts it to a 1600 px WebP 46.5 % lighter, builds the credit line and uploads it with its provenance">
</p>
<p align="center"><sub>Search, WebP and credit from a live run on 2026-11-20; the upload from the verified run against a live WordPress site. Regenerate with <code>python tools/make_demo.py</code>.</sub></p>

| Tool | Source | Returns |
|---|---|---|
| `search_images` | Commons (`extmetadata`) · Openverse · 4 keyed sources | Candidates with a readable licence, author, dimensions and discard counters |
| `inspect_image` | Commons | Re-reads the licence of one image, in any of its URL shapes |
| `prepare_image` | Pillow | Downloads, downscales (never upscales) and converts to WebP, with the real byte saving |
| `build_attribution` | — | The `Photo: Author, LICENCE, via Source` paragraph with its three links |
| `upload_to_wordpress` | WordPress REST | Uploads the file, writes `alt_text` and the provenance, and can set the featured image |

**The golden rule:** a candidate whose licence cannot be read from the field its
source publishes **does not appear** in the response, not even with a warning.
It is dropped silently and counted in `discarded_unknown_license`. A licence is
never inferred from the domain, the search engine or the thumbnail.

---

## Installation

Requires Python 3.11+ (developed on 3.12).

```bash
git clone https://github.com/John-OF/img-license-mcp.git
cd img-license-mcp
python -m venv .venv
```

Install the dependencies with the virtual environment's Python:

```powershell
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`. **No key is needed to get started**: Commons and
Openverse work as they are. The other sources switch on by themselves when their
variable is set.

| Variable | Used for | Where to get it |
|---|---|---|
| `IMG_LICENSE_USER_AGENT` | HTTP identity. Without a descriptive UA, Wikimedia answers 403 | — (there is a default; set your own) |
| `UNSPLASH_ACCESS_KEY` | Unsplash source | <https://unsplash.com/oauth/applications> |
| `PEXELS_API_KEY` | Pexels source | <https://www.pexels.com/api/new/> |
| `PIXABAY_API_KEY` | Pixabay source | <https://pixabay.com/api/docs/> |
| `NPS_API_KEY` | National Park Service source | <https://www.nps.gov/subjects/developer/get-started.htm> |
| `WP_BASE_URL`, `WP_USER`, `WP_APP_PASSWORD` | `upload_to_wordpress` | Users → Profile → Application Passwords |

Paste the Application Password **with its spaces**, exactly as WordPress shows it.

Check that it starts (it waits on stdio; press `Ctrl+C` to exit):

```bash
.venv/bin/python server.py          # Windows: .venv\Scripts\python.exe server.py
```

---

## Registering the server in your MCP client

Always point to the **virtual environment's** Python, not the system one. In the
examples below, replace `<path-to-repo>` with the absolute path where you cloned
the repository (on macOS/Linux, use `.venv/bin/python` instead of
`.venv/Scripts/python.exe`).

**Claude Code, user scope** (available in every project):

```bash
claude mcp add img-license --scope user -- "<path-to-repo>/.venv/Scripts/python.exe" "<path-to-repo>/server.py"
```

**Claude Code, project scope:** copy `.mcp.json.example` to `.mcp.json` and fill
in the paths. It is loaded automatically when Claude Code opens the folder.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "img-license": {
      "command": "<path-to-repo>/.venv/Scripts/python.exe",
      "args": ["<path-to-repo>/server.py"]
    }
  }
}
```

### Code layout

| File | Contents |
|---|---|
| `server.py` | `FastMCP` and the five tools |
| `licensing.py` | Licence engine: source code → canonical licence |
| `sources.py` | One adapter per source, plus the registry of which key each one needs |
| `imaging.py` | Downloads, Commons thumbnails and Pillow helpers |
| `wordpress.py` | Credentials and the provenance block |
| `common.py` | User-Agent, endpoints and text helpers |

---

## Tools

### 1. `search_images(query, license_filter, min_width, sources, limit)`

Searches every available source and returns only what has a readable licence.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | str | — | What the photo should show. English works best: most source metadata is in English |
| `license_filter` | `commercial` \| `no_attribution` \| `any` | `commercial` | See below |
| `min_width` | int (0-10000) | `1600` | Minimum width of the **original**. `prepare_image` never upscales, so this is what caps the publishable size |
| `sources` | list | all | `commons`, `openverse`, `unsplash`, `pexels`, `pixabay`, `nps` |
| `limit` | int (1-50) | `12` | Candidates returned, already filtered and de-duplicated |

The three licence filters:

| Value | Lets through | When to use it |
|---|---|---|
| `commercial` | Public domain, CC0, CC BY, CC BY-SA | The default. Commercial use **and** the right to modify, which resizing and cropping require |
| `no_attribution` | Public domain, CC0, stock licences | When you don't want to maintain credit lines. Far fewer results |
| `any` | Everything determinable, NC and ND included | Maximum coverage; each result carries its flags so you can decide |

Real output (`query="Arashiyama bamboo forest"`, `sources=["commons", "openverse"]`, `limit=5`), trimmed to one result:

```json
{
  "ok": true,
  "query": "Arashiyama bamboo forest",
  "license_filter": "commercial",
  "min_width": 1600,
  "sources_used": ["commons", "openverse"],
  "candidates_examined": 30,
  "discarded_unknown_license": 0,
  "discarded_not_image": 0,
  "discarded_by_license": 0,
  "discarded_too_small": 4,
  "discarded_unknown_size": 0,
  "duplicates_merged": 0,
  "count": 5,
  "results": [
    {
      "source": "commons",
      "title": "Bamboo Forest, Arashiyama, Kyoto, Japan.jpg",
      "source_page_url": "https://commons.wikimedia.org/wiki/File:Bamboo_Forest,_Arashiyama,_Kyoto,_Japan.jpg",
      "file_url": "https://upload.wikimedia.org/wikipedia/commons/...",
      "license": "CC BY-SA 4.0",
      "license_code": "cc-by-sa-4.0",
      "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
      "attribution_required": true,
      "allows_commercial": true,
      "allows_modification": true,
      "author": "Basile Morin",
      "width": 6558, "height": 4372, "bytes": 17157793
    }
  ],
  "sources_skipped": {
    "unsplash": "UNSPLASH_ACCESS_KEY is not set",
    "pexels": "PEXELS_API_KEY is not set",
    "pixabay": "PIXABAY_API_KEY is not set",
    "nps": "NPS_API_KEY is not set"
  }
}
```

**`width`/`height` are always what can be DOWNLOADED, not what the source
claims.** Several APIs advertise the original's dimensions even when their
public URL serves something smaller; if `min_width` trusted that figure, it
would let through photos that then fall short. The original's real size goes
separately in `original_width`/`original_height`, and that is the one cited in
the provenance block on upload.

| Source | `width`/`height` | `original_*` |
|---|---|---|
| Commons, Openverse, Pexels | The original: its URL serves it whole | Same |
| Unsplash | Capped at 2400 wide, which is what the URL requests | The full original |
| Pixabay | Capped at 1280 on the longest side (see limits) | The original, which **cannot** be downloaded |
| NPS | **Measured** by reading the file header | Same |

**The discard counters** say why each candidate fell out, and are worth reading:

| Counter | Meaning |
|---|---|
| `discarded_unknown_license` | Its licence code could not be read or is not citable (GFDL, Free Art License, `attribution`…). **These are never shown** |
| `discarded_not_image` | TIFF and friends: Commons' `filetype:bitmap` lets them through and they are no use on the web. Counted separately so they don't inflate the counter above |
| `discarded_by_license` | Readable licence outside the filter (an NC licence with `license_filter="commercial"`) |
| `discarded_too_small` / `discarded_unknown_size` | Below `min_width`, or no published width |
| `duplicates_merged` | Openverse re-indexes Commons: the same file arrives both ways and is merged |

The counters cover the candidates processed until `limit` is reached. In the run
above, Openverse returned 15 Flickr photos, all at most 1024 px wide; the four
that were processed before the list filled up landed in `discarded_too_small`.
In a real query for `"Berlin 2005"` (50 Commons candidates), **30 came out as
`discarded_not_image`**: all orthophoto TIFFs.

### 2. `inspect_image(url)`

Re-reads the licence of one image, to confirm something that reached you some
other way. It accepts every Commons URL shape:

| URL | `resolved_by` |
|---|---|
| `commons.wikimedia.org/wiki/File:X.jpg` | `Commons file page` |
| `commons.wikimedia.org/w/index.php?curid=97189369` | `Commons curid` |
| `upload.wikimedia.org/wikipedia/commons/7/7a/X.jpg` | `direct upload.wikimedia.org file URL` |
| `.../thumb/7/7a/X.jpg/1920px-X.jpg` | `upload.wikimedia.org thumbnail URL` |

```json
{
  "ok": true,
  "resolved_by": "upload.wikimedia.org thumbnail URL",
  "license": "CC BY-SA 3.0",
  "attribution_required": true,
  "author": "Norbert Nagel",
  "author_url": "https://commons.wikimedia.org/wiki/User:NorbertNagel",
  "width": 4257, "height": 2553
}
```

Any other host returns `ok: false`. **A licence is never invented from the domain:**

```json
{
  "ok": false,
  "host": "www.shutterstock.com",
  "error": "The licence cannot be determined for this host (unsupported host)."
}
```

### 3. `prepare_image(url, max_width, format, quality)`

Download → downscale (**never upscale**) → convert.

| Parameter | Type | Default |
|---|---|---|
| `url` | str | — |
| `max_width` | int (64-5000) | `1600` |
| `format` | `webp` \| `png` \| `jpeg` | `webp` |
| `quality` | int (1-100) | `82` |

Real output for a 6558x4372, 17.2 MB photo on Commons:

```
download     : https://upload.wikimedia.org/.../1920px-Bamboo_Forest...jpg
reason       : Commons thumbnail from the next bucket up
source       : 1920x1280  (1,010,870 bytes)
output       : 1600x1067  (541,320 bytes)   resized=True
saving_pct   : 46.5 %
format_saving: 5.7 %   (same pixels as JPEG q82)
file         : <temp>/img-license/File-Bamboo_Forest-_Arashiyama-_Kyoto-_Japan-1600px.webp
```

Note that **the 17 MB original is not downloaded**, only 1 MB: for Wikimedia URLs
the thumbnail is requested from the API. Another real example, a Santorini
photo: 531,184 → **242,320 bytes, a 54.4 % saving**.

**The two savings measure different things:**

- `saving_pct` — what it weighed against what it weighs, resize and format
  together. This is the number that matters, and **below 30 % it raises an
  explicit warning**.
- `format_saving_pct` — the same final pixels re-encoded as JPEG, to isolate the
  format gain. It is a **much harsher** yardstick than comparing against the
  source JPEG, because a freshly encoded JPEG with `optimize`+`progressive`
  compresses very well: on ordinary photos it lands between 5 % and 20 %. It is
  informative and **never triggers a warning on its own**.

Warnings it can return, all seen in real tests:

```
The source is 3840px wide, narrower than the requested 4000px. It is not upscaled: it keeps its real size.
Reconverting barely pays off: it only saves 17.4% (3,919,476 -> 3,238,724 bytes, threshold 30%).
PNG for a photo produces huge files. Use WebP unless you need transparency or crisp text.
The output is LARGER than the source (273.2% more): 1,010,870 -> 3,772,141 bytes. Do not upload this file.
```

The last one is the PNG case: the same photo went from 1.0 MB to **3.8 MB** as PNG.

### 4. `build_attribution(author, license_name, license_url, file_page_url, source, author_url)`

```json
{
  "ok": true,
  "html": "<p>Photo: <a href=\"https://commons.wikimedia.org/wiki/User:Basile_Morin\">Basile Morin</a>, <a href=\"https://creativecommons.org/licenses/by-sa/4.0\" rel=\"license\">CC BY-SA 4.0</a>, via <a href=\"https://commons.wikimedia.org/wiki/File:Bamboo_Forest...jpg\">Wikimedia Commons</a></p>",
  "text": "Photo: Basile Morin, CC BY-SA 4.0, via Wikimedia Commons",
  "attribution_required": true,
  "notes": []
}
```

If the licence does **not** require credit, the paragraph is still built but
flagged, so you can decide:

```
attribution_required: false
note: CC0 1.0 does not require attribution. The credit is a courtesy, not an obligation: you can leave it out.
```

The author name is HTML-escaped, so an author with `<` or `&` in the name does
not break the page.

### 5. `upload_to_wordpress(...)`

| Parameter | Required | Description |
|---|---|---|
| `file_path` | yes | The output of `prepare_image`. **Never the untouched original** |
| `title` | yes | Media library title |
| `alt_text` | **yes** | In the site's language. **You write it**: nothing here invents it |
| `source_url`, `author`, `license_name` | yes | Go into the provenance block |
| `original_width`, `original_height` | yes | Resolution of the **original**, before resizing |
| `caption` | no | The credit paragraph goes here when the licence requires it |
| `notes` | no | Free text prepended to the provenance |
| `attach_to_post` | no | Post ID |
| `set_featured` | no | Sets the featured image. Requires `attach_to_post` |

It makes **two requests by design**: the multipart one uploads the binary and a
second POST writes the metadata, because WordPress ignores `alt_text` and
`description` in the multipart body.

The `description` is **built here**, not received as free text, so "always" can
be verified:

```
Source: https://commons.wikimedia.org/wiki/File:Bamboo_Forest...jpg
Author: Basile Morin
Licence: CC BY-SA 4.0
Original resolution: 6558x4372
Downloaded: 2026-09-06
```

The download date comes from the file's `mtime`, that is, from when
`prepare_image` wrote it.

Real output of an upload to a live WordPress site (domain replaced):

```json
{
  "ok": true,
  "media_id": 608,
  "media_url": "https://example.com/wp-content/uploads/2026/09/File-Bamboo_Forest-_Arashiyama-_Kyoto-_Japan-1600px.webp",
  "edit_url": "https://example.com/wp-admin/post.php?post=608&action=edit",
  "mime": "image/webp",
  "sizes": {
    "thumbnail": "150x150", "medium": "300x200", "medium_large": "768x512",
    "large": "1024x683", "full": "1600x1067"
  },
  "warnings": []
}
```

Read back afterwards through the unauthenticated API: correct `alt_text`, the
three credit links intact in the `caption` (with their `rel="license"`), the full
provenance block, and the file served at 541,320 bytes.

> **Don't panic when checking over REST:** `description.rendered` shows
> `Original resolution: 6558×4372`, with a multiplication sign. That is
> WordPress's `wptexturize`, which turns an `x` between numbers into `×` when
> rendering. The stored value keeps the `x` that was sent. WordPress also
> prepends a `<p class="attachment">` with the thumbnail to the rendered
> description; your text comes after it.

---

## Suggested workflow

1. `search_images("santorini blue domes at sunset")` → pick a candidate.
2. `inspect_image(source_page_url)` if it came from elsewhere and you want to re-confirm.
3. `prepare_image(file_url)` → check `saving_pct` and the `warnings`.
4. `build_attribution(...)` if `attribution_required` is `true`.
5. `upload_to_wordpress(output_path, ..., caption=<the html from step 4>)`.

Write the `alt_text` yourself, looking at the photo.

---

## Limits and caveats

**Verified against the real services, with dates.** None of this is guesswork:

- **Wikimedia answers 403 to generic User-Agents** (2026-09-01, 09-02 and
  **reconfirmed 09-06**): httpx's default → 403, `python-requests/2.32` → 403,
  a descriptive UA → 200. The default UA points to this repository; if you
  deploy the server, set `IMG_LICENSE_USER_AGENT` to your own descriptive UA with
  a contact.
- **Commons only serves the widths in its bucket list.** Building
  `/thumb/.../1600px-X.jpg` gives **400**; 1280 and 1920 give 200. That is why no
  thumbnail URL is ever built by hand here: it is requested from the API with
  `iiurlwidth`, which rounds up to the next valid bucket.
- **The Commons API misreports `thumbwidth`** (2026-09-06): with
  `iiurlwidth=1600` it answers `thumbwidth: 1600x960`, but the `thumburl` it hands
  over is the `1920px-` one and what you download measures **1920x1151**. So
  dimensions are always measured with Pillow on the downloaded bytes, never
  taken from the declared ones.
- **Anonymous Openverse: `page_size` max 20** (2026-09-06). With 21 it returns
  `401 "page_size may not exceed 20 for anonymous requests"`. The adapter pages
  in steps of 20 (up to 3 pages) instead of asking for big pages.
- **WebP q82 is the default** after the 2026-09-06 measurements (192 KB → 140 KB,
  47 KB → 26 KB). Mind that the ~40 % is against the **source** JPEG. Against a
  freshly encoded JPEG q82 the advantage is much smaller, and that is what
  `format_saving_pct` reports.
- **WebP does not win on high-detail textures without repeatable structure**: a
  crowd photo shrank 19 % and a satellite image of a hurricane 7 %, against
  54-86 % for the rest. Hence the 30 % threshold.
- **A photo saved as PNG produces monstrous derivatives**: one `1536x850`
  derivative weighed 2.7 MB, twice its own original.
- **Pixabay with a standard account tops out at 1280 px** (2026-09-07). Its API
  declares `imageWidth: 6000`, but `fullHDURL` (1920) and `imageURL` (original)
  are **not returned** unless Pixabay approves full access; the only downloadable
  field is `largeImageURL`, scaled to 1280 on its longest side. In practice,
  **with the default `min_width` of 1600, Pixabay contributes nothing** (all of
  it lands in `discarded_too_small`). To use it, request full access or search
  with `min_width=1280`.
- **The NPS API publishes no dimensions.** Without them every photo fell into
  `discarded_unknown_size` and the source was invisible. The adapter **measures**
  them by downloading only the file header (a few KB, with Pillow's incremental
  parser), and only for the candidates it is about to return. Real measurements:
  1800x1012, 2100x1112, 1800x1192. Only photos credited to the NPS are accepted:
  a third-party photo on their site is not public domain.
- **Unsplash's `fit=max` caps the WIDTH, not the longest side.** Mixing them up
  swaps the figures on portrait photos: a 3024x4032 original served with
  `w=2400` is 2400x3200, not 1800x2400.
- **WordPress has no media trash by default** (`MEDIA_TRASH` is off): a REST
  `DELETE` without `force` fails, and with `?force=true` it deletes permanently.
  **That is why this server exposes no delete tool.** If one is ever added, it
  should require explicit confirmation.
- **Yoast SEO's `og:image` does not refresh on the first try** when
  `featured_media` changes over REST. Seen on a published post on 2026-09-06 and
  **reproduced on 2026-09-07** on a draft, switching the featured image from A
  to B:

  | | `featured_media` | `og:image` | JSON-LD |
  |---|---|---|---|
  | after the **first** POST | B | **A** ← keeps the old one | B |
  | after repeating **the same** POST | B | B | B |

  It is not a cache, it is Yoast's indexables table: the JSON-LD updates at once
  and the `og:image` does not. That is why `set_featured` **repeats the same POST
  on purpose**; on a site without Yoast the second request is harmless. Even so,
  check the `og:image` of the published post.

**Known limitations of this version:**

- All six sources were **tested against their real APIs** (2026-09-07). Pixabay
  works but contributes nothing with the default `min_width`, because of the
  1280 px ceiling explained above.
- `upload_to_wordpress` was **tested end to end** against a live WordPress site
  (2026-09-07): a plain media upload, and an upload with `attach_to_post` +
  `set_featured` on a draft, checking afterwards through the API that
  `featured_media`, Yoast's `og:image` and the JSON-LD all pointed at the right
  image.
- `inspect_image` only verifies Wikimedia. Other sources would need their API key
  and a single-item lookup adapter.
- Unsplash's guidelines ask you to ping its download endpoint when a photo is
  used. The `download_location` field is exposed in the results, but the server
  **does not call it yet**.
- Openverse sometimes returns `width`/`height` as `null`. With a non-zero
  `min_width` those candidates fall into `discarded_unknown_size`.

---

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest        # offline: every network call is mocked
python -m ruff check .
python tools/make_demo.py   # regenerates assets/demo.svg
```

The tests never reach the network: HTTP goes through `httpx.MockTransport`, any
unmocked request fails the test, and the environment is cleared so a local
`.env` cannot leak keys into the suite. It runs in about two seconds. CI runs
lint and tests on Python 3.11, 3.12 and 3.13.

---

## Disclaimer

This project is not affiliated with Wikimedia, Openverse, Unsplash, Pexels,
Pixabay, the National Park Service or WordPress. It reads the licence each
source publishes and refuses to guess, but it is not legal advice: you remain
responsible for how you use each image. Respect each API's terms of use and
rate limits.

## License

[MIT](LICENSE) © 2026 John Orellana
