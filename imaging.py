"""
Descarga y procesado de imagen
==============================
Commons solo sirve los anchos de su lista de buckets: pedir /thumb/1600px- da
400, y 1280 o 1920 dan 200 (comprobado 06-09-2026). Ademas la API miente en
`thumbwidth`: con iiurlwidth=1600 responde "1600x1067" pero el thumburl que
entrega es el de 1920px y lo que descargas mide 1920. Por eso aqui nunca se
construye una URL de thumb a mano ni se confia en las dimensiones declaradas:
se pide el thumb a la API, se mide con Pillow y se reescala en local.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image, ImageFile

from common import COMMONS_API, HTTP_TIMEOUT, commons_title_from_url, headers

FORMAT_EXT = {"webp": ".webp", "png": ".png", "jpeg": ".jpg"}

# Tope de descarga: evita tragarse un TIFF de 200 MB por accidente.
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024


def output_dir() -> Path:
    directory = Path(tempfile.gettempdir()) / "img-license"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_stem(url: str) -> str:
    """Nombre de fichero legible a partir de la URL de origen."""
    raw = unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "image"
    stem = Path(raw).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return (stem or "image")[:80]


def commons_thumb_url(url: str, target_width: int) -> tuple[str | None, str]:
    """Thumb de Commons de ancho >= target_width, pedido a la API.

    Devuelve (url, explicacion). Si algo falla se devuelve (None, motivo) y el
    llamante se queda con la URL original.
    """
    title, pageid, _ = commons_title_from_url(url)
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "imageinfo",
        "iiprop": "url|size",
        "iiurlwidth": str(max(target_width, 1)),
    }
    if pageid:
        params["pageids"] = str(pageid)
    elif title:
        params["titles"] = title
    else:
        return None, "the file could not be identified on Commons"

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers(), follow_redirects=True) as client:
            response = client.get(COMMONS_API, params=params)
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages") or []
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return None, f"the Commons API did not answer ({type(exc).__name__})"

    if not pages or pages[0].get("missing"):
        return None, "the file does not exist on Commons"
    infos = pages[0].get("imageinfo") or []
    if not infos:
        return None, "no imageinfo in the Commons response"

    info = infos[0]
    thumb_url = info.get("thumburl")
    original_width = info.get("width")
    # Si el original ya es mas pequeno que el objetivo, el thumb no aporta nada.
    if isinstance(original_width, int) and original_width <= target_width:
        return info.get("url"), "the original is already narrower than the target"
    if not thumb_url:
        return info.get("url"), "Commons offered no thumbnail; using the original"
    return thumb_url, "Commons thumbnail from the next bucket up"


def download(url: str) -> tuple[bytes, str]:
    with httpx.Client(timeout=60.0, headers=headers(), follow_redirects=True) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"the file exceeds the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB download cap"
                    )
                chunks.append(chunk)
    return b"".join(chunks), content_type


def probe_dimensions(url: str, max_bytes: int = 65536) -> tuple[int, int] | None:
    """Dimensiones reales de una imagen remota leyendo solo su cabecera.

    Descarga por trozos y se los va dando al parser incremental de Pillow, que
    resuelve el tamano en cuanto ha visto la cabecera: unos pocos KB en JPEG y
    PNG. Para fuentes que no publican dimensiones (NPS) es la unica forma de
    saber si una foto sirve sin bajarsela entera.
    """
    parser = ImageFile.Parser()
    try:
        with httpx.Client(timeout=20.0, headers=headers(), follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                read = 0
                for chunk in response.iter_bytes():
                    parser.feed(chunk)
                    read += len(chunk)
                    if parser.image is not None:
                        return parser.image.size
                    if read >= max_bytes:
                        return None
    except (httpx.HTTPError, OSError, ValueError):
        return None
    return parser.image.size if parser.image is not None else None


def scaled_to_fit(width: Any, height: Any, longest_side: int) -> tuple[int | None, int | None]:
    """Dimensiones tras encajar la imagen en un lado maximo, sin ampliar.

    Es lo que hace Pixabay con `largeImageURL`: limita el lado mayor.
    """
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None, None
    longest = max(width, height)
    if longest <= longest_side:
        return width, height
    factor = longest_side / longest
    return max(1, round(width * factor)), max(1, round(height * factor))


def scaled_to_width(width: Any, height: Any, max_width: int) -> tuple[int | None, int | None]:
    """Dimensiones tras limitar solo el ANCHO, sin ampliar.

    Es lo que hace `?w=N&fit=max` de Unsplash: acota el ancho, no el lado mayor.
    Confundir las dos cosas da cifras cambiadas en las fotos verticales.
    """
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None, None
    if width <= max_width:
        return width, height
    return max_width, max(1, round(height * max_width / width))


def looks_like_photo(image: Image.Image) -> bool:
    """Foto (muchos colores, sin alfa) frente a grafico con texto o transparencia."""
    if image.mode in ("RGBA", "LA", "P"):
        return False
    return image.getcolors(maxcolors=20000) is None


def save_options_for(format: str, quality: int) -> dict[str, Any]:
    if format == "webp":
        return {"quality": quality, "method": 6}
    if format == "jpeg":
        return {"quality": quality, "optimize": True, "progressive": True}
    return {"optimize": True}
