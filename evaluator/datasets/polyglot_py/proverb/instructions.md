# Instructions

For want of a horseshoe nail, a kingdom was lost, or so the saying goes.

Given a list of inputs, generate the relevant proverb.
For example, given the list `["nail", "shoe", "horse", "rider", "message", "battle", "kingdom"]`, you will output the full text of this proverbial rhyme:

```text
For want of a nail the shoe was lost.
For want of a shoe the horse was lost.
For want of a horse the rider was lost.
For want of a rider the message was lost.
For want of a message the battle was lost.
For want of a battle the kingdom was lost.
And all for the want of a nail.
```

Note that the list of inputs may vary; your solution should be able to handle lists of arbitrary length and content.
No line of the output text should be a static, unchanging string; all should vary according to the input given.


# Instructions append

In [concept:python/unpacking-and-multiple-assignment](https://github.com/exercism/python/tree/main/concepts/unpacking-and-multiple-assignment), you learned multiple techniques for working with `lists`/`tuples` of arbitrary length as well as function arguments of arbitrary length.
This exercise would be a great place to practice those techniques.

## How this exercise is implemented for Python

The test cases for this track add an additional keyword argument called `qualifier`.
You should use this keyword arguments value to modify the final verse of your Proverb.
