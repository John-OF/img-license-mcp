"""
Cliente de WordPress
====================
Comportamientos de WordPress verificados contra un sitio real:
  - MEDIA_TRASH viene desactivado de serie: un borrado de medios por REST es
    permanente. Por eso este servidor no expone ninguna herramienta de borrado.
  - Al cambiar featured_media por REST, el og:image de Yoast SEO no se refresca
    a la primera (comprobado 06-09-2026): el JSON-LD ya daba la imagen nueva y
    el og:image seguia con la vieja. No es cache, es su tabla de indexables. Se
    arregla repitiendo el mismo POST, y en un sitio sin Yoast no hace dano.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

WP_MIMES = {
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def wp_config() -> tuple[str, str, str] | None:
    """(base_url, usuario, application password) o None si falta alguno."""
    base = (os.environ.get("WP_BASE_URL") or "").strip().rstrip("/")
    user = (os.environ.get("WP_USER") or "").strip()
    password = (os.environ.get("WP_APP_PASSWORD") or "").strip()
    if not (base and user and password):
        return None
    return base, user, password


def build_description(
    path: Path,
    source_url: str,
    author: str,
    license_name: str,
    original_width: int,
    original_height: int,
    notes: str = "",
) -> str:
    """Bloque de procedencia. Se construye siempre a partir de campos obligatorios
    para que no pueda quedarse a medias."""
    # Fecha de descarga = cuando `prepare_image` escribio el fichero.
    downloaded_at = datetime.fromtimestamp(path.stat().st_mtime, UTC).date().isoformat()
    provenance = (
        f"Source: {source_url}<br />"
        f"Author: {author}<br />"
        f"Licence: {license_name}<br />"
        f"Original resolution: {original_width}x{original_height}<br />"
        f"Downloaded: {downloaded_at}"
    )
    return f"{notes.strip()}<br /><br />{provenance}" if notes.strip() else provenance
