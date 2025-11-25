# Instructions

Your task is to convert a number into its corresponding raindrop sounds.

If a given number:

- is divisible by 3, add "Pling" to the result.
- is divisible by 5, add "Plang" to the result.
- is divisible by 7, add "Plong" to the result.
- **is not** divisible by 3, 5, or 7, the result should be the number as a string.

## Examples

- 28 is divisible by 7, but not 3 or 5, so the result would be `"Plong"`.
- 30 is divisible by 3 and 5, but not 7, so the result would be `"PlingPlang"`.
- 34 is not divisible by 3, 5, or 7, so the result would be `"34"`.

~~~~exercism/note
A common way to test if one number is evenly divisible by another is to compare the [remainder][remainder] or [modulus][modulo] to zero.
Most languages provide operators or functions for one (or both) of these.

[remainder]: https://exercism.org/docs/programming/operators/remainder
[modulo]: https://en.wikipedia.org/wiki/Modulo_operation
~~~~


# Instructions append

## How this Exercise is Structured in Python

This exercise is best solved with Python's `%` ([modulo][modulo]) operator, which returns the remainder of positive integer division.
It has a method equivalent, `operator.mod()` in the [operator module][operator-mod].


Python also offers additional 'remainder' methods in the [math module][math-module].
[`math.fmod()`][fmod] behaves like `%`, but operates on floats.
[`math.remainder()`][remainder] implements a "step closest to zero" algorithm for the remainder of division.
While we encourage you to get familiar with these methods, neither of these will exactly match the result of `%`, and are not recommended for use with this exercise.

The built-in function [`divmod()`][divmod] will also give a remainder than matches `%` if used with two positive integers, but returns a `tuple` that needs to be unpacked.

[divmod]: https://docs.python.org/3/library/functions.html#divmod
[fmod]: https://docs.python.org/3/library/math.html#math.fmod
[math-module]: https://docs.python.org/3/library/math.html
[modulo]: https://www.programiz.com/python-programming/operators#arithmetic
[operator-mod]: https://docs.python.org/3/library/operator.html#operator.mod
[remainder]: https://docs.python.org/3/library/math.html#math.remainder
