# Model memory: fable

How this model tends to go wrong, and what helps.

- Instructions say the intent, not only the action. A literal executor given only the action will undo the previous fix on the way. (2026-09-10)
- Do not restate secondary evidence as fact. A test's failure message or a log line may point at something that does not exist; look at the thing itself before writing a claim or a reproduction step. (#475)
- Run or read the help of every command and flag before writing it into an instruction for someone else. A wrong flag or precondition makes every rebuttal correct. (#627)
- Before an irreversible action on a process or file, identify the exact target first. Matching by name catches another line's work; I have broken this rule of my own. (2026-09-13)
