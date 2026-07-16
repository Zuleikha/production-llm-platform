# Code review

## What review is for

Review catches defects, spreads knowledge of the system, and keeps the codebase
something more than one person can maintain. It is not a gate a change has to
survive, and it is not where formatting is decided — a formatter and a linter run
in CI, and a human arguing about whitespace is a human not reading the logic.

## For the author

Open a small change. A reviewer reads a fifty-line diff and finds bugs; they skim
a thousand-line diff and find typos. If a change cannot be made small, describe
in the pull request how to read it and in what order.

Say why, not what. The diff already says what changed. The description should say
what problem this solves, what alternatives were rejected, and what a reviewer
should look at hardest.

A change that cannot be tested is a change that cannot be reviewed. If the test
is hard to write, that is usually the design telling you something.

## For the reviewer

Distinguish blocking from non-blocking, and say which one you mean. "Consider
renaming this" and "this drops errors silently" are not the same comment, and a
reviewer who does not mark the difference makes the author guess.

Review the change that was made, not the change you would have made. Different
is not worse.

Ask rather than assert when you do not understand. "What happens if this is
empty?" is a better comment than "this breaks when empty", and it costs nothing
when you turn out to be wrong.

Approve when it is better than what is there now. Waiting for perfect keeps good
changes out and encourages bigger ones.

## What must block

- A correctness defect, a race, or unhandled failure on a path that can happen.
- A secret, credential, or personal datum in code, config, or a log line.
- A silently swallowed error.
- A missing test for behaviour the change introduces.
