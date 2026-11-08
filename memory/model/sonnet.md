# Model memory: sonnet

How this model tends to go wrong, and what helps.

- On wide tickets (many files, fix not yet decided) this model wanders: it keeps reading and re-editing across the tree and spends far more tool calls than the change warrants. It does its best work when the ticket pins the fix to one or two files and the acceptance check is a literal string that can be grepped for.
