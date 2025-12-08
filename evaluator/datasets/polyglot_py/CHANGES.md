# Changes to the Polyglot dataset

The Ridges team has made some changes to the original Polyglot problem set, which was sourced originally from [this repository](https://github.com/exercism/python/tree/main/exercises/practice). The reason for these changes were because some of the questions are believed (by us) to be practically unsolvable due to errors in the problem statement or tests that do not correlate exactly with the instructions. All changes made to the problem set are documented here for transparency.

<br>
<br>

## accumulate

No changes.

## acronym

No changes.

## affine-cipher

Added typing to `encode()` and `decode()`, as it is unclear what types the parameters should be and what the functions should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## all-your-base

No changes.

## allergies

No changes.

## alphametics

Added typing to `solve()`, as it is unclear what type the parameter should be and what the function should return. Added clarification to the instructions about the expected return format (dict mapping letters to digits, or None if no solution exists).

## anagram

Added typing to `find_anagrams()`, as it is unclear what the parameter types should be and what the function should return.

## armstrong-numbers

Added typing to `is_armstrong_number()`, as it is unclear what the parameter type should be and what the function should return.

## atbash-cipher

Added typing to `encode()` and `decode()`, as it is unclear what the parameter types should be and what the functions should return.

## bank-account

Added typing to `get_balance()`, `open()`, `deposit()`, `withdraw()`, and `close()`, as it is unclear what the parameter types should be and what the methods should return.

## binary

Added typing to `parse_binary()`, as it is unclear what the parameter type should be and what the function should return.

## binary-search

Added typing to `find()`, as it is unclear what the parameter types should be and what the function should return.

## binary-search-tree

Added typing to `TreeNode.__init__()`, `BinarySearchTree.__init__()`, `data()`, and `sorted_data()`, as it is unclear what the parameter types should be and what the methods should return. 

## bob

Added typing to `response()`, as it is unclear what the parameter type should be and what the function should return.

## beer-song

Added typing to `recite()`, as it is unclear whether the function should return a list of strings, or a single string, separated by newlines.

## book-store

Added typing to `total()`, as it is unclear what format of input the function should expect. Changed the return type to `int` and added a comment (`# in cents`) to indicate the format of the output, as the instructions do not specify whether the solution should return an answer as a decimal number of dollars, or an integer number of cents. In fact, the instructions lean towards decimal dollars, whereas the tests use integer cents.

## bottle-song

Added typing to `recite()`, as it is unclear whether the function should return a list of strings, or a single string, separated by newlines.

## bowling

No changes. The instructions provide enough typing information, as such, it is not necessary to include it in the Python file.

## change

Added typing to `find_fewest_coins()`, as it is unclear what the parameter types should be and what the function should return.

## circular-buffer

Added typing to `BufferFullException.__init__()`, `BufferEmptyException.__init__()`, `CircularBuffer.__init__()`, `read()`, `write()`, `overwrite()`, and `clear()`, as it is unclear what the parameter types should be and what the methods should return.

## clock

Added typing to `__init__()`, `__repr__()`, `__str__()`, `__eq__()`, `__add__()`, and `__sub__()`, as it is unclear what the parameter types should be and what the methods should return.

## collatz-conjecture

Added typing to `steps()`, as it is unclear what the parameter type should be and what the function should return.

## complex-numbers

Added typing to `__init__()`, `__eq__()`, `__add__()`, `__mul__()`, `__sub__()`, `__truediv__()`, `__abs__()`, `conjugate()`, and `exp()`, as it is unclear what the parameter types should be and what the methods should return.

## connect

Added typing to `__init__()` and `get_winner()`, as it is unclear what type the board representation should be and what format the winner should be returned in. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## crypto-square

Added typing to `cipher_text()`, as it is unclear what the parameter type should be and what the function should return.

## custom-set

Added typing to `__init__()`, `isempty()`, `__contains__()`, `issubset()`, `isdisjoint()`, `__eq__()`, `add()`, `intersection()`, `__sub__()`, and `__add__()`, as it is unclear what the parameter types should be and what the methods should return.

## darts

Added typing to `score()`, as it is unclear what the parameter types should be and what the function should return.

## diamond

Added typing to `rows()`, as it is unclear what the parameter type should be and what the function should return.

## difference-of-squares

Added typing to `square_of_sum()`, `sum_of_squares()`, and `difference_of_squares()`, as it is unclear what the parameter type should be and what the functions should return.

## diffie-hellman

Added typing to `private_key()`, `public_key()`, and `secret()`, as it is unclear what the parameter types should be and what the functions should return.

## dnd-character

Added typing to `modifier()`, as it is unclear what the parameter type should be and what the function should return.

## dominoes

Added typing to `can_chain()`, as it is unclear what the parameter types should be and what the function should return.

## eliuds-eggs

Added typing to `egg_count()`, as it is unclear what the parameter type should be and what the function should return.

## etl

Added typing to `transform()`, as it is unclear what the parameter type should be and what the function should return.

## flatten-array

Added typing to `flatten()`, as it is unclear what the parameter type should be and what the function should return.

## flower-field

Added typing to `annotate()`, as it is unclear what the parameter type should be and what the function should return.

## dot-dsl

Added typing to `Node.__init__()`, `Edge.__init__()`, and `Graph.__init__()`, as it is unclear what types the parameters should be. Removed the redundant error messages `"Attribute is malformed"`, `"Node is malformed"`, and `"Edge is malformed"` from `solution.py`, `tests.py`, and `instructions.md` because they were only being used for length validation (wrong number of tuple elements), which is inconsistent - malformed items should raise `TypeError`, not `ValueError`. The solution and tests now consistently raise `TypeError("Graph item malformed")` for all malformed items (wrong number of elements) regardless of item type (`ATTR`, `NODE`, or `EDGE`). The only error messages that remain are: `"Graph data malformed"` (when data is not a list), `"Graph item malformed"` (when a tuple has the wrong number of elements, whether too few or too many), and `"Unknown item"` (when the item type is not `ATTR`, `NODE`, or `EDGE`). The original instructions only documented 2 error messages, so documentation was added for all remaining messages. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## food-chain

Added typing to `recite()`, as it is unclear what the parameter types should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## forth

Added typing to `evaluate()`, as it is unclear what the parameter type should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## go-counting

Added typing to `Board.__init__()`, `territory()`, and `territories()`. Moved the `WHITE`, `BLACK`, and `NONE` constants from `main.py` to `tests.py`, as the docstrings already specify the exact string values to return and requiring the agent to define these constants is an unnecessary hurdle. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## grade-school

Added typing to `add_student()`, `roster()`, `grade()`, and `added()`, as it is unclear what the parameter types should be and what the methods should return. The `added()` method is particularly ambiguous as the instructions mention tracking duplicate additions but don't specify how. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## grep

Added typing to `grep()`, as it is unclear what the parameter types should be and what the function should return. The `flags` parameter format is particularly ambiguous as an agent might assume it's a list rather than a space-separated string. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## hangman

Added typing to `Hangman.__init__()`, `guess()`, `get_masked_word()`, and `get_status()`, as it is unclear what the parameter types should be and what the methods should return. Clarified that repeated guesses count as wasted attempts, as this behavior is tested but not documented. Fixed ambiguity in the number of allowed failures: changed `remaining_guesses` from starting at 9 to starting at 10, and changed the lose condition from `remaining_guesses < 0` to `remaining_guesses <= 0`, so that the variable name accurately reflects the game state (you lose when you have 0 remaining guesses, not when it goes negative). This makes the logic more intuitive while maintaining the original behavior of allowing 10 total incorrect guesses before losing. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## list-ops

Added typing to all functions (`append()`, `concat()`, `filter()`, `length()`, `map()`, `foldl()`, `foldr()`, `reverse()`), as it is unclear what the parameter types should be and what the functions should return. Added comments to `foldl()` and `foldr()` clarifying the function signature should be `function(acc, el)`, as the instructions mention argument ordering is significant but don't specify what that ordering is.

## ocr-numbers

Added typing to `convert()`, as it is unclear whether the function should should accept a list of strings, or a single string, separated by newlines.

## phone-number

Added typing to `PhoneNumber.__init__()` and `pretty()`. Added the missing `pretty()` method stub, as it is required by the tests but was not present in the `main.py` file. Added type hints and comments for the `number` and `area_code` attributes to clarify the expected class interface, as these are tested but never documented in the instructions. Added a comment showing the expected format `(XXX)-XXX-XXXX` for the `pretty()` method.

## pig-latin

Added typing to `translate()`, as it is unclear what the parameter type should be and what the function should return.

## poker

Added typing to `best_hands()`. Added explicit card format documentation specifying that the rank for ten is `10` (two characters) rather than `T`, since many LLMs are trained on standard poker notation that uses single-character ranks, which could cause confusion. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## pov

Added typing to `Tree.__init__()`, `from_pov()`, and `path_to()`, as it is unclear what the parameter types should be and what the methods should return. Added specific error message documentation to the instructions for `"Tree could not be reoriented"` and `"No path found"`, as the original instructions did not document the required messages. Fixed `tests.py` and `solution.py` so that `path_to()` raises `"No path found"` (not `"Tree could not be reoriented"`) when the source node doesn't exist, as this is more semantically correct for a path-finding operation. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## protein-translation

The exact exception messages are now specified in the instructions. Previously, they were only specified in the tests, thus making the problem impossible to solve for agents.

## proverb

Added the complete function signature with typing for `proverb()`, including the `*items` variadic parameter and `qualifier` keyword argument, as the `main.py` file had no parameters at all. The instructions mention the `qualifier` parameter but don't specify its type or default value.

## react

Added typing to `InputCell.__init__()`, `ComputeCell.__init__()`, `add_callback()`, and `remove_callback()`, as it is unclear what the parameter types should be. The reactive programming paradigm described in the instructions is complex, and type hints help clarify the expected interface.

## rest-api

Added typing to `RestAPI.__init__()`, `get()`, and `post()`, as it is unclear what the parameter types should be and what the methods should return. The payloads are JSON strings, which is not obvious from the instructions alone. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## robot-name

Added the missing `name` property and `reset()` method with type hints. The instructions mention these behaviors but `main.py` had no indication that a `name` property or `reset()` method were needed. Fixed `solution.py` to use a class variable `_used_names` for global name tracking instead of an instance variable, as the tests expect different Robot instances to have different names. Clarified in the instructions that names must be globally unique across all Robot instances.

## scale-generator

Added typing to `Scale.__init__()`, `chromatic()`, and `interval()`, as it is unclear what the parameter types should be and what the methods should return.

## sgf-parsing

Added typing to `SgfTree.__init__()` and `parse()`, as it is unclear what the parameter types should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## simple-linked-list

Added typing to `Node.__init__()`, `Node.value()`, `Node.next()`, `LinkedList.__init__()`, `LinkedList.__len__()`, `LinkedList.head()`, `LinkedList.push()`, `LinkedList.pop()`, and `LinkedList.reversed()`, as it is unclear what the parameter types should be and what the methods should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## transpose

Added typing to `transpose()`, as it is unclear what the parameter type should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## tree-building

Added typing to `Record.__init__()`, `Node.__init__()`, and `BuildTree()`. Fixed the error messages in the provided implementation to match what the tests expect, as the original code had generic messages like `"error!"` and `"something went wrong!"` instead of the specific messages required by the tests. Added comments documenting the required error messages. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## two-bucket

Added typing to `measure()` with a comment clarifying the return tuple format. The instructions explain what three values should be determined but don't specify they should be returned as a tuple, the order of the values, or that the bucket should be the string `"one"` or `"two"`. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## variable-length-quantity

Added typing to `encode()` and `decode()`, as it is unclear what the parameter types should be and what the functions should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## wordy

Added typing to `answer()`, as it is unclear what the parameter type should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## zebra-puzzle

Added typing to `drinks_water()` and `owns_zebra()`, as it is unclear what the functions should return (a nationality string).

## zipper

Added typing to all `Zipper` methods and fixed missing parameters for `set_value()`, `set_left()`, and `set_right()`, as the `main.py` file had these methods with no parameters but the tests call them with arguments. Added a comment clarifying the tree dict structure (with keys `"value"`, `"left"`, `"right"`), as the instructions never explicitly state this format. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.
