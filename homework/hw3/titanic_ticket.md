# Titanic Ticket Purchasing System

This project simulates buying a new ticket for the Titanic ride using historical data from the Titanic dataset.

The program loads the dataset, validates passenger input, finds the passenger class based on the fare, generates a unique 6-digit ticket number, estimates survival chance, writes the ticket to a text file, and prints a final message to the user.

## Project Goal

The goal of this project is to build a small command-line system that follows the assignment instructions and uses the Titanic dataset as the source for validation and calculations.

The program checks that the passenger data is legal, places the passenger in the correct class, and estimates the chance of survival based on similar passengers in the dataset.

## Features

- Loads the Titanic dataset from `titanic.csv`
- Validates that required columns exist
- Cleans important dataset fields before using them
- Collects passenger details from the user
- Validates `name`, `age`, `sex`, and `fare`
- Finds the passenger class from the fare
- Prefers the better class if fare ranges overlap
- Generates a unique 6-digit ticket number
- Calculates survival chance from the dataset
- Writes the final ticket to `ticket.txt`

## Project Rules

This solution follows the assignment rules:

- Fare must be between the minimum and maximum positive fares in the dataset
- Zero fares and missing fares are ignored when calculating fare bounds
- Age must be between 0 and 130
- Sex must be either `male` or `female`
- Leading and trailing spaces are removed from the name
- If a fare matches more than one class range, the better class is chosen
- Ticket numbers must be unique and contain exactly 6 digits

## Project Flow

The program works in the following order:

1. Load the Titanic dataset
2. Check that the dataset contains the required columns
3. Clean and normalize important columns
4. Ask the user for name, age, sex, and fare
5. Validate all input values
6. Calculate fare ranges for each passenger class
7. Infer the passenger class from the fare
8. Generate a unique 6-digit ticket number
9. Calculate the passenger's survival chance
10. Create the final ticket text
11. Write the ticket to `ticket.txt`
12. Print the final message

## Dataset Cleaning

Before using the dataset, the program performs a few cleaning steps:

- Numeric columns are converted safely using `errors="coerce"`
- The `sex` column is converted to lowercase and stripped
- The `ticket` column is converted to string and stripped

This makes the later comparisons and calculations more reliable.

## Survival Chance Logic

The survival chance is calculated from the Titanic dataset by grouping passengers according to:

- passenger class
- sex
- age group

The age groups used are:

- `under_18`
- `18_and_over`

The program then calculates the mean of the `survived` column for the matching group.

If the exact group is missing, the program uses broader fallback groups so the calculation can still succeed.

## Ticket Output

The final ticket is written to a file named `ticket.txt`.

The ticket includes:

- ticket number
- fare
- age
- class
- sex
- passenger name

The output is shown in a boxed text layout.

Example:

```text
-----------------------------------------------------
| ticket: 123456         | fare: 175               |
-----------------------------------------------------
| age: 76                | class: 1                |
-----------------------------------------------------
| sex: male              | name: Re'em Mor    |
-----------------------------------------------------
```

## Project Structure

```text
.
├── titanic_ticket.py
├── titanic.csv
├── ticket.txt
└── test_titanic_ticket.md, titanic_ticket_
```

## How to Run

Make sure that:

- Python is installed
- `pandas` is installed
- `titanic.csv` is in the same folder as the program

Run the program with:

```bash
python titanic_ticket.py
```

## Example Run

```text
Welcome to the Titanic ticket system.
Please enter your passenger details below.
Please enter your name: Re'em Mor
Please enter your age: 76
Please enter your sex (male/female): male
Please enter your fare (between 3.1708 and 512.3292): 175
Dear Re'em Mor, your chances to die on our trip are 67.4%.
Enjoy your trip and stay safe! :)
```

## Design Choices

This project follows a clean and simple structure:

- small functions with clear responsibilities
- constants instead of hardcoded values
- dataclasses for passenger and ticket data
- input validation with repeated prompts
- safe file and dataset error handling
- readable formatting and comments

## Summary

This project demonstrates input validation, dataset cleaning, grouping with pandas, random unique ID generation, file writing, and clean program structure in Python.

It was written as a readable and organized solution for the Titanic ticket assignment.