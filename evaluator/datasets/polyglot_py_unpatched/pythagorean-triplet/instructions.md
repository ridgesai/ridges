# Description

A Pythagorean triplet is a set of three natural numbers, {a, b, c}, for which,

```text
a² + b² = c²
```

and such that,

```text
a < b < c
```

For example,

```text
3² + 4² = 5².
```

Given an input integer N, find all Pythagorean triplets for which `a + b + c = N`.

For example, with N = 1000, there is exactly one Pythagorean triplet for which `a + b + c = 1000`: `{200, 375, 425}`.


# Instructions append

*NOTE*: The description above mentions mathematical sets, but also that a Pythagorean Triplet {a, b, c} _must_ be ordered such that a < b < c (ascending order). This makes Python's `set` type unsuited to this exercise, since it is an inherently unordered container. Therefore please return a list of lists instead (i.e. `[[a, b, c]]`). You can generate the triplets themselves in whatever order you would like, as the enclosing list's order will be ignored in the tests.
