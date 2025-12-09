# Instructions

Convert a hexadecimal number, represented as a string (e.g. "10af8c"), to its decimal equivalent using first principles (i.e. no, you may not use built-in or external libraries to accomplish the conversion).

On the web we use hexadecimal to represent colors, e.g. green: 008000, teal: 008080, navy: 000080).

The program should handle invalid hexadecimal strings.

## Exception messages

Sometimes it is necessary to [raise an exception](https://docs.python.org/3/tutorial/errors.html#raising-exceptions). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging. For situations where you know that the error source will be a certain type, you can choose to raise one of the [built in error types](https://docs.python.org/3/library/exceptions.html#base-classes), but should still include a meaningful message.

This particular exercise requires that you use the [raise statement](https://docs.python.org/3/reference/simple_stmts.html#the-raise-statement) to "throw" a `ValueError` when the hexadecimal string is invalid. The tests will only pass if you both `raise` the `exception` and include a message with it.

To raise a `ValueError` with a message, write the message as an argument to the `exception` type:

```python
# when the hexadecimal string contains invalid characters
raise ValueError("Invalid hexadecimal string")
```
