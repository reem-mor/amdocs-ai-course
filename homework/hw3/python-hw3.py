## Re'em Mor
## ID: 311117774


# QUESTION1 + QUESTION2

class Vehicle:
    def __init__(self, name, max_speed, mileage):
        self.name = name
        self.max_speed = max_speed
        self.mileage = mileage


vehicle1 = Vehicle("Toyota", 180, 25000)


class Bus(Vehicle):
    pass


bus1 = Bus("School Bus", 120, 80000)

print(vehicle1.name, vehicle1.max_speed, vehicle1.mileage)
print(bus1.name, bus1.max_speed, bus1.mileage)


# QUESTION3

class MyString:
    def __init__(self):
        self.text = ""

    def get_String(self):
        self.text = input("Enter a string: ")

    def print_String(self):
        print(self.text.upper())


obj = MyString()
obj.get_String()
obj.print_String()


# QUESTION4

with open("my_id.txt", "w") as file:
    file.write("name: Reem Mor\n")
    file.write("age: 32\n")
    file.write("phone number: 0526775754\n")



# QUESTION5

def count_words_in_file(file_path):
    word_count = {}

    with open(file_path, "r") as file:
        words = file.read().split()

    for word in words:
        word_count[word] = word_count.get(word, 0) + 1

    return word_count


result = count_words_in_file("my_id.txt")
print(result)


# QUESTION6

def find_longest_word(file_path):
    with open(file_path, "r") as file:
        words = file.read().split()

    longest_word = ""

    for word in words:
        if len(word) > len(longest_word):
            longest_word = word

    return longest_word


result = find_longest_word("my_id.txt")
print(result)



# QUESTION7

def sum_list(numbers):
    total = 0

    for num in numbers:
        total += num

    return total


print(sum_list([1, 2, 3, 4, 5]))




# QUESTION8

def multiply_list(numbers):
    result = 1

    for num in numbers:
        result *= num

    return result


print(multiply_list([1, 2, 3, 4]))


# QUESTION9

def find_min_value(numbers):
    min_value = numbers[0]

    for num in numbers:
        if num < min_value:
            min_value = num

    return min_value


print(find_min_value([8, 3, 12, 1, 6]))




# QUESTION10

def count_letters(text):
    upper_count = 0
    lower_count = 0

    for char in text:
        if char.isupper():
            upper_count += 1
        elif char.islower():
            lower_count += 1

    return {"uppercase": upper_count, "lowercase": lower_count}


result = count_letters("Hello World")
print(result)


# QUESTION11

import numpy as np

arr = np.arange(10)
print(arr)


# QUESTION12

import numpy as np

arr = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9])
odd_arr = arr[arr % 2 != 0]

print(odd_arr)




# QUESTION13

import numpy as np

arr = np.eye(5)
arr[arr > 0] = -1

print(arr)


# QUESTION14

def power(a, b):
    if b == 0:
        return 1
    return a * power(a, b - 1)


print(power(3, 2))



# QUESTION15

class Stack:
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)

    def pop(self):
        if not self.is_empty():
            return self.items.pop()
        return None

    def is_empty(self):
        return len(self.items) == 0


class py_solution:
    def is_valid_parenthese(self, text):
        stack = Stack()
        pairs = {
            ')': '(',
            '}': '{',
            ']': '['
        }
        valid_chars = set("(){}[]")

        for char in text:
            if char not in valid_chars:
                return False

            if char in "({[":
                stack.push(char)
            else:
                if stack.is_empty() or stack.pop() != pairs[char]:
                    return False

        return stack.is_empty()


solution = py_solution()

print(solution.is_valid_parenthese("()"))
print(solution.is_valid_parenthese("()[]{}"))
print(solution.is_valid_parenthese("[)"))
print(solution.is_valid_parenthese("({[)]"))
print(solution.is_valid_parenthese("{{{"))
print(solution.is_valid_parenthese(""))
print(solution.is_valid_parenthese("({[]})"))
print(solution.is_valid_parenthese("abc"))