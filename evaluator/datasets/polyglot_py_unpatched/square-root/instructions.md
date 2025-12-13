# Instructions

Your task is to calculate the square root of a given number.

- Try to avoid using the pre-existing math libraries of your language.
- As input you'll be given a positive whole number, i.e. 1, 2, 3, 4…
- You are only required to handle cases where the result is a positive whole number.

Some potential approaches:

- Linear or binary search for a number that gives the input number when squared.
- Successive approximation using Newton's or Heron's method.
- Calculating one digit at a time or one bit at a time.

You can check out the Wikipedia pages on [integer square root][integer-square-root] and [methods of computing square roots][computing-square-roots] to help with choosing a method of calculation.

[integer-square-root]: https://en.wikipedia.org/wiki/Integer_square_root
[computing-square-roots]: https://en.wikipedia.org/wiki/Methods_of_computing_square_roots


# Instructions append

## How this Exercise is Structured in Python


Python offers a wealth of mathematical functions in the form of the [math module][math-module] and built-ins such as the exponentiation operator `**`, [`pow()`][pow] and [`sum()`][sum].
However, we'd like you to consider the challenge of solving this exercise without those built-ins or modules.

While there is a mathematical formula that will find the square root of _any_ number, we have gone the route of having only [natural numbers][nautral-number] (positive integers) as solutions.  
It is possible to compute the square root of any natural number using only natural numbers in the computation.


[math-module]: https://docs.python.org/3/library/math.html
[pow]: https://docs.python.org/3/library/functions.html#pow
[sum]: https://docs.python.org/3/library/functions.html#sum
[nautral-number]: https://en.wikipedia.org/wiki/Natural_number

