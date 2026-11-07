"""The plant directory keeps the place and drops the street."""

import pandas as pd

from rxradar.transform.plants import parse_place


def test_us_address_keeps_city_and_state_not_street():
    place = parse_place(
        "65 Pitts Station Rd, Marion, North Carolina (NC) 28752, United States (USA)"
    )
    assert place == {"city": "Marion", "region": "North Carolina", "country": "United States"}


def test_foreign_address_with_postal_code():
    place = parse_place("RUE DES NAVOIRS, CHAUNY, HAUTS DE FRANCE F-02300, France (FRA)")
    assert place == {"city": "CHAUNY", "region": "HAUTS DE FRANCE", "country": "France"}


def test_short_address_keeps_the_country_only_as_far_as_it_can():
    assert parse_place("Rugao, Jiangsu, China (CHN)") == {
        "city": "Rugao",
        "region": "Jiangsu",
        "country": "China",
    }
    assert parse_place("Mumbai, India (IND)")["country"] == "India"
    assert parse_place("nonsense")["country"] == ""


def test_korea_has_a_comma_in_its_name():
    place = parse_place("9 Ro, Sojeong-Myeon, Sejong, Sejong 30002, Korea, South (KOR)")
    assert place == {"city": "Sejong", "region": "Sejong", "country": "South Korea"}


def test_only_making_the_drug_or_ingredient_counts():
    from rxradar.transform.plants import plant_family_rows

    sites = pd.DataFrame(
        {
            "plant_duns": ["1", "2", "3", "4"],
            "plant_name": ["Maker", "Lab", "Chem", "Unlisted"],
            "operation": ["manufacture", "ANALYSIS", "API MANUFACTURE", "MANUFACTURE"],
            "ndc": ["0-1", "0-1", "0-1", "9-9"],
        }
    )
    products = pd.DataFrame({"PRODUCTNDC": ["0-1"], "family": ["A"]})
    rows = plant_family_rows(sites, products)
    assert sorted(zip(rows.plant_duns, rows.role, strict=True)) == [
        ("1", "finished"),
        ("3", "ingredient"),
    ]
