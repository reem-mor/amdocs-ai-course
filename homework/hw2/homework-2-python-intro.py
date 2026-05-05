## Re'em Mor
## ID:311117774
## Lecture 2 - Python Intro Homework
## Date of submission: 2026-05-05



# QUESTION 1

my_info = ["Reem", "Mor", 32, "0526775754"]

for item in my_info:
    print(item)


# QUESTION 2

my_info_dict = {
    ("name", "last_name"): "Reem Mor",
    "age": 32,
    "phone number": "05267757547"
}

print(my_info_dict)


# QUESTION 3

# Return an error if the lists are not the same length.
def max_list(lst1, lst2):
    if len(lst1) != len(lst2):
        raise ValueError("Lists must be of the same length")

    new_list = []
    for a, b in zip(lst1, lst2):
        new_list.append(max(a, b))

    return new_list


lst1 = [1, 2, 3, 4, 5]
lst2 = [5, 4, 3, 2, 1]

print(max_list(lst1, lst2))


# QUESTION 4

# Stop the program if a string appears in the list.
values = [1, 2, 3, 4, 5, 6, 7, 8, 9]

even_count = 0
odd_count = 0
found_string = False

for item in values:
    if isinstance(item, str):
        print("It's a string!!!")
        even_count = 0
        odd_count = 0
        found_string = True
        break

    if item % 2 == 0:
        even_count += 1
    else:
        odd_count += 1

if not found_string:
    print(f"Number of even numbers: {even_count}")
    print(f"Number of odd numbers: {odd_count}")


# Extra example with a string inside the list
values = [1, 2, 3, 4, "Oops", 6, 7, 8, 9]

even_count = 0
odd_count = 0
found_string = False

for item in values:
    if isinstance(item, str):
        print("It's a string!!!")
        even_count = 0
        odd_count = 0
        found_string = True
        break

    if item % 2 == 0:
        even_count += 1
    else:
        odd_count += 1

if not found_string:
    print(f"Number of even numbers: {even_count}")
    print(f"Number of odd numbers: {odd_count}")


# QUESTION 5

def generate_dictionary(n):
    result = {}

    for x in range(1, n + 1):
        result[x] = x + 3

    return result


print(generate_dictionary(5))


# QUESTION 6

dic1 = {1: 10, 2: 20}
dic2 = {3: 30, 4: 40}
dic3 = {5: 50, 6: 60}

new_dict = {}
new_dict.update(dic1)
new_dict.update(dic2)
new_dict.update(dic3)

print(new_dict)


# QUESTION 7

# Ignore spaces when counting characters.
def count_characters(text):
    char_count = {}

    for char in text:
        if char != " ":
            char_count[char] = char_count.get(char, 0) + 1

    return char_count


print(count_characters(" HANNA"))


# QUESTION 8

# Add values for keys that appear in both dictionaries.
d1 = {'a': 100, 'b': 200, 'c': 300}
d2 = {'a': 300, 'b': 200, 'd': 400}

combined_dict = {}

for key in d1:
    combined_dict[key] = d1[key]

for key in d2:
    if key in combined_dict:
        combined_dict[key] += d2[key]
    else:
        combined_dict[key] = d2[key]

print(combined_dict)
    
# QUESTION 9

# Keep only the first appearance of each value.
def unique_list(items):
    unique_items = []

    for item in items:
        if item not in unique_items:
            unique_items.append(item)

    return unique_items


print(unique_list([1, 2, 3, 3, 3, 3, 4, 5]))


# QUESTION 10

# Use a nested loop to print numbers from 1 up to the current row.
for i in range(1, 9):
    for j in range(1, i + 1):
        print(j, end="")
    print()


# QUESTION 11

# Print the pattern line by line exactly as shown in the exercise.
print("****")
print("*")
print("*")
print(" ***")
print("    *")
print("    *")
print("****")