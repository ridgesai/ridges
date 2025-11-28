# Changes to the Polyglot dataset

The Ridges team has made some changes to the original Polyglot problem set, which was sourced originally from [this repository](https://github.com/Aider-AI/polyglot-benchmark/tree/main/javascript/exercises/practice). The reason for these changes were because some of the questions are believed (by us) to be practically unsolvable due to errors in the problem statement or tests that do not correlate exactly with the instructions. All changes made to the problem set are documented here for transparency.

<br>
<br>

## affine-cipher

Added typing to `encode()` and `decode()`, as it is unclear what types the parameters should be and what the functions should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## beer-song

Added typing to `recite()`, as it is unclear whether the function should return a list of strings, or a single string, seperated by newlines.

## book-store

Added typing to `total()`, as it is unclear what format of input the function should expect. Changed the return type to `int` and added a comment (`in cents`) to indicate the format of the output, as the instructions do not specify whether the solution should return an answer as a decimal number of dollars, or an integer number of cents. In fact, the instructions lean towards decimal dollars, whereas the tests use integer cents.

## bottle-song

Added typing to `recite()`, as it is unclear whether the function should return a list of strings, or a single string, seperated by newlines.

## bowling

No changes. The instructions provide enough typing information, as such, it is not necessary to include it in the JavaScript file.

## connect

Added typing to the Board `constructor()` and `get_winner()`, as it is unclear what type the board representation should be and what format the winner should be returned in. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## dominoes

Added typing to `chain()`.

## food-chain

Added typing to `verse()` and `verses()`, as it is unclear what the parameter types should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## forth

Added typing to `evaluate()` and `stack()`, as it is unclear what the parameter type should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## go-counting

Added typing to `GoCounting.constructor()`, `getTerritory()`, and `getTerritories()`. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## grade-school

Added typing to `add()`, `roster()`, and `grade()`, as it is unclear what the parameter types should be and what the methods should return

## grep

No changes. There are already JSDocs in the JavaScript file.

## list-ops

Added typing to all functions (`append()`, `concat()`, `filter()`, `length()`, `map()`, `foldl()`, `foldr()`, `reverse()`), as it is unclear what the parameter types should be and what the functions should return. Added comments to `foldl()` and `foldr()` clarifying the function signature should be `function(acc, el)`, as the instructions mention argument ordering is significant but don't specify what that ordering is.

## phone-number

Added typing to `clean()`. Added a comment showing the expected format `XXXXXXXXXX` for the `clean()` method.

## pig-latin

Added typing to `translate()`, as it is unclear what the parameter type should be and what the function should return.

## poker

Added typing to `best_hands()`. Added explicit card format documentation specifying that the rank for ten is `10` (two characters) rather than `T`, since many LLMs are trained on standard poker notation that uses single-character ranks, which could cause confusion. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## proverb

Added the complete function signature with typing for `proverb()`, including the `...args` rest parameter, as the `main.js` file had no parameters at all

## react

Added typing to `InputCell.constructor()`, `ComputeCell.constructor()`, `CallbackCell.constructor()`, `add_callback()`, and `remove_callback()`, as it is unclear what the parameter types should be. The reactive programming paradigm described in the instructions is complex, and type hints help clarify the expected interface.

## rest-api

Added typing to `RestAPI.constructor()`, `get()`, and `post()`, as it is unclear what the parameter types should be and what the methods should return. The payloads are JSON strings, which is not obvious from the instructions alone. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## robot-name

Added the missing `name` property and `reset()` method with type hints. The instructions mention these behaviors but `main.js` had no indication that a `name` property or `reset()` method were needed.
