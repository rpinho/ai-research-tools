# The producer never grades itself

**The rule.** Whoever produced a result never scores it and never sees the sealed target. Not
because the producer lies, but because a search that can see the scorer optimises the scorer.

**Enforced by** sealed selection with declarations frozen before the first result; a separate
exact checker for every certificate, which must also reject deliberately corrupted ones; and
reviewers from a different model family than the author. The same logic applies to writing: a
readability review runs on the exact bytes that will be sent, by a different family than the
writer.

**Where it leaks.** Declarations cannot create filesystem isolation. A real boundary needs the
scorer to run as a separate user or container that owns the labels. Until then this is a
convention with receipts, which is worth less than it looks.
