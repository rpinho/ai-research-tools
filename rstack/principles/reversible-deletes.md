# Every deletion is reversible

**The rule.** Never hard-delete anything the owner might want back. Move it to the system trash,
or to a dated backup. Unconditional recursive deletion is denied outright. Before deleting data
you did not just create, verify a copy exists elsewhere first: extract, verify, then remove.

**Enforced by** a trash-only storage adapter that refuses to delete at all when no trash command
is configured, a guard that denies file-manager-driven deletes, and a permission rule that denies
recursive force-deletes.

It reads as paranoia until the first time a cleanup step removes the only copy of an input a week
of work depended on.
