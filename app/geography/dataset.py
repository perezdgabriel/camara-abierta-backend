from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_GEOGRAPHY_DATASET_PATH = Path(__file__).with_name("data") / "chile_current.json"


class CommuneRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)


class ProvinceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    communes: list[CommuneRecord]


class RegionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    capital: str = Field(min_length=1, max_length=100)
    provinces: list[ProvinceRecord]


class DistrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    region_number: int = Field(gt=0)
    commune_numbers: list[int]


class CircumscriptionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    region_numbers: list[int]
    commune_numbers: list[int]


class GeographyDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    effective_from: date
    regions: list[RegionRecord]
    districts: list[DistrictRecord]
    circumscriptions: list[CircumscriptionRecord]

    @model_validator(mode="after")
    def _validate_consistency(self) -> GeographyDataset:
        region_numbers = _ensure_unique(
            [region.number for region in self.regions], "region numbers"
        )
        province_numbers = _ensure_unique(
            [
                province.number
                for region in self.regions
                for province in region.provinces
            ],
            "province numbers",
        )
        commune_records = [
            (commune.number, region.number, province.number)
            for region in self.regions
            for province in region.provinces
            for commune in province.communes
        ]
        commune_numbers = _ensure_unique(
            [number for number, _region, _province in commune_records],
            "commune numbers",
        )
        commune_region_numbers = {
            number: region_number
            for number, region_number, _province in commune_records
        }

        district_numbers = _ensure_unique(
            [district.number for district in self.districts], "district numbers"
        )
        circumscription_numbers = _ensure_unique(
            [circ.number for circ in self.circumscriptions],
            "circumscription numbers",
        )

        district_communes_seen: set[int] = set()
        for district in self.districts:
            if district.region_number not in region_numbers:
                raise ValueError(
                    f"District {district.number} references unknown region {district.region_number}"
                )
            if not district.commune_numbers:
                raise ValueError(f"District {district.number} has no communes")
            local_seen: set[int] = set()
            for commune_number in district.commune_numbers:
                if commune_number not in commune_numbers:
                    raise ValueError(
                        f"District {district.number} references unknown commune {commune_number}"
                    )
                if commune_number in local_seen:
                    raise ValueError(
                        f"District {district.number} repeats commune {commune_number}"
                    )
                local_seen.add(commune_number)
                if commune_region_numbers[commune_number] != district.region_number:
                    raise ValueError(
                        f"District {district.number} assigns commune {commune_number} "
                        f"to region {district.region_number}, but the commune belongs to "
                        f"region {commune_region_numbers[commune_number]}"
                    )
                if commune_number in district_communes_seen:
                    raise ValueError(
                        f"Commune {commune_number} is assigned to multiple districts"
                    )
                district_communes_seen.add(commune_number)

        if district_communes_seen != commune_numbers:
            missing = sorted(commune_numbers - district_communes_seen)
            extra = sorted(district_communes_seen - commune_numbers)
            raise ValueError(
                "District mappings must cover each commune exactly once "
                f"(missing={missing}, extra={extra})"
            )

        circumscription_communes_seen: set[int] = set()
        for circumscription in self.circumscriptions:
            if not circumscription.region_numbers:
                raise ValueError(
                    f"Circumscription {circumscription.number} has no regions"
                )
            if not circumscription.commune_numbers:
                raise ValueError(
                    f"Circumscription {circumscription.number} has no communes"
                )
            local_regions = set(circumscription.region_numbers)
            if len(local_regions) != len(circumscription.region_numbers):
                raise ValueError(
                    f"Circumscription {circumscription.number} repeats region numbers"
                )
            if not local_regions.issubset(region_numbers):
                unknown = sorted(local_regions - region_numbers)
                raise ValueError(
                    f"Circumscription {circumscription.number} references unknown regions {unknown}"
                )
            circumscription_local_seen: set[int] = set()
            for commune_number in circumscription.commune_numbers:
                if commune_number not in commune_numbers:
                    raise ValueError(
                        f"Circumscription {circumscription.number} references unknown commune {commune_number}"
                    )
                if commune_number in circumscription_local_seen:
                    raise ValueError(
                        f"Circumscription {circumscription.number} repeats commune {commune_number}"
                    )
                circumscription_local_seen.add(commune_number)
                if commune_region_numbers[commune_number] not in local_regions:
                    raise ValueError(
                        f"Circumscription {circumscription.number} assigns commune "
                        f"{commune_number} outside its regions"
                    )
                if commune_number in circumscription_communes_seen:
                    raise ValueError(
                        f"Commune {commune_number} is assigned to multiple circumscriptions"
                    )
                circumscription_communes_seen.add(commune_number)

        if circumscription_communes_seen != commune_numbers:
            missing = sorted(commune_numbers - circumscription_communes_seen)
            extra = sorted(circumscription_communes_seen - commune_numbers)
            raise ValueError(
                "Circumscription mappings must cover each commune exactly once "
                f"(missing={missing}, extra={extra})"
            )

        if not province_numbers:
            raise ValueError("Geography dataset has no provinces")
        if not district_numbers:
            raise ValueError("Geography dataset has no districts")
        if not circumscription_numbers:
            raise ValueError("Geography dataset has no circumscriptions")
        return self


def load_geography_dataset(
    path: Path = DEFAULT_GEOGRAPHY_DATASET_PATH,
) -> GeographyDataset:
    data = json.loads(path.read_text(encoding="utf-8"))
    return GeographyDataset.model_validate(data)


def _ensure_unique(values: list[int], label: str) -> set[int]:
    unique = set(values)
    if len(unique) != len(values):
        raise ValueError(f"Geography dataset contains duplicate {label}")
    return unique
