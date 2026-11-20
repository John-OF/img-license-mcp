"""The licence engine: codes published by each source -> canonical licence."""

from __future__ import annotations

import pytest

from licensing import attribution_needed, parse_license, passes_filter


@pytest.mark.parametrize(
    ("code", "version", "name", "url"),
    [
        # Commons `License` field.
        ("cc-by-sa-3.0", None, "CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/"),
        ("cc-by-4.0", None, "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
        # A jurisdiction suffix does not change the permissions.
        ("cc-by-2.0-fr", None, "CC BY 2.0", "https://creativecommons.org/licenses/by/2.0/"),
        # A bare major version is padded.
        ("cc-by-sa-4", None, "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
        # Openverse sends the clauses and the version separately.
        ("by-sa", "2.5", "CC BY-SA 2.5", "https://creativecommons.org/licenses/by-sa/2.5/"),
        # Clause order is fixed by Creative Commons, not by the incoming code.
        ("cc-by-sa-nc-4.0", None, "CC BY-NC-SA 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
        # Case, underscores and spaces are normalised.
        ("CC_BY_NC_ND 4.0", None, "CC BY-NC-ND 4.0", "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
    ],
)
def test_creative_commons_codes(code, version, name, url):
    entry = parse_license(code, version)
    assert entry["license"] == name
    assert entry["license_url"] == url
    assert entry["attribution_required"] is True


def test_cc_clauses_map_to_permissions():
    by_sa = parse_license("cc-by-sa-4.0")
    assert (by_sa["allows_commercial"], by_sa["allows_modification"], by_sa["share_alike"]) == (True, True, True)

    by_nc_nd = parse_license("cc-by-nc-nd-4.0")
    assert (by_nc_nd["allows_commercial"], by_nc_nd["allows_modification"]) == (False, False)


@pytest.mark.parametrize(
    ("code", "name"),
    [("cc0", "CC0 1.0"), ("cc-zero", "CC0 1.0"), ("pdm", "Public Domain Mark 1.0"), ("pd", "Public domain")],
)
def test_public_domain_codes(code, name):
    entry = parse_license(code)
    assert entry["license"] == name
    assert entry["attribution_required"] is False
    assert entry["allows_commercial"] and entry["allows_modification"]


def test_commons_pd_family_keeps_the_reason():
    entry = parse_license("pd-usgov")
    assert entry["license"] == "Public domain"
    assert entry["pd_reason"] == "pd-usgov"


@pytest.mark.parametrize(
    ("code", "version"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("gfdl", None),  # not a CC code: never guessed
        ("cc-sa-3.0", None),  # CC without BY is not a real licence
        ("cc-by", None),  # no version, so no exact licence to cite
        ("by-nc", ""),
    ],
)
def test_unrecognised_codes_are_dropped(code, version):
    assert parse_license(code, version) is None


def test_license_filter():
    by_sa = parse_license("cc-by-sa-4.0")
    by_nc = parse_license("cc-by-nc-4.0")
    by_nd = parse_license("cc-by-nd-4.0")
    cc0 = parse_license("cc0")

    assert passes_filter(by_sa, "commercial")
    assert not passes_filter(by_nc, "commercial")
    assert not passes_filter(by_nd, "commercial")  # images get resized: modification is required

    assert passes_filter(cc0, "no_attribution")
    assert not passes_filter(by_sa, "no_attribution")

    assert all(passes_filter(e, "any") for e in (by_sa, by_nc, by_nd, cc0))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CC BY-SA 4.0", True),
        ("cc-by-2.0", True),
        ("CC0 1.0", False),
        ("Public domain", False),
        ("Unsplash License", False),
        ("Pixabay Content License", False),
        ("Some custom licence", None),
        ("", None),
    ],
)
def test_attribution_needed(name, expected):
    assert attribution_needed(name) is expected
