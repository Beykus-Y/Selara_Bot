# Agent responses and administrator receipts

The group agent executes actual tool calls regardless of a provider's
finish_reason label, including providers returning `stop` with tool calls.
A blank/whitespace/null completion or empty choices gets at most two continuation
requests, inside the existing eight-round limit. Continuations reuse tool results;
they instruct the model not to repeat completed actions or uncertain deliveries.
A confirmed artifact send remains terminal, with its caption carrying the answer.

If the model still returns nothing or the round budget ends, a server fallback
shows available verified leaderboard values and their actual periods. Activity
uses message counts; karma uses karma values. Unknown totals/percentages, missing
periods and unconfirmed images are not invented. The actual fallback text is saved
in the interaction history and administrator receipt.

The private administrator receipt is now assembled by the server, without a
second model call. It reports actual tool successes/errors, the confirmed artifact
count when applicable, and the text of the delivered answer. Artifact IDs retained
for future chat context are not presented as part of the text sent to the group.
Rollback buttons retain their existing authorization and audit-record checks.
