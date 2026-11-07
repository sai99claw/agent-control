# Model memory: opus

How this model tends to go wrong, and what helps.

- Before handing in, check the working tree: this model applies the patch, runs the gates, and forgets to commit, with no symptom until the hand-in is rejected (#486, #489).
- To undo a mutation check in a file that also holds uncommitted work, restore from a copy saved before the mutation (and verify the hash) or commit first. `git checkout -- file` goes back to HEAD and silently drops the uncommitted implementation too (#30, #45, #63).
- Overturning an upstream instruction needs more evidence than following it. A number used as a premise must be one you measured yourself; mark measured and inferred separately, and mark what you are unsure of instead of resolving it by assertion.
- When counting how many cases a rule covers, count with the rule itself, not a narrower condition: the narrow count is a false lower bound.
- Do not widen the scope past what was asked. Every extra check or screenshot lands in the context and is paid for; answer the question that was put (#514).
- Before killing a process, read that PID's command line and path, and stop only pids your own code recorded. A search by command-line pattern also matches the shell running the search, so it kills your own session (#479).
- Before a verbatim replace in an existing file, print the actual segment and match against it. A comment or one character of indentation off makes the replace fail, and a stripped dump of the file misleads several times in a row (#29).
