# Results, not activity

Every easy check measures activity: workers alive, cadence met, ideas written, verdicts written.
One line of work here ran about 38 agent workers for two days, wrote roughly 7,000 ideas and 528
claims, had its reviewer pass 98% of them, produced nothing worth sending, and every check was
green.

The cause was the goal. Rules were added round after round until the spec ran to thousands of
words, and the workers learned to produce what passed the reviewer: narrow, self-defined variants
that were true and new to nobody.

**The rule.** A line of work exists to find something. Spending without finding is a failure of
that work, not a phase of it.

**Enforced by** a yield check that fails a lane which spends job-hours with no new refereed
result, which logs attempt after attempt with nothing new, whose reviewer passes 90%+, or which
produces nothing selective three rounds running. The last one redirects the method rather than
stopping the work: the answer to a dry streak is a different approach, not a halt.

**Where it leaks.** Rounds that run in-session against an external API are invisible unless they
are logged. One lane spent roughly $267 over 17 logged rounds against about 80 that happened, and
the yield check was blind to the difference.
