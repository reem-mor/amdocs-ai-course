"""
Hello, This is a Titanic Ticket Purchasing System. 
Student Name: Re'em Mor
Lecturer: Alexander Kuznetsov
================================

This program simulates buying a new ticket for the Titanic ride.

What the program does:
1. Loads the Titanic dataset from a CSV file.
2. Gets passenger input from the user.
3. Validates name, age, sex, and fare.
4. Finds the passenger class based on the fare.
5. Generates a unique 6-digit ticket number.
6. Calculates the passenger's survival chance from the dataset.
7. Writes the final ticket to a text file.
8. Prints a final message to the user.

Project rules:
- Fare must be between the minimum and maximum positive fares in the dataset.
- Zero fares and missing fares are ignored when calculating fare ranges.
- Age must be between 0 and 130.
- Sex must be either "male" or "female".
- Leading and trailing spaces are removed from the name.
- If a fare fits more than one class range, the better class is chosen.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Input and output files
DATASET_PATH = Path(__file__).resolve().parent / "titanic.csv"
OUTPUT_PATH = Path(__file__).resolve().parent / "ticket.txt"

# Columns required from the Titanic dataset
REQUIRED_COLUMNS: tuple[str, ...] = (
    "pclass",
    "survived",
    "name",
    "sex",
    "age",
    "fare",
    "ticket",
)

# Allowed values for sex
ALLOWED_SEX: tuple[str, ...] = ("male", "female")

# Legal age range
MIN_AGE = 0.0
MAX_AGE = 130.0

# Ticket number settings
TICKET_DIGITS = 6
TICKET_MIN = 10 ** (TICKET_DIGITS - 1)   # 100000
TICKET_MAX = 10 ** TICKET_DIGITS - 1     # 999999
MAX_TICKET_ATTEMPTS = 10_000

# Age groups used in the survival calculation
AGE_SPLIT = 18.0
AGE_GROUP_MINOR = "under_18"
AGE_GROUP_ADULT = "18_and_over"

# Default ticket cell width
TICKET_CELL_WIDTH = 25


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PassengerInput:
    """Passenger details after validation."""
    name: str
    age: float
    sex: str
    fare: float


@dataclass(frozen=True)
class Ticket:
    """Final ticket data."""
    passenger: PassengerInput
    pclass: int
    ticket_number: int
    survival_chance: float


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class UserCancelled(Exception):
    """Raised when the user stops the program during input."""


# ---------------------------------------------------------------------------
# Dataset loading and cleaning
# ---------------------------------------------------------------------------

def load_dataset(path: Path) -> pd.DataFrame:
    """
    Load the Titanic dataset and check that it is valid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    try:
        # Skip broken rows instead of stopping the whole program
        df = pd.read_csv(path, on_bad_lines="skip")
    except pd.errors.EmptyDataError as exc:
        raise ValueError(f"Dataset file is empty: {path}") from exc
    except pd.errors.ParserError as exc:
        raise ValueError(f"Dataset file is not a valid CSV file: {path}") from exc

    if df.empty:
        raise ValueError(f"Dataset has no rows: {path}")

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(f"Dataset is missing required columns: {missing_columns}")

    df = df.copy()

    # Convert numeric columns safely
    df["fare"] = pd.to_numeric(df["fare"], errors="coerce")
    df["age"] = pd.to_numeric(df["age"], errors="coerce")
    df["pclass"] = pd.to_numeric(df["pclass"], errors="coerce")
    df["survived"] = pd.to_numeric(df["survived"], errors="coerce")

    # Normalize text columns
    df["sex"] = df["sex"].astype(str).str.strip().str.lower()
    df["ticket"] = df["ticket"].astype(str).str.strip()

    return df


def get_fare_bounds(df: pd.DataFrame) -> tuple[float, float]:
    """
    Return the minimum and maximum legal fare from the dataset.
    """
    # Ignore zero and missing fares according to the project instructions
    fares = df.loc[df["fare"] > 0, "fare"].dropna()

    if fares.empty:
        raise ValueError("Dataset contains no usable positive fare values.")

    return float(fares.min()), float(fares.max())


def get_class_fare_ranges(df: pd.DataFrame) -> dict[int, tuple[float, float]]:
    """
    Return the fare range for each passenger class.
    """
    usable = df[(df["fare"] > 0) & df["fare"].notna() & df["pclass"].notna()]

    ranges: dict[int, tuple[float, float]] = {}

    for pclass, group in usable.groupby("pclass"):
        fares = group["fare"]
        if not fares.empty:
            ranges[int(pclass)] = (float(fares.min()), float(fares.max()))

    if not ranges:
        raise ValueError("No class fare ranges could be computed from the dataset.")

    return ranges


# ---------------------------------------------------------------------------
# Input handling and validation
# ---------------------------------------------------------------------------

def _safe_input(prompt: str) -> str:
    """
    Read user input safely.
    """
    try:
        return input(prompt)
    except EOFError as exc:
        raise UserCancelled("end of input") from exc
    except KeyboardInterrupt as exc:
        raise UserCancelled("interrupted by user") from exc


def _parse_finite_float(raw: str) -> Optional[float]:
    """
    Convert a string to a valid finite float.
    """
    try:
        value = float(raw)
    except ValueError:
        return None

    if value != value or value in (float("inf"), float("-inf")):
        return None

    return value


def prompt_name() -> str:
    """
    Ask the user for a valid name.
    """
    while True:
        raw = _safe_input("Please enter your name: ")
        cleaned = raw.strip()

        if not cleaned:
            print("Name is wrong. Please enter a valid name.")
            continue

        if any(ch.isdigit() for ch in cleaned):
            print("Name is wrong. Digits are not allowed in the name.")
            continue

        allowed_symbols = {" ", "-", "'"}
        if not all(ch.isalpha() or ch in allowed_symbols for ch in cleaned):
            print("Name is wrong. Use letters, spaces, hyphens, or apostrophes only.")
            continue

        if not any(ch.isalpha() for ch in cleaned):
            print("Name is wrong. Please enter a valid name.")
            continue

        return cleaned


def prompt_age() -> float:
    """
    Ask the user for age until a valid value is entered.
    """
    while True:
        raw = _safe_input("Please enter your age: ").strip()
        value = _parse_finite_float(raw)

        if value is None:
            print("Age is wrong. Please enter a number between 0 and 130.")
            continue

        if not (MIN_AGE <= value <= MAX_AGE):
            print("Age is wrong. Please enter a number between 0 and 130.")
            continue

        return value


def prompt_sex() -> str:
    """
    Ask the user for sex until a valid value is entered.
    """
    while True:
        raw = _safe_input("Please enter your sex (male/female): ")
        cleaned = raw.strip().lower()

        if cleaned in ALLOWED_SEX:
            return cleaned

        print("Sex value is illegal. Please enter male or female.")


def prompt_fare(min_fare: float, max_fare: float) -> float:
    """
    Ask the user for fare until a valid value is entered.
    """
    while True:
        raw = _safe_input(
            f"Please enter your fare (between {min_fare:.4f} and {max_fare:.4f}): "
        ).strip()

        value = _parse_finite_float(raw)

        if value is None:
            print(
                f"Fare payment is illegal. Please enter a number between "
                f"{min_fare:.4f} and {max_fare:.4f}."
            )
            continue

        if not (min_fare <= value <= max_fare):
            print(
                f"Fare payment is illegal. Please enter a value between "
                f"{min_fare:.4f} and {max_fare:.4f}."
            )
            continue

        return value


def collect_passenger_input(df: pd.DataFrame) -> PassengerInput:
    """
    Collect and validate all passenger input fields.
    """
    min_fare, max_fare = get_fare_bounds(df)

    name = prompt_name()
    age = prompt_age()
    sex = prompt_sex()
    fare = prompt_fare(min_fare, max_fare)

    return PassengerInput(name=name, age=age, sex=sex, fare=fare)


# ---------------------------------------------------------------------------
# Class inference
# ---------------------------------------------------------------------------

def infer_pclass(fare: float, class_ranges: dict[int, tuple[float, float]]) -> int:
    """
    Find the passenger class from the fare.
    """
    matches = [
        pclass
        for pclass, (low, high) in class_ranges.items()
        if low <= fare <= high
    ]

    # If the fare matches more than one class, choose the better one
    if matches:
        return min(matches)

    # Fallback: choose the nearest class range
    def distance(pclass: int) -> tuple[float, int]:
        low, high = class_ranges[pclass]

        if fare < low:
            return (low - fare, pclass)
        if fare > high:
            return (fare - high, pclass)
        return (0.0, pclass)

    return min(class_ranges, key=distance)


# ---------------------------------------------------------------------------
# Ticket number generation
# ---------------------------------------------------------------------------

def get_existing_ticket_numbers(df: pd.DataFrame) -> set[int]:
    """
    Return all existing 6-digit numeric ticket numbers.
    """
    tickets = df["ticket"].astype(str).str.strip()
    six_digit_tickets = tickets[tickets.str.fullmatch(r"\d{6}")]

    return {int(ticket) for ticket in six_digit_tickets}


def generate_unique_ticket(existing: set[int]) -> int:
    """
    Generate a unique 6-digit ticket number.
    """
    if len(existing) >= (TICKET_MAX - TICKET_MIN + 1):
        raise RuntimeError("All 6-digit ticket numbers are already taken.")

    for _ in range(MAX_TICKET_ATTEMPTS):
        candidate = random.randint(TICKET_MIN, TICKET_MAX)
        if candidate not in existing:
            existing.add(candidate)
            return candidate

    raise RuntimeError("Failed to generate a unique ticket number.")


# ---------------------------------------------------------------------------
# Survival chance calculation
# ---------------------------------------------------------------------------

def age_group(age: float) -> str:
    """
    Return the passenger's age group.
    """
    return AGE_GROUP_MINOR if age < AGE_SPLIT else AGE_GROUP_ADULT


def _add_age_group_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add an age_group column to the dataframe.
    """
    result = df.copy()
    result["age_group"] = pd.Series(pd.NA, index=result.index, dtype="object")

    has_age = result["age"].notna()
    result.loc[has_age & (result["age"] < AGE_SPLIT), "age_group"] = AGE_GROUP_MINOR
    result.loc[has_age & (result["age"] >= AGE_SPLIT), "age_group"] = AGE_GROUP_ADULT

    return result


def _mean_survived(subset: pd.DataFrame) -> Optional[float]:
    """
    Return the mean of the survived column for a subset.
    """
    if subset.empty:
        return None

    survived = subset["survived"].dropna()
    if survived.empty:
        return None

    return float(survived.mean())


def survival_chance(df: pd.DataFrame, pclass: int, sex: str, age: float) -> float:
    """
    Calculate the passenger's survival chance.
    """
    target_group = age_group(age)

    # Use only rows that can be used for the survival calculation
    working = _add_age_group_column(
        df.dropna(subset=["survived", "sex", "pclass"])
    )

    grouped = working.groupby(
        ["pclass", "sex", "age_group"], dropna=False
    )["survived"].mean()

    key = (pclass, sex, target_group)

    if key in grouped.index:
        rate = grouped.loc[key]
        if pd.notna(rate):
            return float(rate)

    # If the exact group is missing, widen the search
    fallback_sets = [
        working[(working["pclass"] == pclass) & (working["sex"] == sex)],
        working[working["sex"] == sex],
        working,
    ]

    for subset in fallback_sets:
        rate = _mean_survived(subset)
        if rate is not None:
            return rate

    raise ValueError("Cannot compute survival chance from the dataset.")


# ---------------------------------------------------------------------------
# Ticket formatting and file writing
# ---------------------------------------------------------------------------

def _format_cell(field: str, value: object, width: int = TICKET_CELL_WIDTH) -> str:
    """
    Format one ticket cell and pad it to a fixed width.
    """
    return f" {field}: {value}".ljust(width)


def _format_value(value: float) -> str:
    """
    Format a number without unnecessary zeros.
    """
    return f"{value:g}"


def format_ticket(ticket: Ticket) -> str:
    """
    Build the final ticket text.
    """
    passenger = ticket.passenger

    rows = [
        ("ticket", f"{ticket.ticket_number:06d}", "fare", _format_value(passenger.fare)),
        ("age", _format_value(passenger.age), "class", ticket.pclass),
        ("sex", passenger.sex, "name", passenger.name),
    ]

    # Make the layout wide enough even for long names
    longest_cell = max(
        len(f" {field}: {value}")
        for left_field, left_value, right_field, right_value in rows
        for field, value in ((left_field, left_value), (right_field, right_value))
    )

    cell_width = max(TICKET_CELL_WIDTH, longest_cell + 1)
    total_width = 2 * cell_width + 3
    border = "-" * total_width

    lines = [border]

    for left_field, left_value, right_field, right_value in rows:
        left_cell = _format_cell(left_field, left_value, cell_width)
        right_cell = _format_cell(right_field, right_value, cell_width)
        lines.append(f"|{left_cell}|{right_cell}|")
        lines.append(border)

    return "\n".join(lines) + "\n"


def write_ticket_file(ticket: Ticket, path: Path) -> None:
    """
    Write the ticket to a text file.
    """
    try:
        path.write_text(format_ticket(ticket), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Could not write ticket file {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Main program flow
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Run the full Titanic ticket purchase process.
    """
    try:
        df = load_dataset(DATASET_PATH)
        class_ranges = get_class_fare_ranges(df)

        print("Welcome to the Titanic ticket system.")
        print("Please enter your passenger details below.")

        passenger = collect_passenger_input(df)

        pclass = infer_pclass(passenger.fare, class_ranges)

        existing_tickets = get_existing_ticket_numbers(df)
        ticket_number = generate_unique_ticket(existing_tickets)

        chance = survival_chance(df, pclass, passenger.sex, passenger.age)

        ticket = Ticket(
            passenger=passenger,
            pclass=pclass,
            ticket_number=ticket_number,
            survival_chance=chance,
        )

        write_ticket_file(ticket, OUTPUT_PATH)

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except UserCancelled as exc:
        print(f"\nCancelled ({exc}). No ticket was issued.", file=sys.stderr)
        return 130

    # The project example prints chance to die
    die_chance = (1 - chance) * 100

    print(f"Dear {passenger.name}, your chances to die on our trip are {die_chance:.1f}%.")
    print("Enjoy your trip and stay safe! :)")

    return 0


if __name__ == "__main__":
    sys.exit(main())