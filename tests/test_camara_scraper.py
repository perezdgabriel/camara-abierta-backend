from app.scrapers.camara_diputados import build_enrichment


def test_build_enrichment_maps_dipid_to_bcn_id_and_photo_fields():
    result = build_enrichment(
        {
            "dipid": "1254",
            "photo_url": "https://camara.cl/photo.jpg",
            "profile_url": "https://camara.cl/diputados/detalle?prmId=1254",
        }
    )

    assert result is not None
    bcn_id, fields = result
    assert bcn_id == "camara:1254"
    assert fields["photo_url"] == "https://camara.cl/photo.jpg"
    assert fields["profile_url"].endswith("prmId=1254")
    # District is BCN-REST-owned now (ADR-0012); scraper must not touch it.
    assert "district_number" not in fields


def test_build_enrichment_returns_none_without_dipid():
    assert build_enrichment({"photo_url": "x"}) is None


def test_build_enrichment_omits_empty_photo_and_profile():
    _, fields = build_enrichment({"dipid": "5"})
    assert fields == {}
