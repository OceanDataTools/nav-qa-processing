"""R2R's navigation tests: the six tests of R2R's QA 2.0 nav reports, rated G/Y/R from the core
QA statistics (`stats.NavStats`).

Test, bound and info names are R2R's (NavManager's `xmltools2.inc.php`), and so are the G and Y
bounds, stated once here and written to the XML from the same values:

| Test | G | Y | otherwise R |
|---|---|---|---|
| PercentCompleteness | ≥95 % | ≥75 % | |
| LongestGapEpoch | ≤900 s | ≤86,400 s | |
| PercentRecordsOutOfSequence, …WithBadQualityIndicator, PercentUnreasonable{Speeds,Accelerations} | ≤5 % | ≤10 % | |

The overall rating is G if every test is G, R if any test is R, and Y otherwise; the infos count
the tests by rating. Descriptions are ours (`docs/architecture.md`, "Standardized quantities"):
they say what the statistics measure here, which for accelerations isn't what navqa measured.

How the ratings are reached (docs/navmanager-differences.md, A7):
- A result is rated as written, rounded to its quantity's decimals (`quantities.PERCENTAGE`,
  `DURATION`), so the value in the report and its rating always agree. NavManager also rates
  the values it writes, but writes gaps at the receiver's time precision rather than to the
  millisecond, so a gap just over a bound (900.004 s, written 900.00 for a receiver timing to
  the centisecond) could rate differently.
- When no record reports a GGA quality indicator, no record has a bad one: 0 %, G. That is what
  navqa reports too (it drops the count when every "bad" record is a missing indicator), but
  when some records do report one, navqa counts the missing ones as bad (A4); here they're left
  out.
- A statistic that can't be computed (no usable fixes, or one fix for speeds) has no result
  (`xsi:nil`) and is rated R. navqa would divide by zero.
"""

from dataclasses import dataclass

from nav_qa_processing import quantities as q
from nav_qa_processing.readable import Readable, block, plural
from nav_qa_processing.stats import NavStats

GREEN, YELLOW, RED = "G", "Y", "R"

OVERALL_DESCRIPTION = (
    "GREEN (G) if all tests GREEN, RED (R) if at least one test RED, else YELLOW (Y)"
)

# R2R's bounds: (G, Y)
COMPLETENESS_BOUNDS = (95.0, 75.0)  # [percent], minimum
LONGEST_GAP_BOUNDS = (900.0, 86_400.0)  # [s], maximum
PERCENT_BOUNDS = (5.0, 10.0)  # [percent], maximum


@dataclass
class Bound:
    name: str
    quantity: q.Quantity
    value: float

    @property
    def text(self) -> str:
        return self.quantity.format(self.value)


@dataclass
class TestResult(Readable):
    __test__ = False  # not a pytest test class

    name: str
    description: str
    rating: str
    quantity: q.Quantity
    result: float | None  # as rated: rounded to the quantity's decimals; None if not computed
    bounds: list[Bound]
    detail: str = ""

    @property
    def text(self) -> str:
        return self.quantity.format(self.result)

    def __str__(self) -> str:
        detail = f" ({self.detail})" if self.detail else ""
        return f"{self.rating}  {self.name}: {self.text} {self.quantity.unit}{detail}"


@dataclass
class Info:
    name: str
    quantity: q.Quantity
    value: int

    @property
    def text(self) -> str:
        return self.quantity.format(self.value)


@dataclass
class R2RResult(Readable):
    rating: str
    tests: list[TestResult]
    infos: list[Info]

    @property
    def description(self) -> str:
        return OVERALL_DESCRIPTION

    def __str__(self) -> str:
        return block(f"R2R rating {self.rating}", [str(test) for test in self.tests])


def _rounded(value: float | None, quantity: q.Quantity) -> float | None:
    return None if value is None else round(value, quantity.decimals)


def at_least(name, description, value, bounds, detail="") -> TestResult:
    """Rated G at or above bounds[0], Y at or above bounds[1], else R"""
    value = _rounded(value, q.PERCENTAGE)
    green, yellow = bounds
    if value is None or value < yellow:
        rating = RED
    else:
        rating = GREEN if value >= green else YELLOW
    return TestResult(
        name,
        description,
        rating,
        q.PERCENTAGE,
        value,
        [
            Bound("GMinimumThreshold", q.PERCENTAGE, green),
            Bound("YMinimumThreshold", q.PERCENTAGE, yellow),
        ],
        detail,
    )


def at_most(name, description, value, bounds, quantity=q.PERCENTAGE, detail="") -> TestResult:
    """Rated G at or below bounds[0], Y at or below bounds[1], else R"""
    value = _rounded(value, quantity)
    green, yellow = bounds
    if value is None or value > yellow:
        rating = RED
    else:
        rating = GREEN if value <= green else YELLOW
    return TestResult(
        name,
        description,
        rating,
        quantity,
        value,
        [
            Bound("GMaximumThreshold", quantity, green),
            Bound("YMaximumThreshold", quantity, yellow),
        ],
        detail,
    )


def overall_rating(tests: list[TestResult]) -> str:
    ratings = {test.rating for test in tests}
    if ratings == {GREEN}:
        return GREEN
    return RED if RED in ratings else YELLOW


def run_r2r_tests(stats: NavStats) -> R2RResult:
    s = stats
    bad_quality = s.bad_quality_percent
    if bad_quality is None and s.records:
        bad_quality = 0.0  # no record reports an indicator, so none reports a bad one
    tests = [
        at_least(
            "PercentCompleteness",
            "Percentage of sampling intervals from the first fix to the last that hold a fix.",
            s.completeness,
            COMPLETENESS_BOUNDS,
            f"{s.occupied_epochs} of {s.possible_epochs} intervals of {s.modal_interval} s"
            if s.modal_interval
            else "",
        ),
        at_most(
            "LongestGapEpoch",
            "Longest time between successive fixes.",
            s.longest_gap,
            LONGEST_GAP_BOUNDS,
            q.DURATION,
            f"{plural(s.gaps, 'gap')} of the gap threshold or longer",
        ),
        at_most(
            "PercentRecordsOutOfSequence",
            "Percentage of fixes timed before the fix preceding them.",
            s.out_of_sequence_percent,
            PERCENT_BOUNDS,
            detail=f"{s.out_of_sequence} of {s.records}",
        ),
        at_most(
            "PercentRecordsWithBadQualityIndicator",
            "Percentage of fixes whose GGA quality indicator is not a valid fix.",
            bad_quality,
            PERCENT_BOUNDS,
            detail=f"{s.bad_quality} of {s.quality_reported} reporting one",
        ),
        at_most(
            "PercentUnreasonableSpeeds",
            "Percentage of speeds between successive fixes above the speed threshold.",
            s.unreasonable_speeds_percent,
            PERCENT_BOUNDS,
            detail=f"{s.unreasonable_speeds} of {s.speeds}",
        ),
        at_most(
            "PercentUnreasonableAccelerations",
            "Percentage of fixes whose acceleration is above the acceleration threshold.",
            s.unreasonable_accelerations_percent,
            PERCENT_BOUNDS,
            detail=f"{s.unreasonable_accelerations} of {s.accelerations}",
        ),
    ]
    ratings = [test.rating for test in tests]
    infos = [
        Info("TotalTests", q.COUNT, len(tests)),
        Info("GTests", q.COUNT, ratings.count(GREEN)),
        Info("YTests", q.COUNT, ratings.count(YELLOW)),
        Info("RTests", q.COUNT, ratings.count(RED)),
    ]
    return R2RResult(overall_rating(tests), tests, infos)
