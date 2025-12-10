# Changes to the Polyglot dataset

The Ridges team has made some changes to the original Polyglot problem set, which was sourced originally from [this repository](https://github.com/exercism/javascript/tree/main/exercises/practice). The reason for these changes were because some of the questions are believed (by us) to be practically unsolvable due to errors in the problem statement or tests that do not correlate exactly with the instructions. All changes made to the problem set are documented here for transparency.

<br>
<br>

## accumulate

Added parameters to `accumulate()`, as it is unclear what order the parameters should be in.

## acronym

Added typing to `parse()`, as it is unclear what the parameter type should be and what the function should return.

## affine-cipher

Added typing to `encode()` and `decode()`, as it is unclear what types the parameters should be and what the functions should return. Added error message documentation specifying that `"a and m must be coprime."` should be thrown when necessary. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## all-your-base

Added typing to `convert()`, as it is unclear what order the parameters should be in.
The exact exception messages are now specified in the instructions. Previously, they were only specified in the tests, thus making the problem impossible to solve for agents.

## allergies

Added typing and parameters, as it is unclear what should be returned.

## alphametics

Fixed return type from `number[]` to `Object`, as the tests expect an object mapping letters to digits, not an array. Added clarification to the instructions about the expected return format (object mapping letters to digits, or `null` if no solution exists).

## anagram

Added parameters and typing to `findAnagrams()`, as it is unclear what the parameter types should be and what the function should return.

## armstrong-numbers

Added parameter and typing to `isArmstrongNumber()`, as it is unclear what the parameter type should be and what the function should return.

## atbash-cipher

Added typing to `encode()` and `decode()`, as it is unclear what the parameter types should be and what the functions should return.

## bank-account

Added typing to `deposit()`, `withdraw()`, and `balance`, as it is unclear what the parameter types should be and what the methods should return.

## binary

Added typing to `constructor()` and `toDecimal()`, as it is unclear what the parameter type should be and what the method should return (number or null for invalid inputs).

## binary-search

Added typing to `find()`, as it is unclear what the parameter types should be and what the function should return. Added error message documentation specifying that `"Value not in array"` should be thrown when the value is not found.

## binary-search-tree

Added typing to `constructor()`, `data`, `left`, `right`, `insert()`, and `each()`, as it is unclear what the parameter types should be and what the methods should return.

## bob

Added typing to `hey()`, as it is unclear what the parameter type should be and what the function should return.

## beer-song

Added typing to `recite()`, as it is unclear whether the function should return a list of strings, or a single string, separated by newlines.

## book-store

Added typing to `total()`, as it is unclear what format of input the function should expect. Changed the return type to `int` and added a comment (`in cents`) to indicate the format of the output, as the instructions do not specify whether the solution should return an answer as a decimal number of dollars, or an integer number of cents. In fact, the instructions lean towards decimal dollars, whereas the tests use integer cents.

## bottle-song

Added typing to `recite()`, as it is unclear whether the function should return a list of strings, or a single string, separated by newlines.

## bowling

Added typing to `roll()` and `score()`, as it is unclear what the parameter type should be for `roll()` and what the method should return. Added error message documentation specifying the exact error messages that should be thrown for invalid rolls and scoring operations.

## change

Added typing to `calculate()`, as it is unclear what the parameter types should be and what the method should return. Added error message documentation specifying the exact error messages that should be thrown for invalid totals and impossible currency representations.

## circular-buffer

Added typing to `constructor()`, `write()`, `read()`, `forceWrite()`, and `clear()`, as it is unclear what the parameter types should be and what the methods should return.

## clock

Added typing to `constructor()`, `toString()`, `plus()`, `minus()`, and `equals()`, as it is unclear what the parameter types should be and what the methods should return.

## collatz-conjecture

Added typing to `steps()`, as it is unclear what the parameter type should be and what the function should return. Added error message documentation specifying that `"Only positive integers are allowed"` should be thrown for non-positive inputs.

## complex-numbers

Added typing to `constructor()`, `real`, `imag`, `add()`, `sub()`, `div()`, `mul()`, `abs`, `conj`, and `exp`, as it is unclear what the parameter types should be and what the methods/getters should return.

## connect

Added typing to the Board `constructor()` and `get_winner()`, as it is unclear what type the board representation should be and what format the winner should be returned in. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## crypto-square

Added typing to `constructor()` and `ciphertext`, as it is unclear what the parameter type should be and what the getter should return.

## custom-set

Added typing to `constructor()`, `empty()`, `contains()`, `add()`, `subset()`, `disjoint()`, `eql()`, `union()`, `intersection()`, and `difference()`, as it is unclear what the parameter types should be and what the methods should return.

## darts

Added typing to `score()`, as it is unclear what the parameter types should be and what the function should return.

## diamond

Added typing to `rows()`, as it is unclear what the parameter type should be and what the function should return.

## difference-of-squares

Added typing to `constructor()`, `sumOfSquares`, `squareOfSum`, and `difference`, as it is unclear what the parameter type should be and what the getters should return.

## diffie-hellman

Added typing to `constructor()`, `getPublicKey()`, `getSecret()`, and `getPrivateKey()`, as it is unclear what the parameter types should be and what the methods should return.

## dnd-character

Added typing to `abilityModifier()`, `rollAbility()`, `strength`, `dexterity`, `constitution`, `intelligence`, `wisdom`, `charisma`, and `hitpoints`, as it is unclear what the parameter types should be and what the function/getters should return. Added error message documentation specifying the exact error messages that should be thrown for invalid ability scores (less than 3 or greater than 18).

## dominoes

Added typing to `chain()`, as it is unclear what the parameter types should be and what the function should return.

## eliuds-eggs

Added typing to `eggCount()`, as it is unclear what the parameter type should be and what the function should return.

## etl

Added typing to `transform()`, as it is unclear what the parameter type should be and what the function should return.

## flatten-array

Added typing to `flatten()`, as it is unclear what the parameter type should be and what the function should return.

## flower-field

Added typing to `annotate()`, as it is unclear what the parameter type should be and what the function should return.

## food-chain

Added typing to `verse()` and `verses()`, as it is unclear what the parameter types should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## forth

Added typing to `evaluate()` and `stack()`, as it is unclear what the parameter type should be and what the function should return. Added error message documentation specifying the exact error messages that should be thrown for various invalid Forth operations (unterminated definitions, unknown commands, invalid definitions, stack errors, and division by zero). There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## game-of-life

Added typing to `GameOfLife.constructor()` and `state()`, as it is unclear what the parameter type should be and what the method should return.

## gigasecond

Added typing to `gigasecond()`, as it is unclear what the parameter type should be and what the function should return.

## go-counting

Added typing to `GoCounting.constructor()`, `getTerritory()`, and `getTerritories()`. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## grade-school

Added typing to `add()`, `roster()`, and `grade()`, as it is unclear what the parameter types should be and what the methods should return

## grains

Added typing to `square()` and `total()`, as it is unclear what the parameter type should be and what the functions should return. Added error message documentation specifying that `"square must be between 1 and 64"` should be thrown for invalid square numbers.

## grep

No changes. There are already JSDocs in the JavaScript file.

## hamming

Added typing to `compute()`, as it is unclear what the parameter types should be and what the function should return. Added error message documentation specifying that `"strands must be of equal length"` should be thrown when comparing strands of different lengths.

## hello-world

Added typing to `hello()`, as it is unclear what the function should return.

## hexadecimal

Added typing to `toDecimal()`, as it is unclear what the parameter type should be and what the function should return.

## high-scores

Added typing to `constructor()`, `scores`, `latest`, `personalBest`, and `personalTopThree`, as it is unclear what the parameter type should be and what the getters should return.

## house

Added typing to `verse()` and `verses()`, as it is unclear what the parameter types should be and what the methods should return.

## isbn-verifier

Added typing to `isValid()`, as it is unclear what the parameter type should be and what the function should return.

## isogram

Added typing to `isIsogram()`, as it is unclear what the parameter type should be and what the function should return.

## killer-sudoku-helper

Added typing to `combinations()`, as it is unclear what the parameter types should be and what the function should return.

## kindergarten-garden

Added typing to `Garden.constructor()` and `plants()`, as it is unclear what the parameter types should be and what the methods should return.

## knapsack

Added typing to `knapsack()`, as it is unclear what the parameter types should be and what the function should return.

## largest-series-product

Added typing to `largestProduct()`, as it is unclear what the parameter types should be and what the function should return. Added error message documentation specifying the exact error messages that should be thrown for invalid spans (exceeding string length, negative, or non-digit input).

## leap

Added typing to `isLeap()`, as it is unclear what the parameter type should be and what the function should return.

## ledger

Added typing to `createEntry()` and `formatEntries()`, as it is unclear what the parameter types should be and what the functions should return.

## linked-list

Added typing to `push()`, `pop()`, `shift()`, `unshift()`, `delete()`, and `count()`, as it is unclear what the parameter types should be and what the methods should return.

## list-ops

Added typing to all functions (`append()`, `concat()`, `filter()`, `length()`, `map()`, `foldl()`, `foldr()`, `reverse()`), as it is unclear what the parameter types should be and what the functions should return. Added comments to `foldl()` and `foldr()` clarifying the function signature should be `function(acc, el)`, as the instructions mention argument ordering is significant but don't specify what that ordering is.

## luhn

Added typing to `valid()`, as it is unclear what the parameter type should be and what the function should return.

## markdown

Added typing to `parse()`, as it is unclear what the parameter type should be and what the function should return.

## matching-brackets

Added typing to `isPaired()`, as it is unclear what the parameter type should be and what the function should return.

## matrix

Added typing to `Matrix.constructor()`, `rows`, and `columns`, as it is unclear what the parameter type should be and what the getters should return.

## micro-blog

Added typing to `truncate()`, as it is unclear what the parameter type should be and what the function should return.

## meetup

Added typing to `meetup()`, as it is unclear what the parameter types should be and what the function should return.

## minesweeper

Added typing to `annotate()`, as it is unclear what the parameter type should be and what the function should return.

## nth-prime

Added typing to `prime()`, as it is unclear what the parameter type should be and what the function should return. Added error message documentation specifying that `"there is no zeroth prime"` should be thrown when asking for the 0th prime.

## nucleotide-count

Added typing to `countNucleotides()`, as it is unclear what the parameter type should be and what the function should return.

## ocr-numbers

Added typing to `convert()`, as it is unclear whether the function should should accept a list of strings, or a single string, separated by newlines.

## octal

Added typing to `Octal.constructor()` and `toDecimal()`, as it is unclear what the parameter type should be and what the method should return.

## palindrome-products

Added typing to `Palindromes.generate()`, as it is unclear what the parameter types should be and what the method should return.

## pangram

Added typing to `isPangram()`, as it is unclear what the parameter type should be and what the function should return.

## parallel-letter-frequency

Added typing to `parallelLetterFrequency()`, as it is unclear what the parameter type should be and what the function should return.

## pascals-triangle

Added typing to `rows()`, as it is unclear what the parameter type should be and what the function should return.

## perfect-numbers

Added typing to `classify()`, as it is unclear what the parameter type should be and what the function should return. Added error message documentation specifying that `"Classification is only possible for natural numbers."` should be thrown when the number is not a natural number (less than or equal to 0).

## phone-number

Added typing to `clean()`. Added a comment showing the expected format `XXXXXXXXXX` for the `clean()` method. Added error message documentation specifying the exact error messages that should be thrown for various invalid phone number formats (letters, punctuations, wrong number of digits, invalid area/exchange codes).

## pig-latin

Added typing to `translate()`, as it is unclear what the parameter type should be and what the function should return.

## point-mutations

Added typing to `DNA.constructor()` and `hammingDistance()`, as it is unclear what the parameter types should be and what the method should return.

## poker

Added typing to `best_hands()`. Added explicit card format documentation specifying that the rank for ten is `10` (two characters) rather than `T`, since many LLMs are trained on standard poker notation that uses single-character ranks, which could cause confusion. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## protein-translation

The exact exception messages are now specified in the instructions. Previously, they were only specified in the tests, thus making the problem impossible to solve for agents.

## prime-factors

Added typing to `primeFactors()`, as it is unclear what the parameter type should be and what the function should return.

## promises

Added typing to `promisify()`, `all()`, `allSettled()`, `race()`, and `any()`, as it is unclear what the parameter types should be and what the functions should return.

## proverb

Added the complete function signature with typing for `proverb()`, including the `...args` rest parameter, as the `main.js` file had no parameters at all.

## pythagorean-triplet

Added typing to `triplets()`, `Triplet.constructor()`, and `toArray()`, as it is unclear what the parameter types should be and what the methods should return.

## queen-attack

Added typing to `QueenAttack.constructor()`, `toString()`, and `canAttack`, as it is unclear what the parameter types should be and what the methods should return. The exact exception messages are now specified in the instructions. Previously, they were only specified in the tests, thus making the problem impossible to solve for agents.

## rail-fence-cipher

Added typing to `encode()` and `decode()`, as it is unclear what the parameter types should be and what the functions should return.

## raindrops

Added typing to `convert()`, as it is unclear what the parameter type should be and what the function should return.

## rational-numbers

Added typing to `Rational.constructor()` and all methods (`add()`, `sub()`, `mul()`, `div()`, `abs()`, `exprational()`, and `expreal()`), as it is unclear what the parameter types should be and what the methods should return.

## react

Added typing to `InputCell.constructor()`, `ComputeCell.constructor()`, `CallbackCell.constructor()`, `add_callback()`, and `remove_callback()`, as it is unclear what the parameter types should be. The reactive programming paradigm described in the instructions is complex, and type hints help clarify the expected interface.

## rectangles

Added typing to `count()`, as it is unclear what the parameter type should be and what the function should return.

## relative-distance

Added typing to `degreesOfSeparation()`, as it is unclear what the parameter types should be and what the function should return.

## resistor-color

Added typing to `colorCode()`, as it is unclear what the parameter type should be and what the function should return.

## resistor-color-duo

Added typing to `decodedValue()`, as it is unclear what the parameter type should be and what the function should return.

## resistor-color-trio

Added typing to `ResistorColorTrio.constructor()` and `label()`, as it is unclear what the parameter types should be and what the methods should return.

## rest-api

Added typing to `RestAPI.constructor()`, `get()`, and `post()`, as it is unclear what the parameter types should be and what the methods should return. The payloads are JSON strings, which is not obvious from the instructions alone. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## reverse-string

Added typing to `reverseString()`, as it is unclear what the parameter type should be and what the function should return.

## rna-transcription

Added typing to `toRna()`, as it is unclear what the parameter type should be and what the function should return.

## robot-name

Added the missing `name` property and `reset()` method with type hints. The instructions mention these behaviors but `main.js` had no indication that a `name` property or `reset()` method were needed.

## robot-simulator

Added typing to `Robot.bearing`, `Robot.coordinates`, `Robot.place()`, and `Robot.evaluate()`, as it is unclear what the parameter types should be and what the methods should return.

## roman-numerals

Added typing to `toRoman()`, as it is unclear what the parameter type should be and what the function should return.

## rotational-cipher

Added typing to `rotate()`, as it is unclear what the parameter types should be and what the function should return.

## run-length-encoding

Added typing to `encode()` and `decode()`, as it is unclear what the parameter types should be and what the functions should return.

## saddle-points

Added typing to `saddlePoints()`, as it is unclear what the parameter type should be and what the function should return.

## satellite

Added typing to `treeFromTraversals()`, as it is unclear what the parameter types should be and what the function should return. Added error message documentation specifying the exact error messages that should be thrown for invalid tree traversals (different lengths, duplicate items, or different elements).

## say

Added typing to `say()`, as it is unclear what the parameter type should be and what the function should return. Added error message documentation specifying that `"Number must be between 0 and 999,999,999,999."` should be thrown for numbers outside the supported range.

## scrabble-score

Added typing to `score()`, as it is unclear what the parameter type should be and what the function should return.

## secret-handshake

Added typing to `commands()`, as it is unclear what the parameter type should be and what the function should return.

## series

Added typing to `Series.constructor()` and `slices()`, as it is unclear what the parameter types should be and what the methods should return. Added error message documentation specifying the exact error messages that should be thrown for invalid inputs (empty series, negative slice length, zero slice length, or slice length exceeding series length).

## sieve

Added typing to `primes()`, as it is unclear what the parameter type should be and what the function should return.

## simple-cipher

Added typing to `Cipher.constructor()`, `encode()`, `decode()`, and `key` getter, as it is unclear what the parameter types should be and what the methods should return.

## scale-generator

Added typing to `Scale.constructor()`, `chromatic()`, and `interval()`, as it is unclear what the parameter types should be and what the methods should return.

## simple-linked-list

Added typing to `Element.constructor()`, `Element.value()`, `Element.next()`, `List.constructor()`, `List.length`, `List.head`, `List.add()`, and `List.reverse()`, as it is unclear what the parameter types should be and what the methods should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## space-age

Added typing to `age()`, as it is unclear what the parameter types should be and what the function should return. Added error message documentation specifying that `"not a planet"` should be thrown for invalid planet names.

## spiral-matrix

Added typing to `spiralMatrix()`, as it is unclear what the parameter type should be and what the function should return.

## square-root

Added typing to `squareRoot()`, as it is unclear what the parameter type should be and what the function should return.

## state-of-tic-tac-toe

Added typing to `gamestate()`, as it is unclear what the parameter type should be and what the function should return. Added error message documentation specifying the exact error messages that should be thrown for invalid board states (wrong turn order or game continuing after completion).

## strain

Added typing to `keep()` and `discard()`, as it is unclear what the parameter types should be and what the functions should return.

## sublist

Added typing to `List.constructor()` and `compare()`, as it is unclear what the parameter types should be and what the methods should return.

## sum-of-multiples

Added typing and parameters to the sum function, since agents can't guess the right order of arguments expected in the tests. Added allowed assumptions to the instructions.

## tournament

Added typing to `tournamentTally()`, as it is unclear what the parameter type should be and what the function should return.

## transpose

Added typing to `transpose()`, as it is unclear what the parameter type should be and what the function should return. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## triangle

Added typing to `Triangle.constructor()`, `isEquilateral`, `isIsosceles`, and `isScalene`, as it is unclear what the parameter types should be and what the getters should return.

## trinary

Added typing to `Trinary.constructor()` and `toDecimal()`, as it is unclear what the parameter types should be and what the methods should return.

## twelve-days

Added typing to `recite()`, as it is unclear what the parameter types should be and what the function should return.

## two-bucket

Added typing to `solve()`. The instructions explain what three values should be determined but don't specify they should be returned as an object. Added error message documentation specifying the exact error messages that should be thrown for invalid bucket configurations (goal too large, goal not a multiple of GCD). There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## two-fer

Added typing to `twoFer()`, as it is unclear what the parameter type should be and what the function should return.

## variable-length-quantity

Added typing to `encode()` and `decode()`, as it is unclear what the parameter types should be and what the functions should return. Added error message documentation specifying that `"Incomplete sequence"` should be thrown for incomplete VLQ sequences. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## word-count

Added typing to `countWords()`, as it is unclear what the parameter type should be and what the function should return.

## word-search

Added typing to `WordSearch.constructor()` and `find()`, as it is unclear what the parameter types should be and what the methods should return.

## wordy

Added typing to `answer()`, as it is unclear what the parameter type should be and what the function should return. Added error message documentation specifying the exact error messages that should be thrown for invalid word problems (unknown operations and syntax errors). There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.

## yacht

Added typing to `score()`, as it is unclear what the parameter types should be and what the function should return.

## zebra-puzzle

Added typing to `drinks_water()` and `owns_zebra()`, as it is unclear what the functions should return (a nationality string).

## zipper

Added typing to all `Zipper` methods. There are some links that are impossible for the agent to follow. This will be resolved in a future version of our sandbox, where we provide restricted Internet access.
