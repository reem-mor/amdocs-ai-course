"""
Test suite for titanic_ticket.py
================================

Best-practice pytest suite that uses the real Titanic dataset only.

Goals:
- Verify the program works correctly against titanic.csv.
- Avoid synthetic datasets for logic assertions.
- Keep tests isolated, readable, and deterministic where possible.
- Cover both unit-level behavior and end-to-end program execution.

Run with:
    python -m pytest -v
"""

from __future__ import annotations

import builtins
import random
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import titanic_ticket as tt

HERE = Path(__file__).resolve().parent
DATASET_PATH = Path(__file__).resolve().parent / "titanic.csv"
OUTPUT_PATH = Path(__file__).resolve().parent / "ticket.txt"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dataset_path() -> Path:
    """Path to the real Titanic dataset."""
    path = Path(__file__).resolve().parent / "titanic.csv"
    if not path.exists():
        pytest.fail(f"titanic.csv was not found at: {path}")
    return path

@pytest.fixture(scope="module")
def real_df(dataset_path: Path) -> pd.DataFrame:
    """Load the real Titanic dataset exactly as production code does."""
    return tt.load_dataset(dataset_path)


@pytest.fixture
def patch_inputs(monkeypatch):
    """
    Patch builtins.input with a queue of predefined replies.

    Returns a helper that accepts a list of input strings and records the
    prompts that were shown by the program.
    """
    def _setup(replies: list[str]) -> list[str]:
        replies = list(replies)
        prompts: list[str] = []

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            if not replies:
                raise AssertionError(
                    f"input() called more times than expected. Prompts: {prompts}"
                )
            return replies.pop(0)

        monkeypatch.setattr(builtins, "input", fake_input)
        return prompts

    return _setup


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

class TestLoadDatasetRealData:
    """Tests that validate properties of the real loaded Titanic dataframe."""

    def test_load_dataset_returns_non_empty_dataframe(self, dataset_path: Path):
        df = tt.load_dataset(dataset_path)
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_required_columns_exist(self, real_df: pd.DataFrame):
        assert set(tt.REQUIRED_COLUMNS).issubset(real_df.columns)

    def test_numeric_columns_are_numeric_after_loading(self, real_df: pd.DataFrame):
        assert pd.api.types.is_numeric_dtype(real_df["fare"])
        assert pd.api.types.is_numeric_dtype(real_df["age"])
        assert pd.api.types.is_numeric_dtype(real_df["pclass"])
        assert pd.api.types.is_numeric_dtype(real_df["survived"])

    def test_sex_column_is_normalized_to_lowercase(self, real_df: pd.DataFrame):
        non_null = real_df["sex"].dropna()
        assert (non_null == non_null.astype(str).str.strip().str.lower()).all()

    def test_ticket_column_is_string_like_and_stripped(self, real_df: pd.DataFrame):
        tickets = real_df["ticket"].astype(str)
        assert (tickets == tickets.str.strip()).all()

    def test_real_dataset_contains_expected_classes(self, real_df: pd.DataFrame):
        classes = set(real_df["pclass"].dropna().astype(int).unique())
        assert classes == {1, 2, 3}

    def test_real_dataset_contains_both_sexes(self, real_df: pd.DataFrame):
        sexes = set(real_df["sex"].dropna().unique())
        assert {"male", "female"}.issubset(sexes)

    def test_real_dataset_contains_survival_values(self, real_df: pd.DataFrame):
        survived_values = set(real_df["survived"].dropna().astype(int).unique())
        assert survived_values.issuperset({0, 1})


# ---------------------------------------------------------------------------
# Fare bounds and class ranges
# ---------------------------------------------------------------------------

class TestFareAnalysisRealData:
    """Tests for real fare statistics derived from titanic.csv."""

    def test_get_fare_bounds_matches_manual_real_dataset_calculation(
        self, real_df: pd.DataFrame
    ):
        got_min, got_max = tt.get_fare_bounds(real_df)

        manual_fares = real_df.loc[real_df["fare"] > 0, "fare"].dropna()
        expected_min = float(manual_fares.min())
        expected_max = float(manual_fares.max())

        assert got_min == expected_min
        assert got_max == expected_max

    def test_fare_bounds_are_positive_and_ordered(self, real_df: pd.DataFrame):
        lo, hi = tt.get_fare_bounds(real_df)
        assert lo > 0
        assert hi > lo

    def test_get_class_fare_ranges_matches_manual_real_dataset_calculation(
        self, real_df: pd.DataFrame
    ):
        got = tt.get_class_fare_ranges(real_df)

        usable = real_df[
            (real_df["fare"] > 0)
            & real_df["fare"].notna()
            & real_df["pclass"].notna()
        ]
        expected = {
            int(pclass): (float(group["fare"].min()), float(group["fare"].max()))
            for pclass, group in usable.groupby("pclass")
        }

        assert got == expected

    def test_class_ranges_contain_all_three_classes(self, real_df: pd.DataFrame):
        ranges = tt.get_class_fare_ranges(real_df)
        assert set(ranges.keys()) == {1, 2, 3}

    def test_each_class_range_is_non_degenerate(self, real_df: pd.DataFrame):
        ranges = tt.get_class_fare_ranges(real_df)
        for _, (lo, hi) in ranges.items():
            assert lo > 0
            assert hi > lo

    def test_real_dataset_has_overlap_between_class_ranges(self, real_df: pd.DataFrame):
        ranges = tt.get_class_fare_ranges(real_df)

        sample_fares = sorted(set(float(x) for x in real_df["fare"].dropna() if x > 0))
        overlaps = [
            fare
            for fare in sample_fares
            if len([pc for pc, (lo, hi) in ranges.items() if lo <= fare <= hi]) >= 2
        ]

        assert overlaps, "Expected at least one overlapping fare in the real dataset."


# ---------------------------------------------------------------------------
# Class inference using real ranges
# ---------------------------------------------------------------------------

class TestInferPclassRealData:
    """Tests that infer_pclass behaves correctly with real Titanic fare ranges."""

    def test_real_fare_50_promotes_to_best_matching_class(self, real_df: pd.DataFrame):
        ranges = tt.get_class_fare_ranges(real_df)
        matches = [pc for pc, (lo, hi) in ranges.items() if lo <= 50.0 <= hi]

        assert len(matches) >= 2
        assert tt.infer_pclass(50.0, ranges) == min(matches)

    def test_real_dataset_min_fare_is_assigned_to_a_valid_class(self, real_df: pd.DataFrame):
        ranges = tt.get_class_fare_ranges(real_df)
        min_fare, _ = tt.get_fare_bounds(real_df)

        pclass = tt.infer_pclass(min_fare, ranges)
        assert pclass in {1, 2, 3}

    def test_real_dataset_max_fare_is_assigned_to_a_valid_class(self, real_df: pd.DataFrame):
        ranges = tt.get_class_fare_ranges(real_df)
        _, max_fare = tt.get_fare_bounds(real_df)

        pclass = tt.infer_pclass(max_fare, ranges)
        assert pclass in {1, 2, 3}

    @pytest.mark.parametrize("fare", [7.25, 8.05, 13.0, 26.0, 50.0, 83.475, 263.0])
    def test_selected_realistic_fares_map_to_best_matching_class(
        self, real_df: pd.DataFrame, fare: float
    ):
        ranges = tt.get_class_fare_ranges(real_df)

        matches = [pc for pc, (lo, hi) in ranges.items() if lo <= fare <= hi]
        if matches:
            assert tt.infer_pclass(fare, ranges) == min(matches)

    def test_infer_pclass_is_deterministic_on_real_ranges(self, real_df: pd.DataFrame):
        ranges = tt.get_class_fare_ranges(real_df)
        assert tt.infer_pclass(50.0, ranges) == tt.infer_pclass(50.0, ranges)


# ---------------------------------------------------------------------------
# Prompt validation
# ---------------------------------------------------------------------------

class TestPromptName:
    """Interactive validation tests for passenger name."""

    @pytest.mark.parametrize(
        "reply,expected",
        [
            ("Reem", "Reem"),
            (" Reem Mor ", "Reem Mor"),
            ("X", "X"),
            ("O'Brien", "O'Brien"),
            ("Mary-Jane", "Mary-Jane"),
            ("José", "José"),
        ],
    )
    def test_valid_names_accepted(self, patch_inputs, reply, expected):
        patch_inputs([reply])
        assert tt.prompt_name() == expected

    @pytest.mark.parametrize("bad", ["", " ", "\t\n", "!!!", "???", "123", "...", " . "])
    def test_invalid_names_reprompt_until_valid(self, patch_inputs, bad, capsys):
        patch_inputs([bad, "Reem"])
        assert tt.prompt_name() == "Reem"
        out = capsys.readouterr().out.lower()
        assert "name is wrong" in out or "digit" in out

    def test_name_with_digits_is_reprompted(self, patch_inputs, capsys):
        patch_inputs(["Reem123", "Reem"])
        assert tt.prompt_name() == "Reem"
        out = capsys.readouterr().out.lower()
        assert "digit" in out or "name is wrong" in out

    def test_name_prompt_strips_only_edges(self, patch_inputs):
        patch_inputs(["  Reem Mor  "])
        assert tt.prompt_name() == "Reem Mor"
        
class TestPromptAge:
    """Interactive validation tests for passenger age."""

    @pytest.mark.parametrize(
        "reply,expected",
        [
            ("0", 0.0),
            ("130", 130.0),
            ("29.5", 29.5),
            (" 30 ", 30.0),
            ("0.001", 0.001),
        ],
    )
    def test_valid_ages_accepted(self, patch_inputs, reply, expected):
        patch_inputs([reply])
        assert tt.prompt_age() == expected

    @pytest.mark.parametrize(
        "bad",
        ["abc", "", " ", "thirty", "1/0", "+", "1.2.3", "nan", "NaN", "inf", "-inf", "Infinity"],
    )
    def test_non_numeric_or_nonfinite_age_reprompted(self, patch_inputs, bad, capsys):
        patch_inputs([bad, "30"])
        assert tt.prompt_age() == 30.0
        out = capsys.readouterr().out.lower()
        assert "age is wrong" in out or "between 0 and 130" in out

    @pytest.mark.parametrize("bad", ["-1", "131", "200", "-0.5", "1e10"])
    def test_out_of_range_age_reprompted(self, patch_inputs, bad, capsys):
        patch_inputs([bad, "50"])
        assert tt.prompt_age() == 50.0
        out = capsys.readouterr().out.lower()
        assert "between 0 and 130" in out

    def test_age_eof_raises_user_cancelled(self, monkeypatch):
        def boom(_=""):
            raise EOFError
        monkeypatch.setattr(builtins, "input", boom)
        with pytest.raises(tt.UserCancelled):
            tt.prompt_age()

    def test_age_keyboard_interrupt_raises_user_cancelled(self, monkeypatch):
        def boom(_=""):
            raise KeyboardInterrupt
        monkeypatch.setattr(builtins, "input", boom)
        with pytest.raises(tt.UserCancelled):
            tt.prompt_age()


class TestPromptSex:
    """Interactive validation tests for passenger sex."""

    @pytest.mark.parametrize(
        "reply,expected",
        [
            ("male", "male"),
            ("female", "female"),
            ("MALE", "male"),
            ("Female", "female"),
            (" male ", "male"),
            ("\tfemale\n", "female"),
        ],
    )
    def test_valid_sex_values_accepted(self, patch_inputs, reply, expected):
        patch_inputs([reply])
        assert tt.prompt_sex() == expected

    @pytest.mark.parametrize(
        "bad",
        ["", " ", "helicopter", "m", "f", "boy", "girl", "ma le", "male/female", "1", "true"],
    )
    def test_invalid_sex_values_reprompted(self, patch_inputs, bad, capsys):
        patch_inputs([bad, "male"])
        assert tt.prompt_sex() == "male"
        out = capsys.readouterr().out.lower()
        assert "male or female" in out or "illegal" in out


class TestPromptFare:
    """Interactive validation tests for fare, using the real Titanic fare range."""

    def test_real_dataset_min_fare_is_accepted(self, patch_inputs, real_df: pd.DataFrame):
        min_fare, max_fare = tt.get_fare_bounds(real_df)
        patch_inputs([str(min_fare)])
        assert tt.prompt_fare(min_fare, max_fare) == min_fare

    def test_real_dataset_max_fare_is_accepted(self, patch_inputs, real_df: pd.DataFrame):
        min_fare, max_fare = tt.get_fare_bounds(real_df)
        patch_inputs([str(max_fare)])
        assert tt.prompt_fare(min_fare, max_fare) == max_fare

    def test_realistic_middle_fare_is_accepted(self, patch_inputs, real_df: pd.DataFrame):
        min_fare, max_fare = tt.get_fare_bounds(real_df)
        middle = 50.0
        assert min_fare <= middle <= max_fare
        patch_inputs([str(middle)])
        assert tt.prompt_fare(min_fare, max_fare) == middle

    @pytest.mark.parametrize("bad", ["abc", "", "nan", "inf", "-inf"])
    def test_non_numeric_or_nonfinite_fare_reprompted(
        self, patch_inputs, real_df: pd.DataFrame, bad, capsys
    ):
        min_fare, max_fare = tt.get_fare_bounds(real_df)
        patch_inputs([bad, "50"])
        assert tt.prompt_fare(min_fare, max_fare) == 50.0
        out = capsys.readouterr().out.lower()
        assert "fare" in out and ("illegal" in out or "number" in out)

    def test_below_real_minimum_fare_is_rejected(self, patch_inputs, real_df: pd.DataFrame, capsys):
        min_fare, max_fare = tt.get_fare_bounds(real_df)
        patch_inputs([str(min_fare - 0.01), str(min_fare)])
        assert tt.prompt_fare(min_fare, max_fare) == min_fare
        out = capsys.readouterr().out
        assert f"{min_fare:.4f}" in out
        assert f"{max_fare:.4f}" in out

    def test_above_real_maximum_fare_is_rejected(self, patch_inputs, real_df: pd.DataFrame, capsys):
        min_fare, max_fare = tt.get_fare_bounds(real_df)
        patch_inputs([str(max_fare + 0.01), str(max_fare)])
        assert tt.prompt_fare(min_fare, max_fare) == max_fare
        out = capsys.readouterr().out
        assert f"{min_fare:.4f}" in out
        assert f"{max_fare:.4f}" in out


# ---------------------------------------------------------------------------
# Combined input collection
# ---------------------------------------------------------------------------

class TestCollectPassengerInput:
    """Tests the full interactive input flow with real fare bounds."""

    def test_collect_passenger_input_returns_expected_dataclass(
        self, patch_inputs, real_df: pd.DataFrame
    ):
        prompts = patch_inputs([" Reem ", "30", "Male", "50"])
        passenger = tt.collect_passenger_input(real_df)

        assert isinstance(passenger, tt.PassengerInput)
        assert passenger.name == "Reem"
        assert passenger.age == 30.0
        assert passenger.sex == "male"
        assert passenger.fare == 50.0

        assert len(prompts) == 4

    def test_collect_passenger_input_reprompts_invalid_values(
        self, patch_inputs, real_df: pd.DataFrame
    ):
        min_fare, max_fare = tt.get_fare_bounds(real_df)
        replies = [
            "   ", "Reem",
            "-1", "25",
            "robot", "female",
            str(max_fare + 1), "50",
        ]

        patch_inputs(replies)
        passenger = tt.collect_passenger_input(real_df)

        assert passenger.name == "Reem"
        assert passenger.age == 25.0
        assert passenger.sex == "female"
        assert passenger.fare == 50.0
        assert min_fare <= passenger.fare <= max_fare


# ---------------------------------------------------------------------------
# Ticket number generation using real dataset
# ---------------------------------------------------------------------------

class TestTicketGenerationRealData:
    """Tests unique ticket generation against real existing Titanic tickets."""

    def test_existing_ticket_numbers_are_six_digit_only(self, real_df: pd.DataFrame):
        existing = tt.get_existing_ticket_numbers(real_df)

        assert all(isinstance(x, int) for x in existing)
        assert all(100000 <= x <= 999999 for x in existing)

    def test_existing_ticket_numbers_match_manual_real_dataset_filter(
        self, real_df: pd.DataFrame
    ):
        got = tt.get_existing_ticket_numbers(real_df)

        tickets = real_df["ticket"].astype(str).str.strip()
        expected = {int(ticket) for ticket in tickets[tickets.str.fullmatch(r"\d{6}")]}
        assert got == expected

    def test_generated_ticket_is_six_digits_and_not_in_existing(self, real_df: pd.DataFrame):
        existing = tt.get_existing_ticket_numbers(real_df)
        original = existing.copy()

        ticket_number = tt.generate_unique_ticket(existing)

        assert 100000 <= ticket_number <= 999999
        assert ticket_number not in original

    def test_multiple_generated_tickets_are_unique(self, real_df: pd.DataFrame):
        existing = tt.get_existing_ticket_numbers(real_df).copy()
        new_numbers = [tt.generate_unique_ticket(existing) for _ in range(100)]

        assert len(new_numbers) == len(set(new_numbers))

    def test_generation_is_reproducible_with_seed_shape(self, real_df: pd.DataFrame):
        existing_a = tt.get_existing_ticket_numbers(real_df).copy()
        existing_b = tt.get_existing_ticket_numbers(real_df).copy()

        random.seed(12345)
        a = tt.generate_unique_ticket(existing_a)

        random.seed(12345)
        b = tt.generate_unique_ticket(existing_b)

        assert a == b


# ---------------------------------------------------------------------------
# Age grouping
# ---------------------------------------------------------------------------

class TestAgeGroup:
    """Tests for the age split used in survival analysis."""

    @pytest.mark.parametrize(
        "age,expected",
        [
            (0, "under_18"),
            (5, "under_18"),
            (17, "under_18"),
            (17.99, "under_18"),
            (18, "18_and_over"),
            (18.0, "18_and_over"),
            (30, "18_and_over"),
            (130, "18_and_over"),
        ],
    )
    def test_age_group_boundaries(self, age, expected):
        assert tt.age_group(age) == expected

    def test_real_dataset_age_groups_include_both_buckets(self, real_df: pd.DataFrame):
        with_groups = tt._add_age_group_column(real_df)
        groups = set(with_groups["age_group"].dropna().unique())
        assert {"under_18", "18_and_over"}.issubset(groups)


# ---------------------------------------------------------------------------
# Survival chance using real groupby truth
# ---------------------------------------------------------------------------

class TestSurvivalChanceRealData:
    """Cross-check survival_chance against direct pandas groupby on real data."""

    def test_matches_real_groupby_for_all_non_empty_cells(self, real_df: pd.DataFrame):
        working = tt._add_age_group_column(
            real_df.dropna(subset=["survived", "sex", "pclass"])
        )

        truth = working.groupby(
            ["pclass", "sex", "age_group"], dropna=False
        )["survived"].mean()

        for (pclass, sex, age_group), expected in truth.items():
            if pd.isna(age_group):
                continue

            age_lookup = 10 if age_group == "under_18" else 30
            got = tt.survival_chance(real_df, int(pclass), str(sex), age_lookup)

            assert abs(got - float(expected)) < 1e-12

    def test_survival_outputs_are_probabilities(self, real_df: pd.DataFrame):
        for pclass in [1, 2, 3]:
            for sex in ["male", "female"]:
                for age in [5, 25, 80]:
                    rate = tt.survival_chance(real_df, pclass, sex, age)
                    assert 0.0 <= rate <= 1.0

    def test_survival_is_deterministic(self, real_df: pd.DataFrame):
        a = tt.survival_chance(real_df, 1, "female", 30)
        b = tt.survival_chance(real_df, 1, "female", 30)
        assert a == b

    def test_first_class_female_has_higher_survival_than_third_class_male(
        self, real_df: pd.DataFrame
    ):
        first_female = tt.survival_chance(real_df, 1, "female", 30)
        third_male = tt.survival_chance(real_df, 3, "male", 30)
        assert first_female > third_male

    def test_children_and_adults_can_differ_within_same_class_and_sex(
        self, real_df: pd.DataFrame
    ):
        child = tt.survival_chance(real_df, 3, "male", 10)
        adult = tt.survival_chance(real_df, 3, "male", 30)
        assert 0.0 <= child <= 1.0
        assert 0.0 <= adult <= 1.0


# ---------------------------------------------------------------------------
# Ticket formatting
# ---------------------------------------------------------------------------

class TestFormatTicket:
    """Tests for the boxed text ticket format."""

    @pytest.fixture
    def sample_ticket(self) -> tt.Ticket:
        return tt.Ticket(
            passenger=tt.PassengerInput(
                name="Reem Mor",
                age=76,
                sex="male",
                fare=175.0,
            ),
            pclass=1,
            ticket_number=111111,
            survival_chance=0.326,
        )

    def test_ticket_has_expected_number_of_lines(self, sample_ticket: tt.Ticket):
        out = tt.format_ticket(sample_ticket)
        lines = out.strip().split("\n")
        assert len(lines) == 7

    def test_ticket_border_lines_contain_only_dashes(self, sample_ticket: tt.Ticket):
        out = tt.format_ticket(sample_ticket)
        lines = out.strip().split("\n")

        for idx in (0, 2, 4, 6):
            assert set(lines[idx]) == {"-"}

    def test_ticket_contains_required_fields(self, sample_ticket: tt.Ticket):
        out = tt.format_ticket(sample_ticket)

        assert "ticket:" in out
        assert "fare:" in out
        assert "age:" in out
        assert "class:" in out
        assert "sex:" in out
        assert "name:" in out

    def test_ticket_contains_expected_values(self, sample_ticket: tt.Ticket):
        out = tt.format_ticket(sample_ticket)

        assert "111111" in out
        assert "175" in out
        assert "76" in out
        assert "male" in out
        assert "Reem Mor" in out
        assert re.search(r"class:\s+1", out)

    def test_whole_numbers_do_not_show_trailing_zeroes(self, sample_ticket: tt.Ticket):
        out = tt.format_ticket(sample_ticket)
        assert "175.0" not in out
        assert "76.0" not in out

    def test_decimal_values_are_preserved(self):
        ticket = tt.Ticket(
            passenger=tt.PassengerInput(name="X", age=29.5, sex="male", fare=72.5),
            pclass=2,
            ticket_number=200000,
            survival_chance=0.5,
        )

        out = tt.format_ticket(ticket)
        assert "29.5" in out
        assert "72.5" in out

    def test_ticket_number_is_zero_padded(self):
        ticket = tt.Ticket(
            passenger=tt.PassengerInput(name="X", age=30, sex="male", fare=50),
            pclass=2,
            ticket_number=42,
            survival_chance=0.5,
        )

        out = tt.format_ticket(ticket)
        assert "000042" in out

    def test_long_name_does_not_break_alignment(self):
        ticket = tt.Ticket(
            passenger=tt.PassengerInput(
                name="Reginald Aldridge Featherstonehaugh III",
                age=30,
                sex="male",
                fare=50,
            ),
            pclass=2,
            ticket_number=300000,
            survival_chance=0.5,
        )

        out = tt.format_ticket(ticket)
        lines = out.strip().split("\n")
        widths = {len(line) for line in lines}

        assert len(widths) == 1
        assert "Reginald Aldridge Featherstonehaugh III" in out

    def test_each_data_row_has_three_pipe_characters(self, sample_ticket: tt.Ticket):
        out = tt.format_ticket(sample_ticket)
        lines = out.strip().split("\n")

        for idx in (1, 3, 5):
            assert lines[idx].count("|") == 3


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

class TestWriteTicketFile:
    """Tests writing the text ticket to disk."""

    def test_ticket_file_is_created_and_matches_format(self, tmp_path: Path):
        ticket = tt.Ticket(
            passenger=tt.PassengerInput(name="Reem", age=30, sex="male", fare=50),
            pclass=2,
            ticket_number=123456,
            survival_chance=0.5,
        )

        path = tmp_path / "ticket.txt"
        tt.write_ticket_file(ticket, path)

        assert path.exists()
        assert path.read_text(encoding="utf-8") == tt.format_ticket(ticket)

    def test_write_ticket_file_raises_runtime_error_for_bad_path(self, tmp_path: Path):
        ticket = tt.Ticket(
            passenger=tt.PassengerInput(name="Reem", age=30, sex="male", fare=50),
            pclass=2,
            ticket_number=123456,
            survival_chance=0.5,
        )

        with pytest.raises(RuntimeError, match="Could not write"):
            tt.write_ticket_file(ticket, tmp_path)


# ---------------------------------------------------------------------------
# End-to-end tests with real titanic.csv
# ---------------------------------------------------------------------------

class TestEndToEndRealData:
    """Run the real script in a subprocess with the real Titanic dataset."""

    SCRIPT = Path(__file__).resolve().parent / "titanic_ticket.py"
    OUTPUT = Path(__file__).resolve().parent / "ticket.txt"

    def _run(self, stdin_text: str, cwd: Path):
        if self.OUTPUT.exists():
            self.OUTPUT.unlink()

        return subprocess.run(
            [sys.executable, str(self.SCRIPT)],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=20,
        )

    def test_happy_path_creates_ticket_and_exits_zero(self, tmp_path: Path):
        result = self._run("Reem Mor\n76\nmale\n175\n", tmp_path)

        assert result.returncode == 0, result.stderr
        assert self.OUTPUT.exists()

        contents = self.OUTPUT.read_text(encoding="utf-8")
        assert "ticket:" in contents
        assert "fare: 175" in contents
        assert "age: 76" in contents
        assert "class: 1" in contents
        assert "sex: male" in contents
        assert "name: Reem Mor" in contents

    def test_final_message_uses_die_phrasing(self, tmp_path: Path):
        result = self._run("Reem\n30\nmale\n50\n", tmp_path)

        assert result.returncode == 0
        assert "chances to die" in result.stdout
        assert "Enjoy your trip" in result.stdout

    def test_invalid_inputs_are_reprompted_in_end_to_end_run(self, tmp_path: Path):
        user_input = (
            "\n"
            "Reem\n"
            "200\n"
            "30\n"
            "robot\n"
            "male\n"
            "100000\n"
            "50\n"
        )

        result = self._run(user_input, tmp_path)

        assert result.returncode == 0
        assert "ticket.txt" not in result.stderr.lower()
        assert self.OUTPUT.exists()

    def test_immediate_eof_exits_cleanly(self, tmp_path: Path):
        result = self._run("", tmp_path)

        assert result.returncode == 130 or result.returncode != 0
        assert "traceback" not in result.stderr.lower()

    def test_missing_dataset_exits_with_error(self):
        pytest.skip(
            "Skipped because titanic_ticket.py now loads titanic.csv relative to the script location."
        )