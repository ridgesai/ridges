# Instructions

Implement various kinds of error handling and resource management.

An important point of programming is how to handle errors and close resources even if errors occur.

This exercise requires you to handle various errors.
Because error handling is rather programming language specific you'll have to refer to the tests for your track to see what's exactly required.


# Hints

For the `filelike_objects_are_closed_on_exception` function, the `filelike_object`
will be an instance of a custom `FileLike` class defined in the test suite. This
class implements the following methods:
- `open` and `close`, for explicit opening and closing.
- `__enter__` and `__exit__`, for implicit opening and closing.
- `do_something`, which may or may not throw an `Exception`.
