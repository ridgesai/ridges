# Instructions

Your team has decided to use a doubly linked list to represent each train route in the schedule.
Each station along the train's route will be represented by a node in the linked list.

You don't need to worry about arrival and departure times at the stations.
Each station will simply be represented by a number.

Routes can be extended, adding stations to the beginning or end of a route.
They can also be shortened by removing stations from the beginning or the end of a route.

Sometimes a station gets closed down, and in that case the station needs to be removed from the route, even if it is not at the beginning or end of the route.

The size of a route is measured not by how far the train travels, but by how many stations it stops at.

~~~~exercism/note
The linked list is a fundamental data structure in computer science, often used in the implementation of other data structures.
As the name suggests, it is a list of nodes that are linked together.
It is a list of "nodes", where each node links to its neighbor or neighbors.
In a **singly linked list** each node links only to the node that follows it.
In a **doubly linked list** each node links to both the node that comes before, as well as the node that comes after.

If you want to dig deeper into linked lists, check out [this article][intro-linked-list] that explains it using nice drawings.

[intro-linked-list]: https://medium.com/basecs/whats-a-linked-list-anyway-part-1-d8b7e6508b9d
~~~~


# Instructions append

## How this Exercise is Structured in Python

While linked lists can be implemented in a variety of ways with a variety of underlying data structures, we ask here that you implement your linked list in an OOP fashion.

In the stub file, you will see the start of a `Node` class, as well as a `LinkedList` class.
Your `Node` class should keep track of its value, as well as which nodes precede or follow.
Your `push`, `pop`, `shift`, `unshift`, and the special method for `len` should be implemented in the `LinkedList` class.
You might also find it useful to implement a special `iter` method for iteration.

Unlike the core exercise, we will be testing error conditions by calling `pop` and `shift` on empty `LinkedLists`, so you will need to `raise` errors appropriately.

Finally, we would like you to implement `delete` in addition to the methods outlined above.
`delete` will take one argument, which is the value to be removed from the linked list.
If the value appears more than once, only the **first** occurrence should be removed.

<br>

## Exception Messages

Sometimes it is necessary to [raise an exception][raising]. When you do this, you should always include a **meaningful error message** to indicate what the source of the error is.
This makes your code more readable and helps significantly with debugging.
For situations where you know that the error source will be a certain type, you can choose to raise one of the [built in error types][error types], but should still include a meaningful message.

This particular exercise requires that you use the [raise statement][raise] to "throw" a `ValueError` when a node value being `delete()`-ed is not found in the linked list.
Additionally, an `IndexError` should be thrown if there are no nodes left to `pop()`.
The tests will only pass if you both `raise` these `exceptions` and include messages with them.

To raise a `ValueError` with a message, write the message as an argument to the `exception` type:

```python
# When the value passed to `delete()` is not found.
if not found:
    raise ValueError("Value not found")

```

To raise an `IndexError` with a message, write the message as an argument to the `exception` type:

```python
# When pop() is called and there are no nodes left in the linked list
if self.length == 0:
    raise IndexError("List is empty")

```


## Special Methods in Python

The tests for this exercise will also be calling `len()` on your `LinkedList`.
In order for `len()` to work, you will need to create a `__len__` special method.
For details on implementing special or "dunder" methods in Python, see [Python Docs: Basic Object Customization][basic customization] and [Python Docs: object.__len__(self)][__len__].

We also recommend creating a special [`__iter__`][__iter__] method to help with iterating over your linked list.

<br>

[__iter__]: https://docs.python.org/3/reference/datamodel.html#object.__iter__
[__len__]: https://docs.python.org/3/reference/datamodel.html#object.__len__
[basic customization]: https://docs.python.org/3/reference/datamodel.html#basic-customization
[error types]: https://docs.python.org/3/library/exceptions.html#base-classes
[raise]: https://docs.python.org/3/reference/simple_stmts.html#the-raise-statement
[raising]: https://docs.python.org/3/tutorial/errors.html#raising-exceptions
