from dataclasses import replace
from datetime import date

import pytest
from conftest import CONFIGS
from test_stats import compute, nav_from

from nav_qa_processing.integrations.r2r import rules
from nav_qa_processing.integrations.r2r.config import (
    CruiseConfig,
    load_cruise_config,
)

DAY = date(2026, 7, 9)


@pytest.fixture
def good():
    return compute(nav_from(range(120)))


def ratings(stats):
    return {t.name: t.rating for t in rules.run_r2r_tests(stats).tests}


def test_six_tests_in_r2rs_order(good):
    result = rules.run_r2r_tests(good)
    assert [t.name for t in result.tests] == [
        "PercentCompleteness",
        "LongestGapEpoch",
        "PercentRecordsOutOfSequence",
        "PercentRecordsWithBadQualityIndicator",
        "PercentUnreasonableSpeeds",
        "PercentUnreasonableAccelerations",
    ]
    assert result.rating == "G"
    assert [(i.name, i.value) for i in result.infos] == [
        ("TotalTests", 6),
        ("GTests", 6),
        ("YTests", 0),
        ("RTests", 0),
    ]


@pytest.mark.parametrize(
    ("completeness", "rating"),
    [(95.0, "G"), (94.996, "G"), (94.994, "Y"), (75.0, "Y"), (74.99, "R"), (None, "R")],
)
def test_completeness_is_rated_as_written(good, completeness, rating):
    """94.996 is written 95.00, so it's rated G"""
    assert ratings(replace(good, completeness=completeness))["PercentCompleteness"] == rating


@pytest.mark.parametrize(
    ("gap", "rating"),
    [(900.0, "G"), (900.0004, "G"), (900.001, "Y"), (86_400.0, "Y"), (86_401.0, "R")],
)
def test_longest_gap(good, gap, rating):
    assert ratings(replace(good, longest_gap=gap))["LongestGapEpoch"] == rating


@pytest.mark.parametrize(
    ("percent", "rating"), [(5.0, "G"), (5.01, "Y"), (10.0, "Y"), (10.01, "R")]
)
def test_percentage_tests(good, percent, rating):
    assert (
        ratings(replace(good, out_of_sequence_percent=percent))["PercentRecordsOutOfSequence"]
        == rating
    )


def test_overall_rating(good):
    assert rules.run_r2r_tests(replace(good, longest_gap=1000.0)).rating == "Y"
    result = rules.run_r2r_tests(replace(good, longest_gap=1000.0, completeness=50.0))
    assert result.rating == "R"
    assert [i.value for i in result.infos] == [6, 4, 1, 1]


def test_no_quality_indicator_is_no_bad_one():
    stats = compute(nav_from(range(5), quality=[None] * 5))
    test = rules.run_r2r_tests(stats).tests[3]
    assert (test.result, test.rating, test.text) == (0.0, "G", "0.00")


def test_bounds_and_results_are_written_through_quantities(good):
    test = rules.run_r2r_tests(good).tests[1]
    assert [(b.name, b.text, b.quantity.unit) for b in test.bounds] == [
        ("GMaximumThreshold", "900.000", "s"),
        ("YMaximumThreshold", "86400.000", "s"),
    ]
    assert test.text == "1.000"
    missing = rules.run_r2r_tests(replace(good, longest_gap=None)).tests[1]
    assert (missing.text, missing.rating) == ("NAN", "R")


def test_cruise_config_round_trip(tmp_path):
    config = load_cruise_config(CONFIGS / "SP2613_169851.toml")
    assert config.identifier == "SP2613_169851"
    assert (config.depart_date, config.depart_port.port_id) == (date(2026, 7, 9), "100055")
    assert (config.device.model_name, config.format_alias) == ("BX992", "starc_raw")
    assert config.nav_format == "nav29" and config.extent.westernmost == -117.3773103608
    path = tmp_path / "cruise.toml"
    path.write_text(str(config))
    assert load_cruise_config(path) == config


def test_cruise_config_needs_the_cruise_id_and_dates(tmp_path):
    path = tmp_path / "cruise.toml"
    path.write_text('[cruise]\nid = "X"\ndepart_date = 2026-07-09\n')
    with pytest.raises(ValueError, match="arrive_date"):
        load_cruise_config(path)


def test_identifier_without_a_fileset_id():
    config = CruiseConfig(cruise_id="SP2613", depart_date=DAY, arrive_date=DAY)
    assert config.identifier == "SP2613_gnss"
