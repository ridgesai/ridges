# Instructions

A circular buffer, cyclic buffer or ring buffer is a data structure that uses a single, fixed-size buffer as if it were connected end-to-end.

A circular buffer first starts empty and of some predefined length.
For example, this is a 7-element buffer:

```text
[ ][ ][ ][ ][ ][ ][ ]
```

Assume that a 1 is written into the middle of the buffer (exact starting location does not matter in a circular buffer):

```text
[ ][ ][ ][1][ ][ ][ ]
```

Then assume that two more elements are added — 2 & 3 — which get appended after the 1:

```text
[ ][ ][ ][1][2][3][ ]
```

If two elements are then removed from the buffer, the oldest values inside the buffer are removed.
The two elements removed, in this case, are 1 & 2, leaving the buffer with just a 3:

```text
[ ][ ][ ][ ][ ][3][ ]
```

If the buffer has 7 elements then it is completely full:

```text
[5][6][7][8][9][3][4]
```

When the buffer is full an error will be raised, alerting the client that further writes are blocked until a slot becomes free.

When the buffer is full, the client can opt to overwrite the oldest data with a forced write.
In this case, two more elements — A & B — are added and they overwrite the 3 & 4:

```text
[5][6][7][8][9][A][B]
```

3 & 4 have been replaced by A & B making 5 now the oldest data in the buffer.
Finally, if two elements are removed then what would be returned is 5 & 6 yielding the buffer:

```text
[ ][ ][7][8][9][A][B]
```

Because there is space available, if the client again uses overwrite to store C & D then the space where 5 & 6 were stored previously will be used not the location of 7 & 8.
7 is still the oldest element and the buffer is once again full.

```text
[C][D][7][8][9][A][B]
```


# Instructions append

## Customizing and Raising Exceptions

Sometimes it is necessary to both [customize](https://docs.python.org/3/tutorial/errors.html#user-defined-exceptions) and [`raise`](https://docs.python.org/3/tutorial/errors.html#raising-exceptions) exceptions in your code. When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging. 

Custom exceptions can be created through new exception classes (see [`classes`](https://docs.python.org/3/tutorial/classes.html#tut-classes) for more detail.) that are typically subclasses of [`Exception`](https://docs.python.org/3/library/exceptions.html#Exception).

For situations where you know the error source will be a derivative of a certain exception type, you can choose to inherit from one of the [`built in error types`](https://docs.python.org/3/library/exceptions.html#base-classes) under the _Exception_ class. When raising the error, you should still include a meaningful message.

This particular exercise requires that you create two _custom exceptions_.  One exception to be [raised](https://docs.python.org/3/reference/simple_stmts.html#the-raise-statement)/"thrown" when your circular buffer is **full**, and one for when it is **empty**. The tests will only pass if you customize appropriate exceptions, `raise` those exceptions, and include appropriate error messages.

To customize a `built-in exception`, create a `class` that inherits from that exception. When raising the custom exception with a message, write the message as an argument to the `exception` type:

```python
# subclassing the built-in BufferError to create BufferFullException
class BufferFullException(BufferError):
    """Exception raised when CircularBuffer is full.

    message: explanation of the error.

    """
    def __init__(self, message):
        self.message = message

        
# raising a BufferFullException
raise BufferFullException("Circular buffer is full")
```
