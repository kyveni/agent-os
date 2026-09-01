"""Cron parser surface: parse_cron acceptance/rejection + parse_iso_at."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos.scheduler.parser import CronParseError, parse_cron, parse_iso_at

# --- parse_cron ----------------------------------------------------------


def test_parse_cron_accepts_basic_five_field() -> None:
    assert parse_cron("*/5 * * * *").raw == "*/5 * * * *"


def test_parse_cron_accepts_named_dow_and_month() -> None:
    assert parse_cron("0 9 * * 1-5").raw == "0 9 * * 1-5"
    assert parse_cron("30 8 1 jan *").raw == "30 8 1 jan *"


def test_parse_cron_names_are_case_insensitive() -> None:
    # POSIX: month and day-of-week names are case-insensitive. The parser used
    # to substitute only all-lowercase and all-uppercase spellings, so the
    # common "Mon-Fri" business-hours schedule was rejected outright.
    assert parse_cron("0 9 * * Mon-Fri").day_of_week.values == frozenset({1, 2, 3, 4, 5})
    assert parse_cron("0 9 * * MON-FRI").day_of_week.values == frozenset({1, 2, 3, 4, 5})
    assert parse_cron("0 0 * * Mon,Wed,Fri").day_of_week.values == frozenset({1, 3, 5})
    assert parse_cron("0 9 * Jan *").month.values == frozenset({1})
    assert parse_cron("0 9 * JAN *").month.values == frozenset({1})
    assert parse_cron("0 0 * Jan-Mar *").month.values == frozenset({1, 2, 3})
    assert parse_cron("0 0 * JAN-MAR/2 *").month.values == frozenset({1, 3})


def test_parse_cron_accepts_preset_alias() -> None:
    assert parse_cron("@hourly").raw == "0 * * * *"


def test_parse_cron_rejects_wrong_field_count() -> None:
    with pytest.raises(CronParseError, match="Expected 5 fields"):
        parse_cron("0 9 * *")


def test_parse_cron_rejects_out_of_range_value() -> None:
    with pytest.raises(CronParseError, match="out of range"):
        parse_cron("0 25 * * *")


def test_parse_cron_rejects_garbage() -> None:
    with pytest.raises(CronParseError):
        parse_cron("not-a-cron")


def test_parse_cron_accepts_dow_7_as_sunday() -> None:
    # POSIX permits either 0 or 7 to mean Sunday in the day-of-week field.
    expr = parse_cron("0 0 * * 7")
    assert expr.day_of_week.values == frozenset({0})


def test_parse_cron_dow_ranges_may_end_at_7() -> None:
    # With Sunday spellable as 7, a "WED-SUN" style range is valid and must
    # resolve to the same weekday set as its 0-terminated equivalent.
    expr = parse_cron("0 0 * * WED-7")
    assert expr.day_of_week.values == frozenset({0, 3, 4, 5, 6})


def test_parse_cron_dow_7_dedups_with_0_and_names() -> None:
    assert parse_cron("0 0 * * 0,7").day_of_week.values == frozenset({0})
    assert parse_cron("0 0 * * MON,7").day_of_week.values == frozenset({0, 1})


def test_parse_cron_dow_7_matches_sunday_not_monday() -> None:
    expr = parse_cron("0 0 * * 7")
    sunday = datetime(2026, 8, 30, 0, 0)  # a Sunday
    monday = datetime(2026, 8, 31, 0, 0)  # the next Monday
    assert expr.matches(sunday)
    assert not expr.matches(monday)


def test_parse_cron_rejects_unknown_preset() -> None:
    with pytest.raises(CronParseError, match="Unknown preset"):
        parse_cron("@bogus")


def test_parse_cron_rejects_reversed_range_with_step() -> None:
    # A reversed range in the step branch used to parse into an *empty* field
    # set, so the expression validated, stored, and then matched nothing —
    # _next_run would burn through its whole scan window and raise
    # "No valid next run found" at job creation. Reject it up front like the
    # plain-range branch already does.
    with pytest.raises(CronParseError, match="Range start > end"):
        parse_cron("5-3/2 * * * *")
    with pytest.raises(CronParseError, match="Range start > end"):
        parse_cron("0 0 * * FRI-TUE/2")
    with pytest.raises(CronParseError, match="Range start > end"):
        parse_cron("0 0 * dec-feb/2 *")


# --- parse_iso_at --------------------------------------------------------


def test_parse_iso_at_accepts_offset() -> None:
    dt = parse_iso_at("2026-05-15T09:00:00+08:00")
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.hour == 9


def test_parse_iso_at_accepts_z_suffix() -> None:
    dt = parse_iso_at("2026-05-15T01:00:00Z")
    assert dt.tzinfo is not None
    assert dt.astimezone(UTC) == datetime(2026, 5, 15, 1, 0, tzinfo=UTC)


def test_parse_iso_at_rejects_naive_datetime() -> None:
    with pytest.raises(CronParseError, match="timezone"):
        parse_iso_at("2026-05-15T09:00:00")


def test_parse_iso_at_rejects_garbage() -> None:
    with pytest.raises(CronParseError, match="Invalid ISO-8601"):
        parse_iso_at("not-a-timestamp")


def test_parse_iso_at_rejects_empty() -> None:
    with pytest.raises(CronParseError, match="must not be empty"):
        parse_iso_at("   ")


def test_parse_iso_at_rejects_non_string() -> None:
    with pytest.raises(CronParseError, match="Expected ISO-8601 string"):
        parse_iso_at(12345)  # type: ignore[arg-type]


# --- POSIX day-of-month / day-of-week OR semantics (#660) ---------------
#
# Per POSIX: when both day-of-month and day-of-week are restricted (neither
# is a literal `*`), the job fires when EITHER field matches, not both.
# When only one is restricted, standard AND applies.


class TestCronDomDowOrSemantics:
    """Regression tests for POSIX OR semantics (issue #660)."""

    # --- OR case: "0 0 1,15 * 5" (1st, 15th, or any Friday) ---

    def test_dom_dow_or_matches_dom_only(self) -> None:
        """Dec 1 (Tuesday, not Friday) — fires on 1st."""
        expr = parse_cron("0 0 1,15 * 5")
        dt = datetime(2026, 12, 1, 0, 0)  # Tuesday
        assert expr.matches(dt)

    def test_dom_dow_or_matches_dow_only(self) -> None:
        """Dec 4 (Friday, not 1st or 15th) — fires on Friday."""
        expr = parse_cron("0 0 1,15 * 5")
        dt = datetime(2026, 12, 4, 0, 0)  # Friday
        assert expr.matches(dt)

    def test_dom_dow_or_matches_both(self) -> None:
        """May 15 (Friday) — fires on both 15th and Friday."""
        expr = parse_cron("0 0 1,15 * 5")
        dt = datetime(2026, 5, 15, 0, 0)  # Friday the 15th
        assert expr.matches(dt)

    def test_dom_dow_or_no_match(self) -> None:
        """Dec 2 (Wednesday, not 1st/15th/Friday) — should not fire."""
        expr = parse_cron("0 0 1,15 * 5")
        dt = datetime(2026, 12, 2, 0, 0)  # Wednesday
        assert not expr.matches(dt)

    def test_dom_dow_or_cross_month_boundary(self) -> None:
        """Mar 1 (Sunday) — fires on 1st regardless of month."""
        expr = parse_cron("0 0 1,15 * 5")
        dt = datetime(2026, 3, 1, 0, 0)  # Sunday
        assert expr.matches(dt)

    # --- Single-value restriction OR ---

    def test_single_value_dom_with_dow_or_matches_dom(self) -> None:
        """"0 0 15 * 5" — Dec 15 (Tuesday) fires on 15th."""
        expr = parse_cron("0 0 15 * 5")
        dt = datetime(2026, 12, 15, 0, 0)  # Tuesday
        assert expr.matches(dt)

    def test_single_value_dom_with_dow_or_matches_dow(self) -> None:
        """"0 0 15 * 5" — Dec 11 (Friday, not 15th) fires on Friday."""
        expr = parse_cron("0 0 15 * 5")
        dt = datetime(2026, 12, 11, 0, 0)  # Friday
        assert expr.matches(dt)

    def test_single_value_dom_with_dow_or_no_match(self) -> None:
        """"0 0 15 * 5" — Dec 10 (Thursday) neither 15th nor Friday."""
        expr = parse_cron("0 0 15 * 5")
        dt = datetime(2026, 12, 10, 0, 0)  # Thursday
        assert not expr.matches(dt)

    # --- AND fallback when DOM is wildcard ---

    def test_dom_wildcard_and_fallback_matches(self) -> None:
        """"0 0 * * 5" — only Fridays, DOW restricted, DOM wildcard → AND."""
        expr = parse_cron("0 0 * * 5")
        dt = datetime(2026, 12, 4, 0, 0)  # Friday
        assert expr.matches(dt)

    def test_dom_wildcard_and_fallback_no_match(self) -> None:
        """"0 0 * * 5" — Monday should not match."""
        expr = parse_cron("0 0 * * 5")
        dt = datetime(2026, 12, 7, 0, 0)  # Monday
        assert not expr.matches(dt)

    # --- AND fallback when DOW is wildcard ---

    def test_dow_wildcard_and_fallback_matches(self) -> None:
        """"0 0 15 * *" — only 15th, DOM restricted, DOW wildcard → AND."""
        expr = parse_cron("0 0 15 * *")
        dt = datetime(2026, 12, 15, 0, 0)
        assert expr.matches(dt)

    def test_dow_wildcard_and_fallback_no_match(self) -> None:
        """"0 0 15 * *" — 16th should not match."""
        expr = parse_cron("0 0 15 * *")
        dt = datetime(2026, 12, 16, 0, 0)
        assert not expr.matches(dt)

    # --- AND fallback when both are wildcard ---

    def test_both_wildcard_matches_all(self) -> None:
        """"0 0 * * *" — every day."""
        expr = parse_cron("0 0 * * *")
        assert expr.matches(datetime(2026, 12, 1, 0, 0))
        assert expr.matches(datetime(2026, 12, 31, 0, 0))
        assert expr.matches(datetime(2026, 1, 1, 0, 0))

    # --- POSIX: `*` vs explicit full-range (e.g. `1-31`) ---
    # POSIX ties the OR-vs-AND rule to a literal `*` in the expression,
    # not to whether the resolved set spans every possible value.
    # `0 0 * * 5` uses AND (only Fridays), but `0 0 1-31 * 5` fires OR
    # because `1-31` is "restricted" even though it covers every day.

    def test_explicit_full_range_is_restricted(self) -> None:
        """"0 0 1-31 * 5" — DOM `1-31` is restricted, so OR fires every day."""
        expr = parse_cron("0 0 1-31 * 5")
        # Every day of month matches 1-31, so ANY day fires.
        tue = datetime(2026, 12, 1, 0, 0)  # Tuesday
        thu = datetime(2026, 12, 3, 0, 0)  # Thursday
        sat = datetime(2026, 12, 5, 0, 0)  # Saturday
        assert expr.matches(tue)
        assert expr.matches(thu)
        assert expr.matches(sat)

    def test_both_full_range_is_restricted(self) -> None:
        """"0 0 1-31 * 0-6" — both are explicit full range, still restricted → OR."""
        expr = parse_cron("0 0 1-31 * 0-6")
        assert expr.matches(datetime(2026, 12, 1, 0, 0))
        assert expr.matches(datetime(2026, 12, 31, 0, 0))

    def test_wildcard_star_triggers_and(self) -> None:
        """"0 0 * * 0-6" — DOM `*` wildcard, DOW `0-6` restricted → AND.
        DOW `0-6` covers every day, so this still matches any day — the
        important thing is the AND code path is taken, not the OR path."""
        expr = parse_cron("0 0 * * 0-6")
        # All days match since DOW covers 0-6 (Sunday through Saturday)
        assert expr.matches(datetime(2026, 12, 6, 0, 0))  # Sunday
        assert expr.matches(datetime(2026, 12, 7, 0, 0))  # Monday
        assert expr.matches(datetime(2026, 12, 5, 0, 0))  # Saturday

    # --- Presets that should not regress ---

    def test_monthly_preset_still_and(self) -> None:
        """"@monthly" = "0 0 1 * *" — DOM restricted, DOW wildcard → AND."""
        expr = parse_cron("@monthly")
        assert expr.matches(datetime(2026, 12, 1, 0, 0))
        assert not expr.matches(datetime(2026, 12, 2, 0, 0))

    def test_weekly_preset_still_and(self) -> None:
        """"@weekly" = "0 0 * * 0" — DOW restricted, DOM wildcard → AND."""
        expr = parse_cron("@weekly")
        assert expr.matches(datetime(2026, 11, 29, 0, 0))  # Sunday
        assert not expr.matches(datetime(2026, 11, 30, 0, 0))  # Monday

    # --- Name-based restrictions ---

    def test_name_or_matches_dom_only(self) -> None:
        """"0 0 1-15 * Mon-Fri" — Saturday within 1-15 → fires on DOM."""
        expr = parse_cron("0 0 1-15 * Mon-Fri")
        # Dec 5 2026 = Saturday (cron_dow=6) — outside Mon-Fri, inside 1-15
        assert expr.matches(datetime(2026, 12, 5, 0, 0))

    def test_name_or_matches_dow_only(self) -> None:
        """"0 0 1-15 * Mon-Fri" — Monday after 15th → fires on DOW."""
        expr = parse_cron("0 0 1-15 * Mon-Fri")
        # Dec 21 2026 = Monday (cron_dow=1) — inside Mon-Fri, outside 1-15
        assert expr.matches(datetime(2026, 12, 21, 0, 0))

    def test_name_or_matches_both(self) -> None:
        """"0 0 1-15 * Mon-Fri" — Monday within 1-15 → fires on both."""
        expr = parse_cron("0 0 1-15 * Mon-Fri")
        # Dec 7 2026 = Monday (cron_dow=1) — inside 1-15 AND Mon-Fri
        assert expr.matches(datetime(2026, 12, 7, 0, 0))

    def test_name_or_no_match(self) -> None:
        """"0 0 1-15 * Mon-Fri" — Sunday outside 1-15 → no match."""
        expr = parse_cron("0 0 1-15 * Mon-Fri")
        # Dec 20 2026 = Sunday (cron_dow=0) — outside both
        assert not expr.matches(datetime(2026, 12, 20, 0, 0))
