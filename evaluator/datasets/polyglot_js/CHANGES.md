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
