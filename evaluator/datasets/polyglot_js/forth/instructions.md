# Instructions

Implement an evaluator for a very simple subset of Forth.

[Forth][forth]
is a stack-based programming language.
Implement a very basic evaluator for a small subset of Forth.

Your evaluator has to support the following words:

- `+`, `-`, `*`, `/` (integer arithmetic)
- `DUP`, `DROP`, `SWAP`, `OVER` (stack manipulation)

Your evaluator also has to support defining new words using the customary syntax: `: word-name definition ;`.

To keep things simple the only data type you need to support is signed integers of at least 16 bits size.

You should use the following rules for the syntax: a number is a sequence of one or more (ASCII) digits, a word is a sequence of one or more letters, digits, symbols or punctuation that is not a number.
(Forth probably uses slightly different rules, but this is close enough.)

Words are case-insensitive.

[forth]: https://en.wikipedia.org/wiki/Forth_%28programming_language%29

## Exception messages

Sometimes it is necessary to [throw an error](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging.

This particular exercise requires that you use the [throw new Error statement](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw) to "throw" `Error`s for invalid Forth operations. The tests will only pass if you both `throw` the `Error` and include a message with it.

To throw an `Error` with a message, write the message as an argument to the `Error` type:

```js
// if a word definition is not terminated with a semicolon
throw new Error('Unterminated definition');

// if an unknown command is used
throw new Error('Unknown command');

// if trying to redefine a keyword (like numbers or built-in words)
throw new Error('Invalid definition');

// if trying to perform an operation with only one value on the stack
throw new Error('Only one value on the stack');

// if trying to perform an operation on an empty stack
throw new Error('Stack empty');

// if dividing by zero
throw new Error('Division by zero');
```
