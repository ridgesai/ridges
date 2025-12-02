# Instructions

Your job is to translate RNA sequences into proteins.

RNA strands are made up of three-nucleotide sequences called **codons**.
Each codon translates to an **amino acid**.
When joined together, those amino acids make a protein.

In the real world, there are 64 codons, which in turn correspond to 20 amino acids.
However, for this exercise, you’ll only use a few of the possible 64.
They are listed below:

| Codon              | Amino Acid    |
| ------------------ | ------------- |
| AUG                | Methionine    |
| UUU, UUC           | Phenylalanine |
| UUA, UUG           | Leucine       |
| UCU, UCC, UCA, UCG | Serine        |
| UAU, UAC           | Tyrosine      |
| UGU, UGC           | Cysteine      |
| UGG                | Tryptophan    |
| UAA, UAG, UGA      | STOP          |

For example, the RNA string “AUGUUUUCU” has three codons: “AUG”, “UUU” and “UCU”.
These map to Methionine, Phenylalanine, and Serine.

## “STOP” Codons

You’ll note from the table above that there are three **“STOP” codons**.
If you encounter any of these codons, ignore the rest of the sequence — the protein is complete.

For example, “AUGUUUUCUUAAAUG” contains a STOP codon (“UAA”).
Once we reach that point, we stop processing.
We therefore only consider the part before it (i.e. “AUGUUUUCU”), not any further codons after it (i.e. “AUG”).

Learn more about [protein translation on Wikipedia][protein-translation].

[protein-translation]: https://en.wikipedia.org/wiki/Translation_(biology)

# Instructions append

## Exception messages

Sometimes it is necessary to [throw an error](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw). When you do this, you should always include a **meaningful error message** to indicate what the source of the error is. This makes your code more readable and helps significantly with debugging.

This particular exercise requires that you use the [throw new Error statement](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/throw) to "throw" `Error`s if the codon is invalid. The tests will only pass if you both `throw` the `Error` and include a message with it.

To throw an `Error` with a message, write the message as an argument to the `Error` type:

```js
// if the codon is invalid
new Error('Invalid codon');
```
