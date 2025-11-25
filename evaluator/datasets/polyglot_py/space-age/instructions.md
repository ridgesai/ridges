# Instructions

Given an age in seconds, calculate how old someone would be on a planet in our Solar System.

One Earth year equals 365.25 Earth days, or 31,557,600 seconds.
If you were told someone was 1,000,000,000 seconds old, their age would be 31.69 Earth-years.

For the other planets, you have to account for their orbital period in Earth Years:

| Planet  | Orbital period in Earth Years |
| ------- | ----------------------------- |
| Mercury | 0.2408467                     |
| Venus   | 0.61519726                    |
| Earth   | 1.0                           |
| Mars    | 1.8808158                     |
| Jupiter | 11.862615                     |
| Saturn  | 29.447498                     |
| Uranus  | 84.016846                     |
| Neptune | 164.79132                     |

~~~~exercism/note
The actual length of one complete orbit of the Earth around the sun is closer to 365.256 days (1 sidereal year).
The Gregorian calendar has, on average, 365.2425 days.
While not entirely accurate, 365.25 is the value used in this exercise.
See [Year on Wikipedia][year] for more ways to measure a year.

[year]: https://en.wikipedia.org/wiki/Year#Summary
~~~~


# Instructions append

For the Python track, this exercise asks you to create a `SpaceAge` _class_ (_[concept:python/classes]()_) that includes methods for all the planets of the solar system.
Methods should follow the naming convention `on_<planet name>`.

Each method should `return` the age (_"on" that planet_) in years, rounded to two decimal places:

```python
#creating an instance with one billion seconds, and calling .on_earth().
>>> SpaceAge(1000000000).on_earth()

#This is one billion seconds on Earth in years
31.69
```

For more information on constructing and using classes, see:

-   [**A First Look at Classes**][first look at classes] from the Python documentation.
-   [**A Word About names and Objects**][names and objects] from the Python documentation.
-   [**Objects, values, and types**][objects, values and types] in the Python data model documentation.
-   [**What is a Class?**][what is a class] from Trey Hunners Python Morsels website.

[first look at classes]: https://docs.python.org/3/tutorial/classes.html#a-first-look-at-classes
[names and objects]: https://docs.python.org/3/tutorial/classes.html#a-word-about-names-and-objects
[objects, values and types]: https://docs.python.org/3/reference/datamodel.html#objects-values-and-types
[what is a class]: https://www.pythonmorsels.com/what-is-a-class/
