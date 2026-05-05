# Homework #3

## Question 1 — Vehicle class
Create a new class named `Vehicle`.

Requirements:
- Add `name`, `max_speed`, and `mileage` as parameters inside the `__init__` function.
- Create one `Vehicle` object.

## Question 2 — Bus class
Create a child class named `Bus` that inherits from the `Vehicle` class.

Requirements:
- The `Bus` class should inherit all variables and methods from `Vehicle`.
- Create one `Bus` object.

## Question 3 — String methods class
Write a Python class with two methods:
- `get_String` — accepts a string from the user.
- `print_String` — prints the string in uppercase.

## Question 4 — Text file
Write a text file with your identification information.

Requirements:
- The file name must be `my_id.txt`.

## Question 5 — Word frequency in a file
Write a Python function that counts how many times each word appears in a text file.

Requirements:
- The function should receive the path of the `.txt` file as input.
- The function should return a dictionary.
- Each key should be a word from the file.
- Each value should be the number of times that word appears in the file.

Example output:

```python
{'name:': 1, 'Alex': 1, 'Kuznetsov': 1, 'age:': 1, '27': 1, 'phone number:': 1, '0527389001': 1}
```

## Question 6 — Longest word in a file
Write a Python program that finds the longest word in a text file.

## Question 7 — Sum of a list
Build a function that receives a list of integers as input.

Requirements:
- Calculate the sum of all integers in the list.
- Return the final sum.

## Question 8 — Multiply all values in a list
Build a function that receives a list of integers as input.

Requirements:
- Multiply all values in the list.
- Return the final result.

## Question 9 — Minimal value in a list
Build a function that receives a list of integers as input.

Requirements:
- Find the minimal value in the list.
- Return that value.

## Question 10 — Count uppercase and lowercase letters
Build a Python function that accepts a string.

Requirements:
- Count the number of uppercase letters.
- Count the number of lowercase letters.

## Question 11 — NumPy array from 0 to 9
Create a 1D NumPy array with numbers from `0` to `9`.

Requirements:
- Do not use any loops.

## Question 12 — Extract odd numbers from a NumPy array
Create a 1D NumPy array with number values, either integers or floats.

Requirements:
- Extract all odd numbers into a new array.
- Do not use any loops.

## Challenges

### Challenge 13 — 5x5 NumPy eye array
Create a `5 x 5` 2D NumPy eye array.

Requirements:
- Replace all values that are greater than `0` with `-1`.
- Do not use any loops.

### Challenge 14 — Recursive power function
Create a recursive function that calculates the value of `a` to the power of `b`.

Example:
- `power(3, 2)` means calculating `3^2`
- Expected result: `9`

### Challenge 15 — Valid parentheses
Write a Python program that checks whether a string of parentheses and brackets is valid.

Use these bracket types:
- `(` and `)`
- `{` and `}`
- `[` and `]`

Requirements:
- Brackets must be closed in the correct order.
- `()` and `()[]{}` are valid examples.
- `[)`, `({[)]`, and `{{{` are invalid examples.
- Use a `Stack` class structure to solve this exercise.