# Instructions

Given a number, your task is to express it in English words exactly as your friend should say it out loud.
Yaʻqūb expects to use numbers from 0 up to 999,999,999,999.

Examples:

- 0 → zero
- 1 → one
- 12 → twelve
- 123 → one hundred twenty-three
- 1,234 → one thousand two hundred thirty-four

## Exception messages

Sometimes it is necessary to [throw an error](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging.

This particular exercise requires that you use the [throw new Error statement](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw) to "throw" `Error`s for numbers outside the supported range. The tests will only pass if you both `throw` the `Error` and include a message with it.

To throw an `Error` with a message, write the message as an argument to the `Error` type:

```js
// if the number is not between 0 and 999,999,999,999
throw new Error('Number must be between 0 and 999,999,999,999.');
```
