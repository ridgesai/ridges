# Instructions

Given a string of digits, output all the contiguous substrings of length `n` in that string in the order that they appear.

For example, the string "49142" has the following 3-digit series:

- "491"
- "914"
- "142"

And the following 4-digit series:

- "4914"
- "9142"

And if you ask for a 6-digit series from a 5-digit string, you deserve whatever you get.

Note that these series are only required to occupy _adjacent positions_ in the input;
the digits need not be _numerically consecutive_.

## Exception messages

Sometimes it is necessary to [throw an error](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging.

This particular exercise requires that you use the [throw new Error statement](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw) to "throw" `Error`s for invalid inputs. The tests will only pass if you both `throw` the `Error` and include a message with it.

To throw an `Error` with a message, write the message as an argument to the `Error` type:

```js
// if the series is empty
throw new Error('series cannot be empty');

// if the slice length is negative
throw new Error('slice length cannot be negative');

// if the slice length is zero
throw new Error('slice length cannot be zero');

// if the slice length is greater than the series length
throw new Error('slice length cannot be greater than series length');
```
