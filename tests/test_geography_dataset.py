import pytest
from pydantic import ValidationError

from app.geography.dataset import (
    DEFAULT_GEOGRAPHY_DATASET_PATH,
    GeographyDataset,
    load_geography_dataset,
)


def test_load_geography_dataset_validates_current_baseline():
    dataset = load_geography_dataset()

    assert DEFAULT_GEOGRAPHY_DATASET_PATH.exists()
    assert dataset.version == "2026-05-27"
    assert len(dataset.regions) == 16
    assert sum(len(region.provinces) for region in dataset.regions) == 55
    assert (
        sum(
            len(province.communes)
            for region in dataset.regions
            for province in region.provinces
        )
        == 346
    )
    assert {district.number for district in dataset.districts} == set(range(1, 29))
    assert len(dataset.circumscriptions) == 16

    district_8 = next(
        district for district in dataset.districts if district.number == 8
    )
    assert district_8.region_number == 13

    nuble = next(region for region in dataset.regions if region.number == 16)
    assert [province.name for province in nuble.provinces] == [
        "Diguillín",
        "Itata",
        "Punilla",
    ]


def test_geography_dataset_rejects_missing_commune_coverage():
    payload = load_geography_dataset().model_dump(mode="json")
    payload["districts"][0]["commune_numbers"] = payload["districts"][0][
        "commune_numbers"
    ][:-1]

    with pytest.raises(ValidationError):
        GeographyDataset.model_validate(payload)
