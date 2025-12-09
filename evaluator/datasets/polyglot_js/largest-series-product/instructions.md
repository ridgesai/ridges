# Instructions

Your task is to look for patterns in the long sequence of digits in the encrypted signal.

The technique you're going to use here is called the largest series product.

Let's define a few terms, first.

- **input**: the sequence of digits that you need to analyze
- **series**: a sequence of adjacent digits (those that are next to each other) that is contained within the input
- **span**: how many digits long each series is
- **product**: what you get when you multiply numbers together

Let's work through an example, with the input `"63915"`.

- To form a series, take adjacent digits in the original input.
- If you are working with a span of `3`, there will be three possible series:
  - `"639"`
  - `"391"`
  - `"915"`
- Then we need to calculate the product of each series:
  - The product of the series `"639"` is 162 (`6 × 3 × 9 = 162`)
  - The product of the series `"391"` is 27 (`3 × 9 × 1 = 27`)
  - The product of the series `"915"` is 45 (`9 × 1 × 5 = 45`)
- 162 is bigger than both 27 and 45, so the largest series product of `"63915"` is from the series `"639"`.
  So the answer is **162**.

## Exception messages

Sometimes it is necessary to [throw an error](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging.

This particular exercise requires that you use the [throw new Error statement](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw) to "throw" `Error`s for invalid inputs. The tests will only pass if you both `throw` the `Error` and include a message with it.

To throw an `Error` with a message, write the message as an argument to the `Error` type:

```js
// if the span is greater than the string length
throw new Error('span must not exceed string length');

// if the span is negative
throw new Error('span must not be negative');

// if the input contains non-digit characters
throw new Error('digits input must only contain digits');
```
