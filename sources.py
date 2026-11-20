"""
Fuentes de imagenes
===================
Un adaptador por fuente. Todos devuelven la misma tupla:
    (candidatas normalizadas, examinadas, descartadas por no ser bitmap)

Commons y Openverse no necesitan key. Las otras cuatro se activan solo si su
variable de entorno esta definida, y si no se omiten sin romper la busqueda.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from common import (
    COMMONS_API,
    HTTP_TIMEOUT,
    OPENVERSE_API,
    commons_title_from_url,
    first_href,
    headers,
    strip_tags,
)
from imaging import probe_dimensions, scaled_to_fit, scaled_to_width
from licensing import STOCK_LICENSES, parse_license

# --------------------------------------------------------------------------- #
# Registro de fuentes
# --------------------------------------------------------------------------- #

SOURCE_ENV: dict[str, str | None] = {
    "commons": None,
    "openverse": None,
    "unsplash": "UNSPLASH_ACCESS_KEY",
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
    "nps": "NPS_API_KEY",
}

ALL_SOURCES: tuple[str, ...] = tuple(SOURCE_ENV)

# Reexportada desde common para no romper a quien la importe de este modulo.
__all__ = ["commons_title_from_url"]


def source_key(source: str) -> str | None:
    env_var = SOURCE_ENV.get(source)
    if env_var is None:
        return None
    return (os.environ.get(env_var) or "").strip() or None


# --------------------------------------------------------------------------- #
# Wikimedia Commons
# --------------------------------------------------------------------------- #
# La licencia sale de `extmetadata`, por archivo. `License` es el codigo legible
# por maquina y `LicenseShortName` el nombre exacto que publica Commons: se usa
# el primero para decidir permisos y el segundo para mostrar.

COMMONS_MIMES = ("image/jpeg", "image/png", "image/webp")
WIKIMEDIA_HOSTS = ("commons.wikimedia.org", "upload.wikimedia.org", "thumb.wikimedia.org")


def _em(extmetadata: dict[str, Any], key: str) -> str | None:
    node = extmetadata.get(key)
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _commons_entry(page: dict[str, Any]) -> dict[str, Any] | None:
    """Una pagina de la API -> candidata normalizada, o None si no es utilizable."""
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    if info.get("mime") not in COMMONS_MIMES:
        return None

    extmetadata = info.get("extmetadata") or {}
    code = _em(extmetadata, "License")
    parsed = parse_license(code)
    if parsed is None:
        return None  # licencia no determinable -> fuera, sin aviso

    # Commons publica el nombre exacto; si esta, manda sobre el reconstruido.
    short_name = _em(extmetadata, "LicenseShortName")
    if short_name:
        parsed = {**parsed, "license": short_name}

    license_url = _em(extmetadata, "LicenseUrl")
    if license_url:
        parsed = {**parsed, "license_url": license_url}

    # `AttributionRequired` es explicito por archivo: manda sobre la inferencia.
    attribution_raw = (_em(extmetadata, "AttributionRequired") or "").lower()
    if attribution_raw in ("true", "false"):
        parsed = {**parsed, "attribution_required": attribution_raw == "true"}

    artist_raw = _em(extmetadata, "Artist")

    return {
        "source": "commons",
        "title": page.get("title", "").removeprefix("File:"),
        "source_page_url": info.get("descriptionurl"),
        "file_url": info.get("url"),
        "author": strip_tags(artist_raw),
        "author_url": first_href(artist_raw),
        "width": info.get("width"),
        "height": info.get("height"),
        "original_width": info.get("width"),
        "original_height": info.get("height"),
        "bytes": info.get("size"),
        "mime": info.get("mime"),
        "license_code": code,
        "restrictions": _em(extmetadata, "Restrictions") or None,
        **parsed,
    }


def _commons_query(params: dict[str, str]) -> list[dict[str, Any]]:
    base = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
    }
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers(), follow_redirects=True) as client:
        response = client.get(COMMONS_API, params={**base, **params})
        response.raise_for_status()
        return (response.json().get("query") or {}).get("pages") or []


def commons_search(query: str, limit: int) -> tuple[list[dict[str, Any]], int, int]:
    """Busca ficheros bitmap en Commons -> (candidatas, examinadas, no-bitmap).

    `filetype:bitmap` deja pasar TIFF, que no sirve para web y ademas suele
    llegar sin campo `License`. Se cuenta aparte para no inflar el contador de
    licencias ilegibles con ficheros que se descartan por formato.
    """
    pages = _commons_query({
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": "6",
        "gsrlimit": str(min(max(limit, 1), 50)),
    })
    # El generator no devuelve las paginas en orden de relevancia; `index` si.
    pages.sort(key=lambda p: p.get("index", 9999))

    entries: list[dict[str, Any]] = []
    not_bitmap = 0
    for page in pages:
        infos = page.get("imageinfo") or []
        if infos and infos[0].get("mime") not in COMMONS_MIMES:
            not_bitmap += 1
            continue
        entry = _commons_entry(page)
        if entry:
            entries.append(entry)
    return entries, len(pages), not_bitmap


def commons_details(title: str | None = None, pageid: int | None = None) -> dict[str, Any] | None:
    """Relee un fichero concreto de Commons por titulo (File:...) o por pageid."""
    if pageid:
        params = {"pageids": str(pageid)}
    elif title:
        params = {"titles": title}
    else:
        return None
    pages = _commons_query(params)
    if not pages or pages[0].get("missing"):
        return None
    return _commons_entry(pages[0])


# --------------------------------------------------------------------------- #
# Openverse
# --------------------------------------------------------------------------- #
# Agrega Commons, Flickr y otros. La licencia viene en `license` +
# `license_version`, ya normalizada por ellos. No necesita key.

# Codigos que Openverse acepta en su parametro `license`.
_OV_NO_ATTRIBUTION = "cc0,pdm"

# Limite duro de las peticiones anonimas (comprobado 06-09-2026): con
# page_size=21 la API responde 401 "page_size may not exceed 20 for anonymous
# requests". Para pedir mas hay que paginar, no subir el tamano de pagina.
_OV_MAX_PAGE_SIZE = 20
_OV_MAX_PAGES = 3


def _openverse_entry(result: dict[str, Any]) -> dict[str, Any] | None:
    code = result.get("license")
    parsed = parse_license(code, result.get("license_version"))
    if parsed is None:
        return None

    license_url = result.get("license_url")
    if license_url:
        parsed = {**parsed, "license_url": license_url}

    return {
        "source": "openverse",
        "provider": result.get("source") or result.get("provider"),
        "title": result.get("title"),
        "source_page_url": result.get("foreign_landing_url"),
        "file_url": result.get("url"),
        "author": result.get("creator"),
        "author_url": result.get("creator_url"),
        "width": result.get("width"),
        "height": result.get("height"),
        "original_width": result.get("width"),
        "original_height": result.get("height"),
        "bytes": result.get("filesize") or None,
        "mime": f"image/{result['filetype']}" if result.get("filetype") else None,
        "license_code": f"{code}-{result.get('license_version') or ''}".strip("-"),
        "restrictions": None,
        **parsed,
    }


def openverse_search(query: str, limit: int, license_filter: str) -> tuple[list[dict[str, Any]], int, int]:
    """Busca en Openverse. Pagina de 20 en 20: ver _OV_MAX_PAGE_SIZE."""
    page_size = min(max(limit, 1), _OV_MAX_PAGE_SIZE)
    params: dict[str, str] = {"q": query, "page_size": str(page_size), "mature": "false"}
    # Se filtra ya en el servidor de Openverse cuando se puede: menos ruido.
    if license_filter == "commercial":
        params["license_type"] = "commercial,modification"
    elif license_filter == "no_attribution":
        params["license"] = _OV_NO_ATTRIBUTION

    entries: list[dict[str, Any]] = []
    seen = 0
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers(), follow_redirects=True) as client:
        for page in range(1, _OV_MAX_PAGES + 1):
            response = client.get(OPENVERSE_API, params={**params, "page": str(page)})
            response.raise_for_status()
            results = response.json().get("results") or []
            seen += len(results)
            entries.extend(entry for r in results if (entry := _openverse_entry(r)))
            # Ultima pagina, o ya hay material de sobra para filtrar despues.
            if len(results) < page_size or len(entries) >= limit:
                break

    return entries, seen, 0


# --------------------------------------------------------------------------- #
# Fuentes con API key
# --------------------------------------------------------------------------- #
# Toda la libreria de estas fuentes esta bajo una unica licencia, asi que no hay
# que leerla por fichero: se toma de STOCK_LICENSES. Aun asi la candidata solo
# sale si la peticion a la API oficial ha ido bien; nada se deduce del dominio.
#
# AVISO: sin keys disponibles, estos cuatro adaptadores NO se han podido probar
# contra las APIs reales. El mapeo de campos sale de su documentacion.

# Ancho que se pide a images.unsplash.com con `fit=max`, que acota el ANCHO y
# no el lado mayor. Holgado para reescalar a 1600 sin ampliar, sin llegar a
# bajarse originales de 20 MP.
_UNSPLASH_MAX_WIDTH = 2400

# `largeImageURL` de Pixabay viene escalada a 1280 px en su lado mayor. Los
# campos imageWidth/imageHeight describen el ORIGINAL, que con una cuenta
# estandar no se puede descargar: fullHDURL e imageURL solo llegan si Pixabay te
# aprueba el acceso completo (verificado 07-09-2026: no vienen).
_PIXABAY_MAX_SIDE = 1280


def unsplash_search(query: str, limit: int, license_filter: str) -> tuple[list[dict[str, Any]], int, int]:
    """API oficial de Unsplash. El `napi` interno y las paginas de busqueda dan
    401 (comprobado 02-09-2026): solo sirve api.unsplash.com con Access Key."""
    params = {"query": query, "per_page": str(min(max(limit, 1), 30)), "content_filter": "high"}
    auth = headers({"Authorization": f"Client-ID {source_key('unsplash')}"})
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=auth, follow_redirects=True) as client:
        response = client.get("https://api.unsplash.com/search/photos", params=params)
        response.raise_for_status()
        results = response.json().get("results") or []

    entries = []
    for item in results:
        raw_url = ((item.get("urls") or {}).get("raw") or "").strip()
        page_url = ((item.get("links") or {}).get("html") or "").strip()
        if not raw_url or not page_url:
            continue
        user = item.get("user") or {}
        separator = "&" if "?" in raw_url else "?"
        # `width`/`height` son los del original, pero la URL que se entrega pide
        # w=2400: se declara lo que se puede descargar de verdad, no el original.
        served_w, served_h = scaled_to_width(item.get("width"), item.get("height"),
                                             _UNSPLASH_MAX_WIDTH)
        entries.append({
            "source": "unsplash",
            "title": item.get("description") or item.get("alt_description"),
            "source_page_url": page_url,
            # Parametros aceptados por images.unsplash.com; se pide holgado para
            # que prepare_image pueda reescalar hacia abajo sin ampliar.
            "file_url": f"{raw_url}{separator}w={_UNSPLASH_MAX_WIDTH}&q=85&fm=jpg&fit=max",
            "author": user.get("name"),
            "author_url": (user.get("links") or {}).get("html"),
            "width": served_w,
            "height": served_h,
            "original_width": item.get("width"),
            "original_height": item.get("height"),
            "bytes": None,
            "mime": "image/jpeg",
            "license_code": "unsplash",
            "restrictions": None,
            # Sus normas piden avisar a este endpoint al descargar. Se expone
            # para poder cumplirlo; este servidor todavia no lo llama.
            "download_location": (item.get("links") or {}).get("download_location"),
            **STOCK_LICENSES["unsplash"],
        })
    return entries, len(results), 0


def pexels_search(query: str, limit: int, license_filter: str) -> tuple[list[dict[str, Any]], int, int]:
    params = {"query": query, "per_page": str(min(max(limit, 1), 80))}
    auth = headers({"Authorization": source_key("pexels") or ""})
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=auth, follow_redirects=True) as client:
        response = client.get("https://api.pexels.com/v1/search", params=params)
        response.raise_for_status()
        photos = response.json().get("photos") or []

    entries = []
    for item in photos:
        file_url = (item.get("src") or {}).get("original")
        if not file_url or not item.get("url"):
            continue
        entries.append({
            "source": "pexels",
            "title": item.get("alt") or None,
            "source_page_url": item.get("url"),
            "file_url": file_url,
            "author": item.get("photographer"),
            "author_url": item.get("photographer_url"),
            "width": item.get("width"),
            "height": item.get("height"),
            "original_width": item.get("width"),
            "original_height": item.get("height"),
            "bytes": None,
            "mime": "image/jpeg",
            "license_code": "pexels",
            "restrictions": None,
            **STOCK_LICENSES["pexels"],
        })
    return entries, len(photos), 0


def pixabay_search(query: str, limit: int, license_filter: str) -> tuple[list[dict[str, Any]], int, int]:
    params = {
        "key": source_key("pixabay") or "",
        "q": query,
        "image_type": "photo",
        "safesearch": "true",
        "per_page": str(min(max(limit, 3), 200)),
    }
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers(), follow_redirects=True) as client:
        response = client.get("https://pixabay.com/api/", params=params)
        response.raise_for_status()
        hits = response.json().get("hits") or []

    entries = []
    for item in hits:
        # fullHDURL (1920) solo llega con acceso completo aprobado por Pixabay.
        file_url = item.get("fullHDURL") or item.get("largeImageURL")
        if not file_url or not item.get("pageURL"):
            continue
        author_url = None
        if item.get("user") and item.get("user_id"):
            author_url = f"https://pixabay.com/users/{item['user']}-{item['user_id']}/"
        # imageWidth/Height son los del original, que no se puede descargar: se
        # declara el tamano de la URL que si se entrega.
        cap = 1920 if item.get("fullHDURL") else _PIXABAY_MAX_SIDE
        served_w, served_h = scaled_to_fit(item.get("imageWidth"), item.get("imageHeight"), cap)
        entries.append({
            "source": "pixabay",
            "title": item.get("tags") or None,
            "source_page_url": item.get("pageURL"),
            "file_url": file_url,
            "author": item.get("user"),
            "author_url": author_url,
            "width": served_w,
            "height": served_h,
            "original_width": item.get("imageWidth"),
            "original_height": item.get("imageHeight"),
            "bytes": item.get("imageSize"),
            "mime": "image/jpeg",
            "license_code": "pixabay",
            "restrictions": None,
            **STOCK_LICENSES["pixabay"],
        })
    return entries, len(hits), 0


# Solo se acepta la foto si el credito la identifica como obra del NPS: una foto
# cedida por un tercero dentro de su web NO es dominio publico.
_NPS_CREDIT_RE = re.compile(r"\bNPS\b|National Park Service", re.IGNORECASE)


def nps_search(query: str, limit: int, license_filter: str) -> tuple[list[dict[str, Any]], int, int]:
    params = {"q": query, "limit": "20", "api_key": source_key("nps") or ""}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers(), follow_redirects=True) as client:
        response = client.get("https://developer.nps.gov/api/v1/parks", params=params)
        response.raise_for_status()
        parks = response.json().get("data") or []

    entries: list[dict[str, Any]] = []
    seen = 0
    for park in parks:
        for image in park.get("images") or []:
            seen += 1
            file_url = (image.get("url") or "").strip()
            credit = (image.get("credit") or "").strip()
            # Sin credito reconocible no se puede afirmar que sea dominio publico.
            if not file_url or not _NPS_CREDIT_RE.search(credit):
                continue
            # La API del NPS no publica dimensiones. Sin ellas `min_width` las
            # descartaria todas, asi que se miden leyendo solo la cabecera del
            # fichero: unos pocos KB por foto, y solo de las que se van a devolver.
            medidas = probe_dimensions(file_url)
            entries.append({
                "source": "nps",
                "title": image.get("title") or None,
                "source_page_url": park.get("url") or file_url,
                "file_url": file_url,
                "author": credit or "National Park Service",
                "author_url": park.get("url"),
                "width": medidas[0] if medidas else None,
                "height": medidas[1] if medidas else None,
                "dimensions_probed": bool(medidas),
                "original_width": medidas[0] if medidas else None,
                "original_height": medidas[1] if medidas else None,
                "bytes": None,
                "mime": None,
                "license_code": "pd-usgov",
                "restrictions": None,
                "nps_caption": image.get("caption") or None,
                "nps_alt_text": image.get("altText") or None,
                **STOCK_LICENSES["nps"],
            })
            if len(entries) >= limit:
                return entries, seen, 0
    return entries, seen, 0


# Commons se despacha aparte porque su firma no lleva el filtro de licencia:
# alli se lee archivo por archivo.
ADAPTERS = {
    "openverse": openverse_search,
    "unsplash": unsplash_search,
    "pexels": pexels_search,
    "pixabay": pixabay_search,
    "nps": nps_search,
}
