# Instructions

Calculate the number of grains of wheat on a chessboard.

A chessboard has 64 squares.
Square 1 has one grain, square 2 has two grains, square 3 has four grains, and so on, doubling each time.

Write code that calculates:

- the number of grains on a given square
- the total number of grains on the chessboard

## Exception messages

Sometimes it is necessary to [throw an error](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging.

This particular exercise requires that you use the [throw new Error statement](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw) to "throw" `Error`s for invalid square numbers. The tests will only pass if you both `throw` the `Error` and include a message with it.

To throw an `Error` with a message, write the message as an argument to the `Error` type:

```js
// if the square number is not between 1 and 64
throw new Error('square must be between 1 and 64');
```
