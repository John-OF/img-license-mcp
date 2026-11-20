"""
Motor de licencias
==================
Traduce el codigo legible por maquina que publica cada fuente a una ficha
canonica. Si el codigo no se reconoce devuelve None y la candidata se descarta:
aqui no se adivina nada, solo se normaliza lo que la fuente ya afirma.
"""

from __future__ import annotations

import re
from typing import Any

# Licencias uniformes de las fuentes de stock: toda su libreria es esta.
STOCK_LICENSES: dict[str, dict[str, Any]] = {
    "unsplash": {
        "license": "Unsplash License",
        "license_url": "https://unsplash.com/license",
        "attribution_required": False,
        "allows_commercial": True,
        "allows_modification": True,
        "share_alike": False,
    },
    "pexels": {
        "license": "Pexels License",
        "license_url": "https://www.pexels.com/license/",
        "attribution_required": False,
        "allows_commercial": True,
        "allows_modification": True,
        "share_alike": False,
    },
    "pixabay": {
        "license": "Pixabay Content License",
        "license_url": "https://pixabay.com/service/license-summary/",
        "attribution_required": False,
        "allows_commercial": True,
        "allows_modification": True,
        "share_alike": False,
    },
    "nps": {
        "license": "Public domain",
        "license_url": "https://www.nps.gov/aboutus/disclaimer.htm",
        "attribution_required": False,
        "allows_commercial": True,
        "allows_modification": True,
        "share_alike": False,
    },
}

# Codigos de dominio publico. El valor es el nombre exacto que se mostrara.
_PUBLIC_DOMAIN = {
    "cc0": "CC0 1.0",
    "cc-zero": "CC0 1.0",
    "cc0-1.0": "CC0 1.0",
    "pdm": "Public Domain Mark 1.0",
    "publicdomain": "Public domain",
    "public-domain": "Public domain",
}

_PD_URLS = {
    "CC0 1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "Public Domain Mark 1.0": "https://creativecommons.org/publicdomain/mark/1.0/",
    "Public domain": "https://en.wikipedia.org/wiki/Public_domain",
}

_VERSION_RE = re.compile(r"^\d(?:\.\d)?$")


def parse_license(raw_code: str | None, version: str | None = None) -> dict[str, Any] | None:
    """Codigo de licencia de una fuente -> ficha canonica, o None si no se reconoce.

    Acepta las dos formas que se ven en la practica:
      - Commons  `License`: "cc-by-sa-3.0", "cc0", "pd-usgov", "cc-by-2.0-fr"
      - Openverse `license` + `license_version`: "by-sa" + "2.5", "pdm" + ""
    """
    if not raw_code:
        return None

    code = raw_code.strip().lower().replace("_", "-").replace(" ", "-")
    code = re.sub(r"-+", "-", code).strip("-")
    if not code:
        return None

    # --- Dominio publico por codigo exacto ---
    if code in _PUBLIC_DOMAIN:
        name = _PUBLIC_DOMAIN[code]
        return {
            "license": name,
            "license_url": _PD_URLS[name],
            "attribution_required": False,
            "allows_commercial": True,
            "allows_modification": True,
            "share_alike": False,
        }

    # --- Familia PD-* de Commons: pd, pd-old, pd-us, pd-usgov, pd-art... ---
    if code == "pd" or code.startswith("pd-"):
        return {
            "license": "Public domain",
            "license_url": _PD_URLS["Public domain"],
            "attribution_required": False,
            "allows_commercial": True,
            "allows_modification": True,
            "share_alike": False,
            "pd_reason": code,
        }

    # --- Creative Commons: se separan clausulas y version ---
    tokens = code.split("-")
    if tokens and tokens[0] == "cc":
        tokens = tokens[1:]
    if not tokens or tokens[0] != "by":
        return None  # no es una CC reconocible -> se descarta

    clauses: list[str] = []
    parsed_version = (version or "").strip()
    for token in tokens:
        if token in ("by", "sa", "nc", "nd"):
            if token not in clauses:
                clauses.append(token)
        elif _VERSION_RE.match(token) and not parsed_version:
            parsed_version = token
        # Los sufijos de jurisdiccion (-fr, -de, -es) se ignoran a proposito:
        # no cambian los permisos, solo el texto legal aplicable.

    if "by" not in clauses or not parsed_version:
        return None  # sin version no hay licencia exacta que citar

    if len(parsed_version) == 1:
        parsed_version = f"{parsed_version}.0"

    # El orden canonico lo impone la propia CC, no el que traiga el codigo.
    order = [c for c in ("by", "nc", "nd", "sa") if c in clauses]
    slug = "-".join(order)
    return {
        "license": f"CC {slug.upper()} {parsed_version}",
        "license_url": f"https://creativecommons.org/licenses/{slug}/{parsed_version}/",
        "attribution_required": True,
        "allows_commercial": "nc" not in clauses,
        "allows_modification": "nd" not in clauses,
        "share_alike": "sa" in clauses,
    }


def passes_filter(entry: dict[str, Any], license_filter: str) -> bool:
    """Aplica el filtro editorial sobre una ficha de licencia ya canonica."""
    if license_filter == "any":
        return True
    if license_filter == "no_attribution":
        return not entry["attribution_required"]
    # "commercial": uso comercial Y derecho a modificar (se reescala y recorta).
    return entry["allows_commercial"] and entry["allows_modification"]


def attribution_needed(license_name: str) -> bool | None:
    """True/False si el nombre de licencia lo deja claro, None si no se sabe."""
    name = (license_name or "").strip().lower()
    if not name:
        return None
    if name.startswith("cc by") or name.startswith("cc-by"):
        return True
    if name.startswith("cc0") or "public domain" in name:
        return False
    if name in ("unsplash license", "pexels license", "pixabay content license"):
        return False
    return None
