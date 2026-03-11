# Caesar Cipher

Implement the classic Caesar cipher encryption and decryption.

The Caesar cipher is one of the earliest known and simplest ciphers. It is a type of substitution cipher in which each letter in the plaintext is shifted a certain number of places down the alphabet. For example, with a shift of 1, A would be replaced by B, B would become C, and so on.

## Instructions

Implement two functions:

1. `encrypt(plaintext, shift)` - Encrypts the plaintext using the Caesar cipher with the given shift
2. `decrypt(ciphertext, shift)` - Decrypts the ciphertext using the Caesar cipher with the given shift

## Rules

- Only letters (a-z, A-Z) should be shifted
- Numbers, punctuation, and spaces should remain unchanged
- The shift value can be any integer (positive or negative)
- When shifting, wrap around the alphabet (Z+1 = A, A-1 = Z)
- Preserve the case of letters (uppercase remains uppercase, lowercase remains lowercase)

## Examples

- `encrypt(Hello, World!, 3)` should return `Khoor, Zruog!`
- `decrypt(Khoor, Zruog!, 3)` should return `Hello, World!`
- `encrypt(abc, 1)` should return `bcd`
- `decrypt(bcd, 1)` should return `abc`
- `encrypt(xyz, 3)` should return `abc` (wrapping)
- `decrypt(abc, 3)` should return `xyz` (wrapping)

Run the tests to verify your implementation, then submit your solution.
