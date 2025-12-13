# Instructions

Convert a sequence of digits in one base, representing a number, into a sequence of digits in another base, representing the same number.

~~~~exercism/note
Try to implement the conversion yourself.
Do not use something else to perform the conversion for you.
~~~~

## About [Positional Notation][positional-notation]

In positional notation, a number in base **b** can be understood as a linear combination of powers of **b**.

The number 42, _in base 10_, means:

`(4 × 10¹) + (2 × 10⁰)`

The number 101010, _in base 2_, means:

`(1 × 2⁵) + (0 × 2⁴) + (1 × 2³) + (0 × 2²) + (1 × 2¹) + (0 × 2⁰)`

The number 1120, _in base 3_, means:

`(1 × 3³) + (1 × 3²) + (2 × 3¹) + (0 × 3⁰)`

_Yes. Those three numbers above are exactly the same. Congratulations!_

[positional-notation]: https://en.wikipedia.org/wiki/Positional_notation


# Instructions append

## Exception messages

Sometimes it is necessary to [raise an exception](https://docs.python.org/3/tutorial/errors.html#raising-exceptions). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging. For situations where you know that the error source will be a certain type, you can choose to raise one of the [built in error types](https://docs.python.org/3/library/exceptions.html#base-classes), but should still include a meaningful message.

This particular exercise requires that you use the [raise statement](https://docs.python.org/3/reference/simple_stmts.html#the-raise-statement) to "throw" a `ValueError` for different input and output bases. The tests will only pass if you both `raise` the `exception` and include a meaningful message with it.

To raise a `ValueError` with a message, write the message as an argument to the `exception` type:

```python
# for input.
raise ValueError("input base must be >= 2")

# another example for input.
raise ValueError("all digits must satisfy 0 <= d < input base")

# or, for output.
raise ValueError("output base must be >= 2")
```
