"""
Img-License MCP
===============
Servidor MCP (FastMCP, transporte stdio) para ilustrar articulos de un blog con
imagenes cuya licencia se ha verificado EN ORIGEN, convertirlas a
WebP al tamano correcto, generar el credito de atribucion cuando la licencia lo
exige y subirlas a WordPress con sus metadatos de procedencia.

Herramientas expuestas:
  1. search_images        -> candidatas con licencia determinada, por fuente
  2. inspect_image        -> relee la licencia de una imagen concreta en origen
  3. prepare_image        -> descarga, reescala y convierte a WebP
  4. build_attribution    -> HTML del parrafo de credito
  5. upload_to_wordpress  -> sube el fichero con sus metadatos y lo asigna

Regla de oro: una candidata cuya licencia no se puede leer del campo que publica
su fuente NO aparece en la respuesta. Nunca se deduce del dominio, del buscador
ni del thumbnail. Se descarta en silencio y se cuenta.

Modulos de apoyo: common (identidad HTTP), licensing (motor de licencias),
sources (un adaptador por fuente), imaging (descarga y Pillow), wordpress.

Ejecucion:  python server.py
"""

from __future__ import annotations

import html
import io
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlparse

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from PIL import Image
from pydantic import Field

import imaging
import sources as src
from common import now_iso, user_agent
from licensing import attribution_needed, passes_filter
from wordpress import WP_MIMES, build_description, wp_config

load_dotenv()

mcp = FastMCP(
    name="img-license",
    instructions=(
        "License-safe image toolkit for illustrating blog articles. Use "
        "`search_images` to find photos whose licence has been read from the "
        "source API - anything unverifiable is dropped, never suggested; "
        "`inspect_image` to re-check a single image that arrived some other "
        "way; `prepare_image` to downscale and convert to WebP before "
        "publishing; `build_attribution` to produce the credit paragraph when "
        "the licence requires it; and `upload_to_wordpress` to publish the "
        "file with its provenance metadata. Always prepare an image before "
        "uploading it, and never invent an author, a licence or an alt text."
    ),
)

SourceName = Literal["commons", "openverse", "unsplash", "pexels", "pixabay", "nps"]


# --------------------------------------------------------------------------- #
# Utilidades de las tools
# --------------------------------------------------------------------------- #

def _dedupe_key(entry: dict[str, Any]) -> str:
    """Openverse reindexa Commons: el mismo fichero llega por las dos vias."""
    url = (entry.get("file_url") or "").split("?", 1)[0]
    return unquote(url).lower()


def _interleave(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Mezcla por rondas para que una fuente no monopolice los primeros puestos."""
    mixed: list[dict[str, Any]] = []
    for row in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if row < len(group):
                mixed.append(group[row])
    return mixed


# --------------------------------------------------------------------------- #
# Tool 1: busqueda de imagenes filtrada por licencia
# --------------------------------------------------------------------------- #

@mcp.tool
def search_images(
    query: Annotated[
        str,
        Field(
            description=(
                "What the photo should show (e.g. 'Santorini blue domes at sunset'). English works "
                "best: most source metadata is in English."
            )
        ),
    ],
    license_filter: Annotated[
        Literal["commercial", "no_attribution", "any"],
        Field(
            description=(
                "'commercial' (default): commercial use AND modification allowed - public domain, "
                "CC0, CC BY, CC BY-SA; NC and ND excluded. 'no_attribution': only licences needing "
                "no credit line. 'any': everything whose licence could be determined, NC/ND included, "
                "flagged so you can judge case by case."
            )
        ),
    ] = "commercial",
    min_width: Annotated[
        int,
        Field(
            ge=0,
            le=10000,
            description="Minimum original width in pixels. 1600 keeps only photos usable at full content width.",
        ),
    ] = 1600,
    sources: Annotated[
        list[SourceName] | None,
        Field(
            description=(
                "Restrict to these sources. Omit to use every source available; keyed "
                "sources are skipped when their API key is missing."
            ),
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum number of candidates to return.")] = 12,
) -> dict[str, Any]:
    """Search for photos whose licence is verified at the source.

    Only candidates whose licence could be READ from the source API are
    returned. Anything unverifiable is dropped silently and merely counted in
    `discarded_unknown_license` - it is never surfaced, not even with a warning,
    because a licence that cannot be cited cannot be published.

    Licences are never inferred from the domain, the search engine or the
    thumbnail: they come from Commons' `extmetadata` or the source's own licence
    field, or the image does not appear at all.

    Args:
        query: Subject of the photo; English works best.
        license_filter: Which licences are acceptable. See the parameter help.
        min_width: Minimum ORIGINAL width in pixels; `prepare_image` never
            upscales, so this is what caps the publishable size.
        sources: Subset of sources to query. Omit for all available ones.
        limit: Maximum candidates returned, after filtering and de-duplication.

    Returns:
        dict with `results` (source_page_url, file_url, license, license_url,
        attribution_required, allows_commercial, allows_modification, author,
        author_url, width, height, bytes, source), the discard counters and
        `sources_skipped` explaining which sources were not queried and why.
    """
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "Describe what the photo should show."}

    requested = list(dict.fromkeys(sources or src.ALL_SOURCES))
    unknown = [s for s in requested if s not in src.SOURCE_ENV]
    if unknown:
        return {
            "ok": False,
            "error": f"Unknown sources: {unknown}. Valid: {list(src.ALL_SOURCES)}.",
        }

    sources_used: list[str] = []
    sources_skipped: dict[str, str] = {}
    source_errors: dict[str, str] = {}
    groups: list[list[dict[str, Any]]] = []

    examined = 0
    discarded_unknown_license = 0
    discarded_not_image = 0

    # Se pide de mas a cada fuente: el filtro de licencia y el de ancho recortan
    # bastante, y con `limit` justo la respuesta se quedaria corta.
    per_source = min(50, max(limit * 3, 10))

    for source in requested:
        env_var = src.SOURCE_ENV[source]
        if env_var is not None and not src.source_key(source):
            sources_skipped[source] = f"{env_var} is not set"
            continue

        try:
            if source == "commons":
                found, seen, not_image = src.commons_search(query, per_source)
            else:
                found, seen, not_image = src.ADAPTERS[source](query, per_source, license_filter)
        except httpx.HTTPError as exc:
            source_errors[source] = f"{type(exc).__name__}: {exc}"
            continue
        except (ValueError, KeyError) as exc:
            source_errors[source] = f"unreadable response: {type(exc).__name__}: {exc}"
            continue

        sources_used.append(source)
        examined += seen
        discarded_not_image += not_image
        discarded_unknown_license += seen - len(found) - not_image
        groups.append(found)

    if not sources_used:
        return {
            "ok": False,
            "query": query,
            "error": "No source answered.",
            "sources_skipped": sources_skipped,
            "source_errors": source_errors,
        }

    discarded_by_license = 0
    discarded_too_small = 0
    discarded_unknown_size = 0
    seen_keys: set[str] = set()
    duplicates = 0
    results: list[dict[str, Any]] = []

    for entry in _interleave(groups):
        if not entry.get("file_url") or not entry.get("source_page_url"):
            continue

        if not passes_filter(entry, license_filter):
            discarded_by_license += 1
            continue

        width = entry.get("width")
        if min_width:
            if not isinstance(width, int) or width <= 0:
                discarded_unknown_size += 1
                continue
            if width < min_width:
                discarded_too_small += 1
                continue

        key = _dedupe_key(entry)
        if key in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(key)

        results.append(entry)
        if len(results) >= limit:
            break

    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "license_filter": license_filter,
        "min_width": min_width,
        "fetched_at": now_iso(),
        "sources_used": sources_used,
        "candidates_examined": examined,
        "discarded_unknown_license": discarded_unknown_license,
        "discarded_not_image": discarded_not_image,
        "discarded_by_license": discarded_by_license,
        "discarded_too_small": discarded_too_small,
        "discarded_unknown_size": discarded_unknown_size,
        "duplicates_merged": duplicates,
        "count": len(results),
        "results": results,
    }
    if sources_skipped:
        payload["sources_skipped"] = sources_skipped
    if source_errors:
        payload["source_errors"] = source_errors
        payload["degraded"] = f"Sources that failed: {list(source_errors)}."
    if not results:
        payload["hint"] = (
            "No candidates left after filtering. Try a lower `min_width`, a broader "
            "query, or license_filter='any' to also see NC/ND licences."
        )
    return payload


# --------------------------------------------------------------------------- #
# Tool 2: verificacion individual de una imagen
# --------------------------------------------------------------------------- #

@mcp.tool
def inspect_image(
    url: Annotated[
        str,
        Field(
            description=(
                "Image URL to verify: a Wikimedia Commons file page, a curid link, "
                "or a direct upload.wikimedia.org file or thumbnail URL."
            )
        ),
    ],
) -> dict[str, Any]:
    """Re-read the licence of one image at its source.

    Use it to confirm an image that reached you some other way - a colleague's
    link, an old bookmark, a result from another tool - before publishing it.
    The licence is read again from the source API, never from the URL itself.

    Supported today: Wikimedia Commons, in all its URL shapes (file page,
    `curid`, direct `upload.wikimedia.org` file or thumbnail). Other hosts
    return `ok: False` rather than a guess.

    Args:
        url: The image or file-page URL to verify.

    Returns:
        dict with the same licence block `search_images` returns, plus
        `resolved_by` explaining how the file was identified. On failure,
        `ok: False` with the reason - never an assumed licence.
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "A URL is required."}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "The URL must start with http:// or https://."}

    host = urlparse(url).netloc.lower()
    title, pageid, resolved_by = src.commons_title_from_url(url)

    if title is None and pageid is None:
        return {
            "ok": False,
            "url": url,
            "host": host,
            "error": f"The licence cannot be determined for this host ({resolved_by}).",
            "hint": (
                "Only commons.wikimedia.org, upload.wikimedia.org and thumb.wikimedia.org "
                "can be verified today. Unsplash, Pexels, Pixabay and NPS images should be "
                "found through `search_images`. The licence is never inferred from the domain."
            ),
        }

    try:
        entry = src.commons_details(title=title, pageid=pageid)
    except httpx.HTTPError as exc:
        return {"ok": False, "url": url, "error": f"Commons did not answer: {type(exc).__name__}: {exc}"}
    except (ValueError, KeyError) as exc:
        return {"ok": False, "url": url, "error": f"Unreadable response from Commons: {exc}"}

    if entry is None:
        return {
            "ok": False,
            "url": url,
            "resolved_by": resolved_by,
            "error": (
                "Licence cannot be determined: the file does not exist, is not a bitmap, "
                "or its licence code is not recognised."
            ),
            "hint": "Without a licence readable at the source, this image cannot be published.",
        }

    return {"ok": True, "url": url, "resolved_by": resolved_by, "fetched_at": now_iso(), **entry}


# --------------------------------------------------------------------------- #
# Tool 3: descarga, reescalado y conversion
# --------------------------------------------------------------------------- #

@mcp.tool
def prepare_image(
    url: Annotated[str, Field(description="Direct image URL, or a Wikimedia Commons file page URL.")],
    max_width: Annotated[
        int,
        Field(
            ge=64,
            le=5000,
            description="Target width in pixels. The image is never upscaled: a narrower original is kept as is.",
        ),
    ] = 1600,
    format: Annotated[
        Literal["webp", "png", "jpeg"],
        Field(description="Output format. WebP for photos; PNG only for graphics with text or transparency."),
    ] = "webp",
    quality: Annotated[
        int, Field(ge=1, le=100, description="Encoder quality. 82 is the measured sweet spot for WebP photos.")
    ] = 82,
) -> dict[str, Any]:
    """Download an image, downscale it to `max_width` and convert it.

    Never upscales: if the source is narrower than `max_width` it is kept at its
    own size. For Wikimedia URLs the thumbnail is requested from the API instead
    of downloading the full original - a 6558px, 17 MB photo becomes a 1 MB
    download - because Commons only serves a fixed list of thumbnail widths and
    reports dimensions it does not actually deliver.

    Two savings are reported. `saving_pct` is the headline: downloaded bytes vs
    produced bytes, resize and format together. Below 30% it raises an explicit
    warning, because that is where reconverting stops paying off - high-detail
    textures without repeatable structure (crowds, satellite cloud) barely
    compress. `format_saving_pct` is extra context: the same final pixels
    re-encoded as JPEG, isolating the codec gain. It is a harsher yardstick than
    comparing against the source JPEG, so it runs low on ordinary photos and
    never triggers a warning on its own.

    Args:
        url: Image URL or Commons file page.
        max_width: Target width in pixels; the image is never enlarged.
        format: 'webp' (default), 'png' for graphics/transparency, or 'jpeg'.
        quality: Encoder quality, 1-100.

    Returns:
        dict with `output_path` (a temporary file), `bytes_before`,
        `bytes_after`, `saving_pct`, `format_saving_pct`, the final `width` and
        `height`, and `warnings` with any explicit caveat.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "The URL must start with http:// or https://."}

    host = urlparse(url).netloc.lower()
    download_url = url
    download_note = "direct download of the given URL"

    if host in src.WIKIMEDIA_HOSTS:
        thumb_url, why = imaging.commons_thumb_url(url, max_width)
        if thumb_url:
            download_url, download_note = thumb_url, why
        else:
            download_note = f"used the original URL: {why}"

    try:
        raw, content_type = imaging.download(download_url)
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "url": url,
            "download_url": download_url,
            "error": f"Download failed: {type(exc).__name__}: {exc}",
            "hint": (
                "Wikimedia answers 403 to generic User-Agents. Check "
                "IMG_LICENSE_USER_AGENT if you changed it."
            ),
        }
    except ValueError as exc:
        return {"ok": False, "url": url, "download_url": download_url, "error": str(exc)}

    bytes_before = len(raw)
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "download_url": download_url,
            "content_type": content_type,
            "error": f"The downloaded content is not a readable image: {type(exc).__name__}: {exc}",
        }

    source_width, source_height = image.size
    warnings: list[str] = []

    # --- Reescalado: solo hacia abajo ---
    resized = False
    if source_width > max_width:
        new_height = max(1, round(source_height * max_width / source_width))
        image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)
        resized = True
    elif source_width < max_width:
        warnings.append(
            f"The source is {source_width}px wide, narrower than the requested {max_width}px. "
            "It is not upscaled: it keeps its real size."
        )

    # --- Modo de color segun formato de salida ---
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if format == "jpeg":
        if has_alpha:
            warnings.append("JPEG has no transparency: the alpha channel was dropped.")
        image = image.convert("RGB")
    elif has_alpha:
        image = image.convert("RGBA")
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    if format == "png" and imaging.looks_like_photo(image):
        warnings.append(
            "PNG for a photo produces huge files (a 1536x850 derivative once weighed 2.7 MB, "
            "twice its own original). Use WebP unless you need transparency or crisp text."
        )

    # --- Codificacion ---
    stem = imaging.safe_stem(url)
    output_path = imaging.output_dir() / f"{stem}-{image.size[0]}px{imaging.FORMAT_EXT[format]}"
    try:
        image.save(output_path, format=format.upper(), **imaging.save_options_for(format, quality))
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"Encoding failed: {type(exc).__name__}: {exc}"}

    bytes_after = output_path.stat().st_size
    saving_pct = round((bytes_before - bytes_after) / bytes_before * 100, 1) if bytes_before else 0.0

    # --- Ganancia atribuible solo al formato, a igualdad de pixeles ---
    # Reencodea los mismos pixeles finales como JPEG y compara. Es informativo:
    # un JPEG recien codificado con optimize+progressive es un rival mucho mas
    # duro que el JPEG de origen, asi que este numero sale bajo en fotos
    # normales (5-20 %). NO dispara avisos por si solo.
    format_saving_pct: float | None = None
    same_size_jpeg_bytes: int | None = None
    if format != "jpeg" and not has_alpha:
        try:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
            same_size_jpeg_bytes = buffer.tell()
            if same_size_jpeg_bytes:
                format_saving_pct = round(
                    (same_size_jpeg_bytes - bytes_after) / same_size_jpeg_bytes * 100, 1
                )
        except Exception:
            format_saving_pct = None

    # El umbral del 30 % va sobre el ahorro real (lo que pesaba frente a lo que
    # pesa), que es como estan medidos los casos documentados.
    if saving_pct < 0:
        warnings.append(
            f"The output is LARGER than the source ({abs(saving_pct)}% more): "
            f"{bytes_before:,} -> {bytes_after:,} bytes. Do not upload this file."
        )
    elif saving_pct < 30:
        warnings.append(
            f"Reconverting barely pays off: it only saves {saving_pct}% "
            f"({bytes_before:,} -> {bytes_after:,} bytes, threshold 30%). This happens with "
            "high-detail textures without repeatable structure."
        )

    return {
        "ok": True,
        "url": url,
        "download_url": download_url,
        "download_note": download_note,
        "output_path": str(output_path),
        "format": format,
        "quality": quality,
        "source_width": source_width,
        "source_height": source_height,
        "width": image.size[0],
        "height": image.size[1],
        "resized": resized,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "saving_pct": saving_pct,
        "same_size_jpeg_bytes": same_size_jpeg_bytes,
        "format_saving_pct": format_saving_pct,
        "warnings": warnings,
        "prepared_at": now_iso(),
    }


# --------------------------------------------------------------------------- #
# Tool 4: parrafo de credito
# --------------------------------------------------------------------------- #

_SOURCE_DISPLAY = {
    "commons": "Wikimedia Commons",
    "openverse": "Openverse",
    "unsplash": "Unsplash",
    "pexels": "Pexels",
    "pixabay": "Pixabay",
    "nps": "the National Park Service",
}


def _link(url: str | None, text: str, rel: str | None = None) -> str:
    """Enlace HTML, o el texto pelado si no hay URL utilizable."""
    safe_text = html.escape(text)
    if not url or not url.startswith(("http://", "https://")):
        return safe_text
    rel_attr = f' rel="{rel}"' if rel else ""
    return f'<a href="{html.escape(url, quote=True)}"{rel_attr}>{safe_text}</a>'


@mcp.tool
def build_attribution(
    author: Annotated[str, Field(description="Author exactly as the source states it. Never invent or reformat it.")],
    license_name: Annotated[str, Field(description="Exact licence name, e.g. 'CC BY-SA 4.0'.")],
    license_url: Annotated[str, Field(description="Canonical licence deed URL.")],
    file_page_url: Annotated[str, Field(description="Source page of the file, not the search engine's page.")],
    source: Annotated[
        str,
        Field(
            description=(
                "Source id ('commons', 'openverse', 'unsplash', 'pexels', 'pixabay', 'nps') "
                "or a display name to use as is."
            )
        ),
    ] = "commons",
    author_url: Annotated[
        str | None, Field(description="Author profile URL. Omitted, the author is rendered as plain text.")
    ] = None,
) -> dict[str, Any]:
    """Build the credit paragraph for an image whose licence requires attribution.

    Output shape: `Photo: <author>, <LICENCE>, via <Source>`, with the author,
    the licence and the file page each linked. Only meaningful for licences that
    require credit - for CC0, public domain or the stock licences the response
    still returns the HTML but flags `attribution_required: false` so you can
    skip it.

    Args:
        author: Author string, copied from the source.
        license_name: Exact licence name.
        license_url: Licence deed URL.
        file_page_url: The file's page at the source.
        source: Source id or display name.
        author_url: Author profile URL, optional.

    Returns:
        dict with `html` (ready to paste into the post), `text` (same credit
        without markup), `attribution_required` and any `notes`.
    """
    author = (author or "").strip()
    license_name = (license_name or "").strip()
    if not author:
        return {"ok": False, "error": "An author is required to build a credit."}
    if not license_name:
        return {"ok": False, "error": "The exact licence name is required."}

    display = _SOURCE_DISPLAY.get(source.strip().lower(), source.strip())
    notes: list[str] = []

    required = attribution_needed(license_name)
    if required is False:
        notes.append(
            f"{license_name} does not require attribution. The credit is a courtesy, not an "
            "obligation: you can leave it out."
        )
    elif required is None:
        notes.append(
            f"Could not tell from '{license_name}' whether attribution is required. "
            "Check the licence at the source before publishing without a credit."
        )
    if not author_url:
        notes.append("No `author_url`: the author is rendered as plain text, without a link.")
    if not license_url:
        notes.append("No `license_url`: the licence is rendered as plain text, without a link.")

    body = (
        f"Photo: {_link(author_url, author)}, "
        f"{_link(license_url, license_name, rel='license')}, "
        f"via {_link(file_page_url, display)}"
    )
    return {
        "ok": True,
        "html": f"<p>{body}</p>",
        "text": f"Photo: {author}, {license_name}, via {display}",
        "attribution_required": required,
        "source_display": display,
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# Tool 5: subida a WordPress
# --------------------------------------------------------------------------- #
# Dos peticiones por diseno: la de /media sube el binario y la segunda escribe
# los metadatos, porque WordPress ignora alt_text y description en el multipart.
# Los avisos del sitio estan documentados en wordpress.py.

@mcp.tool
def upload_to_wordpress(
    file_path: Annotated[
        str,
        Field(description="Local file to upload - the output of `prepare_image`, never the untouched original."),
    ],
    title: Annotated[str, Field(description="Media library title.")],
    alt_text: Annotated[
        str,
        Field(
            description=(
                "MANDATORY alt text in the site's language, describing the image for a reader who "
                "cannot see it. Must come from you; it is never invented here."
            )
        ),
    ],
    source_url: Annotated[str, Field(description="Where the image came from - the source page, not the file URL.")],
    author: Annotated[str, Field(description="Author exactly as the source states it.")],
    license_name: Annotated[str, Field(description="Exact licence name, e.g. 'CC BY-SA 4.0'.")],
    original_width: Annotated[
        int, Field(ge=1, description="Width of the ORIGINAL file at the source, before any resizing.")
    ],
    original_height: Annotated[int, Field(ge=1, description="Height of the ORIGINAL file at the source.")],
    caption: Annotated[
        str,
        Field(
            description=(
                "Caption shown under the image. The credit paragraph belongs here when the licence requires it."
            )
        ),
    ] = "",
    notes: Annotated[
        str,
        Field(
            description=(
                "Extra free text prepended to the description. The provenance block is always appended regardless."
            )
        ),
    ] = "",
    attach_to_post: Annotated[
        int | None, Field(description="Post ID to attach the media to. Required if `set_featured` is true.")
    ] = None,
    set_featured: Annotated[
        bool,
        Field(
            description=(
                "Also set the media as that post's featured image. The POST is repeated on purpose - "
                "see the Yoast note."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Upload a prepared image to WordPress with its provenance metadata.

    Uploads the binary to `/wp-json/wp/v2/media`, then writes the metadata in a
    second request because WordPress ignores `alt_text` and `description` in the
    multipart body. The description ALWAYS carries source URL, author, exact
    licence, original resolution and download date - they are built from the
    required parameters, so they cannot be left out.

    `alt_text` is mandatory and must be written by you, in the site's language.
    Nothing here invents it.

    Only ever upload the output of `prepare_image`, never the untouched original.

    Args:
        file_path: Local prepared file.
        title: Media library title.
        alt_text: Mandatory alt text.
        source_url: Source page of the image.
        author: Author as stated by the source.
        license_name: Exact licence name.
        original_width: Original width at the source.
        original_height: Original height at the source.
        caption: Caption; put the credit paragraph here when required.
        notes: Extra description text, prepended to the provenance block.
        attach_to_post: Post ID to attach to.
        set_featured: Set as that post's featured image.

    Returns:
        dict with `media_id`, `media_url`, `edit_url`, the generated `sizes`,
        and `featured_media` details when it was set.
    """
    config = wp_config()
    if config is None:
        return {
            "ok": False,
            "error": "WordPress credentials are missing.",
            "hint": "Set WP_BASE_URL, WP_USER and WP_APP_PASSWORD in .env (see .env.example).",
        }
    base, user, password = config

    alt_text = (alt_text or "").strip()
    if not alt_text:
        return {
            "ok": False,
            "error": "`alt_text` is mandatory and cannot be empty.",
            "hint": "Write it yourself, describing what the image shows. Nothing here invents it.",
        }
    if set_featured and not attach_to_post:
        return {"ok": False, "error": "`set_featured` needs `attach_to_post` with the post ID."}

    path = Path(file_path)
    if not path.is_file():
        return {"ok": False, "error": f"File not found: {file_path}"}
    mime = WP_MIMES.get(path.suffix.lower())
    if mime is None:
        return {
            "ok": False,
            "error": f"Unsupported extension: {path.suffix}. Allowed: {sorted(WP_MIMES)}.",
        }

    description = build_description(
        path, source_url, author, license_name, original_width, original_height, notes
    )
    auth = httpx.BasicAuth(user, password)
    media_endpoint = f"{base}/wp-json/wp/v2/media"

    try:
        with httpx.Client(timeout=120.0, headers={"User-Agent": user_agent()},
                          follow_redirects=True, auth=auth) as client:
            # 1) Binario.
            with path.open("rb") as handle:
                response = client.post(
                    media_endpoint,
                    files={"file": (path.name, handle, mime)},
                    data={"title": title},
                )
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "step": "file upload",
                    "status": response.status_code,
                    "error": response.text[:600],
                    "hint": (
                        "401/403 is usually a mistyped Application Password (keep its spaces). "
                        "413 is PHP's upload size limit."
                    ),
                }
            media_id = response.json().get("id")

            # 2) Metadatos: WordPress ignora alt_text y description en el multipart.
            meta_response = client.post(
                f"{media_endpoint}/{media_id}",
                json={
                    "title": title,
                    "alt_text": alt_text,
                    "caption": caption,
                    "description": description,
                },
            )
            if meta_response.status_code >= 400:
                return {
                    "ok": False,
                    "step": "metadata",
                    "media_id": media_id,
                    "status": meta_response.status_code,
                    "error": meta_response.text[:600],
                    "hint": "The file WAS uploaded. Fix the issue and repeat only this step.",
                }
            media = meta_response.json()

            result: dict[str, Any] = {
                "ok": True,
                "media_id": media_id,
                "media_url": media.get("source_url"),
                "edit_url": f"{base}/wp-admin/post.php?post={media_id}&action=edit",
                "alt_text": media.get("alt_text"),
                "title": (media.get("title") or {}).get("rendered"),
                "mime": media.get("mime_type"),
                "description_sent": description,
                "uploaded_at": now_iso(),
                "warnings": [],
            }

            sizes = (media.get("media_details") or {}).get("sizes") or {}
            result["sizes"] = {
                name: f"{data.get('width')}x{data.get('height')}" for name, data in sizes.items()
            }

            # 3) Adjuntar a la entrada y, si toca, imagen destacada.
            if attach_to_post:
                attach = client.post(f"{media_endpoint}/{media_id}", json={"post": attach_to_post})
                result["attached_to_post"] = attach_to_post
                if attach.status_code >= 400:
                    result["warnings"].append(
                        f"Could not attach the media to post {attach_to_post}: "
                        f"{attach.status_code} {attach.text[:200]}"
                    )

            if set_featured:
                post_endpoint = f"{base}/wp-json/wp/v2/posts/{attach_to_post}"
                payload = {"featured_media": media_id}
                # El POST se repite a proposito: la tabla de indexables de Yoast
                # no refresca el og:image a la primera.
                first = client.post(post_endpoint, json=payload)
                second = client.post(post_endpoint, json=payload)
                result["featured_media"] = {
                    "post_id": attach_to_post,
                    "first_status": first.status_code,
                    "second_status": second.status_code,
                    "double_post": True,
                    "note": (
                        "POST sent twice on purpose: when the featured image changes over REST, "
                        "Yoast SEO does not refresh og:image on the first request (it is its "
                        "indexables table, not a cache). Check the og:image of the published post."
                    ),
                }
                if first.status_code >= 400 and second.status_code >= 400:
                    result["warnings"].append(
                        f"Could not set the featured image: {second.status_code} "
                        f"{second.text[:200]}"
                    )

    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"WordPress did not answer: {type(exc).__name__}: {exc}"}
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": f"Unreadable response from WordPress: {type(exc).__name__}: {exc}"}

    return result


if __name__ == "__main__":
    mcp.run()
