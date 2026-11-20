"""
Utilidades compartidas
======================
Identidad HTTP, endpoints y helpers de texto que usan todos los modulos.
"""

from __future__ import annotations

import html
import os
import re
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse

# Wikimedia devuelve 403 a User-Agents genericos (comprobado 01-09, 02-09 y
# 06-09-2026: httpx por defecto y "python-requests/2.32" dan 403, uno
# descriptivo con contacto da 200). Por eso el UA no es opcional. El de serie
# apunta al repo; quien lo despliegue deberia poner el suyo en el .env.
DEFAULT_USER_AGENT = "img-license-mcp/1.0 (+https://github.com/John-OF/img-license-mcp)"


def user_agent() -> str:
    """UA efectivo. Se lee en cada peticion y no al importar: server.py carga el
    .env despues de importar este modulo, y leerlo antes lo ignoraba."""
    return os.environ.get("IMG_LICENSE_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"

HTTP_TIMEOUT = 30.0


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {"User-Agent": user_agent(), "Accept": "*/*"}
    if extra:
        base.update(extra)
    return base


def strip_tags(raw: str | None) -> str | None:
    """Texto plano de un fragmento HTML (el campo Artist de Commons lo es)."""
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def first_href(raw: str | None) -> str | None:
    """Primer enlace de un fragmento HTML, normalizado a absoluto."""
    if not raw:
        return None
    match = re.search(r"href=[\"']([^\"']+)[\"']", raw)
    if not match:
        return None
    href = html.unescape(match.group(1)).strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://commons.wikimedia.org" + href
    return href if href.startswith("http") else None


def commons_title_from_url(url: str) -> tuple[str | None, int | None, str]:
    """URL de Wikimedia -> (titulo File:..., pageid, como se resolvio)."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = unquote(parsed.path)

    if host in ("upload.wikimedia.org", "thumb.wikimedia.org"):
        parts = [p for p in path.split("/") if p]
        if "thumb" in parts:
            # .../thumb/7/7a/Nombre.jpg/1920px-Nombre.jpg: el fichero real es el
            # penultimo segmento, no el nombre del thumb generado.
            name = parts[-2] if len(parts) >= 2 else None
            return (f"File:{name}" if name else None), None, "upload.wikimedia.org thumbnail URL"
        name = parts[-1] if parts else None
        return (f"File:{name}" if name else None), None, "direct upload.wikimedia.org file URL"

    if host.endswith("wikimedia.org") or host.endswith("wikipedia.org"):
        if path.startswith("/wiki/"):
            name = path.removeprefix("/wiki/")
            if name.startswith(("File:", "Archivo:")):
                return name.replace("Archivo:", "File:"), None, "Commons file page"
            return None, None, "Commons page that is not a file"

        query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
        if query.get("curid", "").isdigit():
            return None, int(query["curid"]), "Commons curid"
        title = unquote(query.get("title", ""))
        if title.startswith("File:"):
            return title, None, "index.php?title=File:"

    return None, None, "unsupported host"
