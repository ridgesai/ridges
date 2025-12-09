# Instructions

Manage robot factory settings.

When a robot comes off the factory floor, it has no name.

The first time you turn on a robot, a random name is generated in the format of two uppercase letters followed by three digits, such as RX837 or BC811.

Every once in a while we need to reset a robot to its factory settings, which means that its name gets wiped.
The next time you ask, that robot will respond with a new random name.

The names must be random: they should not follow a predictable sequence.
Using random names means a risk of collisions.
Every robot ever created should have a unique name.

## Exception messages

Sometimes it is necessary to [throw an error](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging.

This particular exercise requires that you use the [throw new Error statement](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw) to "throw" `Error`s when all possible names have been used. The tests will only pass if you both `throw` the `Error` and include a message with it.

To throw an `Error` with a message, write the message as an argument to the `Error` type:

```js
// if all possible names have been used
throw new Error('Can not generate another name because all the names have been used.');
```
