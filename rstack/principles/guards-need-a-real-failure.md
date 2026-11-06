# Build a guard only after a real failure that cost something

**The rule.** No new guard, gate, lint or receipt unless it fixes a failure that actually happened
and cost something: a rented machine that never shut off, a message sent to the wrong person,
results lost. Not a hole someone could imagine using.

**The evidence**, and it is about this repository's own author. A patch was written to bind an
outgoing message to the hash of the draft that had passed its lint. It was good engineering. It
answered two things that had never happened: that someone might lint one draft and send another,
and that a weak approval stamp might be forged. Neither was in the record of actual corrections.
The owner's reaction was the right one: *"Is it over engineering on process again? That's a common
failure... We don't care about process."*

The same instinct is what grew a lane's spec to thousands of words of layered rules until the
workers optimised the reviewer instead of the problem. A plausible risk is not a reason for a gate.

**How to apply.** Before proposing a check, name the incident it answers. If there is not one,
write the observation down and move on. The honest test:

- Passes: a deadline guard that crashed on malformed input and created a machine with no shutoff.
  A guard pointed at a path that had not existed for four months, so it had been doing nothing.
- Fails: a hash binding for a swap that has never occurred.

**This applies to work already written.** Do not keep a check because the effort is sunk. A lint
and four guard patches were dropped the same day under this rule, including ones their author had
argued for. A rule that exempts your own output is not a rule.
