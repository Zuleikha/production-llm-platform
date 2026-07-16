# Incident response

## Severity levels

Severity is set by customer impact, not by how alarming the graph looks.

- **Sev 1** — the service is unavailable, or is returning wrong data to
  customers. Wrong data is Sev 1 even when everything is nominally up: an
  unavailable service is obvious to the customer, whereas silently wrong data is
  not, and they may act on it.
- **Sev 2** — degraded. Elevated errors or latency, a failing dependency the
  service is riding out, or a feature down while the core path works.
- **Sev 3** — a defect with no live customer impact. Fix it in the normal
  queue; do not page anyone.

If two people disagree about severity, take the higher one. Downgrading later is
cheap and can be done calmly.

## During an incident

One person is incident commander and does not debug. Their job is to decide, to
keep a timeline, and to make sure exactly one person owns each thread of work.
An incident with three people independently restarting things is not being
handled, it is being made worse.

Mitigate before diagnosing. Roll back, shed load, or fail over first; understand
it afterwards. The urge to find the root cause while customers are affected is
the single most common way an incident gets longer.

Record what you did as you do it, including the things that did not work. A
timeline written afterwards from memory is a story, not a record.

## Afterwards

Every Sev 1 and Sev 2 gets a written review within five working days. The review
is blameless: it examines the system that let a person's reasonable action cause
harm, not the person. "Someone was careless" is never a root cause — if being
careful was the only thing standing between the system and an outage, that is
the finding.

Actions from a review need an owner and a date, or they are not actions.
