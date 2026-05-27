from app.scrapers.camara_diputados import build_enrichment


def test_build_enrichment_maps_dipid_to_bcn_id_and_fields():
    result = build_enrichment(
        {
            "dipid": "1254",
            "district": "8",
            "photo_url": "https://camara.cl/photo.jpg",
            "profile_url": "https://camara.cl/diputados/detalle?prmId=1254",
        }
    )

    assert result is not None
    bcn_id, fields = result
    assert bcn_id == "camara:1254"
    assert fields["district_number"] == 8
    assert fields["photo_url"] == "https://camara.cl/photo.jpg"
    assert fields["profile_url"].endswith("prmId=1254")


def test_build_enrichment_returns_none_without_dipid():
    assert build_enrichment({"district": "8"}) is None


def test_build_enrichment_omits_blank_or_zero_district():
    _, fields = build_enrichment({"dipid": "5", "district": ""})
    assert "district_number" not in fields

    _, fields = build_enrichment({"dipid": "5", "district": "0"})
    assert "district_number" not in fields
