# Verification levels

Verification describes evidence for an explicitly scoped statement. It does not
establish novelty, authorship, priority, human refereeing, or acceptance by a
table or journal. A finite calculation never establishes an all-size statement
merely because it passes many tests.

| Level | Evidence required | What it establishes, and its limit |
| --- | --- | --- |
| L0 | Prose, AI review, exploratory calculations, or weaker evidence | A recorded claim without strict L1/L2/L3 evidence. Same-agent checkers and reviewer replays alone remain L0 under this rule. |
| L1 | Exact finite certificate, a differently authored checker, matching output hash and run receipt | The finite claim checked by that independent implementation. Published standard transfers may be cited; an essential new universal bridge left in prose keeps the whole bound L0. |
| L2 | Pinned Lean project, successful build and source hash, main-theorem axiom output, and cited AXIOMS.md | The stated Lean step, conditional on its hypotheses and explicitly inherited axioms. A Lean proof of our step with axiomatized inherited results is **not a proof of the whole bound**. |
| L3 | The L2 packet, with an empty non-standard axiom list | The full scoped statement in Lean using only `propext`, `Classical.choice`, and `Quot.sound`. This still depends on the Lean kernel, toolchain, and the correspondence between the formal statement and the intended mathematics. |

An L3 proof of a finite lemma does not make a surrounding analytic bound L3.
External numerical assumptions, conditional hypotheses, and unformalized new
bridges must remain visible in the statement and public text. Numeric certificates
alone do not discharge Lean hypotheses.

## Rows and opt-in

Result and close events may carry both `verification` (`L0`–`L3`) and
`finite_component` (`L0`–`L3` or `none`). If either field is present, both must be
valid. Untagged historical rows remain valid. Nothing in this implementation
enables a lane. The lane owner can set the top-level TOML key:

```toml
verification_required = true
```

New closes in an opted-in lane require both fields, including administrative or
negative closes (normally `L0` and `none`). The CLI refuses an invalid packet
before appending to IDEAS.jsonl. Automatic intake exclusions in opted-in lanes
record `L0` and `none`. Lint checks result/close history and catches new
closes written outside the CLI using the rollout timestamp
`2026-11-03T18:06:17+00:00` or the new `verification_policy_version = 1` marker.
Existing rows before that cutoff need no migration. CLI closes also record
`verification_required_at_close`; closes made before a lane opts in remain
optional when its owner later enables the flag. Malformed timestamps do not
grandfather externally appended closes.

```sh
rh ideas close LANE ID --closed-reason 'source exclusion' \
  --no-test-reason 'source exclusion' --verification L0 --finite-component none \
  --decided-by MODEL
rh ideas result LANE ID --result-path RESULT --config-path CONFIG \
  --verification-evidence EVIDENCE.json
```

`--verification` and `--finite-component` override those fields in the JSON.
The `--verification-evidence` filename resolves from the current directory.
Paths inside that JSON and checker arguments resolve relative to the **lane**,
not the JSON file. Absolute paths work too. A new result clears earlier verification
metadata unless explicitly supplied; a close may reuse the unchanged recorded
result's packet. Supplying new evidence replaces the old packet completely.
Existing attempt, freeze, prior-art, taste and review rules still apply.

For any level above L0, `verification_scope` names the exact checked or proved
statement in one line. Evidence fields are on the row. If the finite component
has a different packet, put that packet in `finite_evidence`; otherwise both
levels use the row's shared packet. For example:

```json
{
  "verification": "L0",
  "finite_component": "L1",
  "finite_evidence": {
    "verification_scope": "the exact finite witness",
    "producer_author": "model-or-party-A",
    "checker_author": "different-model-or-party-B",
    "certificate": "certificate.json",
    "checker": ["python3", "independent_checker.py", "certificate.json"],
    "checker_max_seconds": 30,
    "expected_output_sha256": "64 lowercase hex characters",
    "run_receipt": "receipts/check.json",
    "run_receipt_sha256": "64 lowercase hex characters"
  }
}
```

## L1 receipt

`checker` is an argv list or a shell-quoted command parsed with `shlex`; no shell
operators or expansion execute. Record producer and checker identities in both
the packet and receipt. The identities must differ after whitespace/case
normalization. A different session of the same model is not independent
authorship. A reviewer running the supplied checker is a replay, not a second
implementation. The harness checks recorded provenance, not whether an author
claim is honest or whether the checker implements the intended exact arithmetic.

The JSON run receipt contains:

```json
{
  "command": ["python3", "independent_checker.py", "certificate.json"],
  "exit_code": 0,
  "producer_author": "model-or-party-A",
  "checker_author": "different-model-or-party-B",
  "certificate_sha256": "SHA256 of certificate bytes",
  "output": "receipts/check.stdout",
  "output_sha256": "SHA256 of exact stdout bytes",
  "checker_sources_sha256": {
    "independent_checker.py": "SHA256 of checker source bytes"
  }
}
```

Hash the receipt itself into `run_receipt_sha256`. List all checker implementation
files in `checker_sources_sha256`, not merely a wrapper. Lint verifies the
certificate, checker sources, receipt and saved stdout. If
`checker_max_seconds <= 120`, lint also reruns the command with that wall-time
limit and compares its exact stdout hash. Larger runs use the hashed receipt
without launching the checker. A timeout or nonzero exit fails.

On macOS the checker runs with network denied and filesystem writes denied
except a disposable scratch directory (`TMPDIR`). Lint never needs to change a
certificate or lane data. Checkers must support this read-only replay contract.
On other platforms, use a read-only sandbox/container before replaying third-party
checker code; the standard-library runner itself supplies timeouts but no OS
filesystem isolation there.

## Lean packet and receipts

```json
{
  "verification": "L2",
  "finite_component": "none",
  "verification_scope": "the new finite envelope step, with its stated hypotheses",
  "lean_project": "proof",
  "main_theorems": ["Project.main"],
  "build_receipt": "proof/receipts/build.json",
  "build_receipt_sha256": "SHA256 of build.json",
  "axioms_receipt": "proof/receipts/axioms.txt"
}
```

The project must have `lean-toolchain` pinned to a release or commit and
`lake-manifest.json` with resolved git commit hashes for every dependency,
including Mathlib when used. Floating toolchain names and local/unpinned
dependency manifests fail. Lint does not download dependencies or rebuild Lean.

The build receipt is JSON:

```json
{
  "command": ["lake", "build"],
  "exit_code": 0,
  "source_sha256": "verification.source_hash(project)",
  "output": "proof/receipts/build.log",
  "output_sha256": "SHA256 of raw lake build output",
  "axioms_sha256": "SHA256 of axioms_receipt",
  "axioms_md_sha256": "SHA256 of proof/AXIOMS.md"
}
```

`source_hash` hashes sorted project-relative names and file hashes, encoded as
`relative/path + NUL + sha256 + newline`. It includes every `.lean` file,
`lean-toolchain`, `lake-manifest.json` and `lakefile.toml` in the project tree;
`.lake`, `.git` and Python caches are excluded. Keep receipts and source archives
free of duplicate `.lean` source trees. Source symlinks are unsupported. The
project's dependencies are supplied by the pinned manifest; lint does not audit
the entire Mathlib source tree.

Keep raw `#print axioms Project.main` output for **each** listed main theorem.
Those audit commands must also occur in the hashed project sources. Both Lean's
`depends on axioms: [...]` and `does not depend on any axioms` outputs are
recognized. All non-standard axioms in the output or explicit project axiom
declarations must be disclosed. `AXIOMS.md` uses this machine-readable table:

```markdown
| Axiom | Citation |
| --- | --- |
| `Project.inherited` | https://example.org/paper-v3, Proposition 3.2 |
```

Use an exact versioned source URL, DOI, arXiv identifier or ISBN with a
theorem/section/equation/page locator. If there are none, write:

```markdown
Non-standard axiom list: **empty**.
```

An empty declaration contradicted by table entries fails. L3 rejects even an
unused non-standard table entry. Any `sorry`, `admit`, or `sorryAx` token in
project Lean sources or proof receipts fails, including comments mentioning one.
Documentation is not scanned for those forbidden tokens. `native_decide` (and
`Lean.ofReduceBool`) requires a substantive AXIOMS.md line mentioning
`native_decide` and explaining compiled evaluation's extra trust boundary. A
line saying the tactic was never used does not count. Any resulting extra
axiom must also be listed and cited; L3 still permits only standard axioms.

Receipts are local records of executions, not cryptographic proof that a remote
party ran a command. Hashes detect changed bytes relative to the recorded packet;
reviewers must still inspect statement coverage, provenance, exactness and
citations. The test replay uses generated **SYNTHETIC** Lean filenames, statements,
dependency pins, hashes and receipt output. It validates packet handling only;
no real lane packet or successful Lean build is distributed.

## Optional publication hook pattern

`rh.verification_line(row)` (also `verification.verification_line`) renders a
one-line statement for PR text and TABLE_RECORD. For example:

```text
Verification level: L0 (prose or AI review; no strict certificate claim); finite component: L1.
```

For a publication body `body.md` claiming a strict level, supply
`body.md.verification.json`:

```json
{
  "sha256": "SHA256 of exact body.md bytes",
  "row": {"verification": "L2", "finite_component": "none", "verification_scope": "...", "lean_project": "..."}
}
```

The row must contain the complete evidence packet described above. Its paths
resolve relative to the body file. This repository does not ship a publication
gate. An optional hook at your publication boundary can call the portable helper:

```python
from verification import publication_errors

errors = publication_errors(body_path)
if errors:
    raise ValueError("; ".join(errors))  # Refuse publication at your boundary.
```

Add `harness/` to the hook's Python import path. Call this check on the exact body
bytes immediately before publication, alongside your review freshness, action,
target and body-byte checks. `publication_errors` validates the evidence with
`rerun=False`; it never runs a checker or a Lean build. Changed receipt bytes
invalidate their hashes. Review receipts do not substitute for verification
receipts. Integration and enforcement at the actual send boundary are the
operator's responsibility.

Bare `L1`, `L2` and `L3` tokens are conservatively treated as whole-result claims;
explicit `finite component: L1`, `finite parts L1` and similar labels use the
finite component. A finite L1 packet cannot authorize an L1 whole-bound claim.
`L1-same-agent`, `L1-independence-unestablished` and `L1-partial` annotations do
not assert strict L1. Text without a strict level claim needs no verification
sidecar. Keep discussions quoting hypothetical levels in a separate document:
the hook validates claims conservatively, not the semantics of arbitrary prose.
Use one scoped verification packet per body. Human review must ensure every
claim in that body actually matches the packet's theorem or certificate scope.

## Tests

From the repository root:

```sh
uv run --no-project --with pytest --with numpy python -m pytest -q
```

`harness/tests/test_verification.py` covers synthetic Lean L2/L3 receipts,
axiom/citation rules, source and receipt tampering, forbidden placeholders,
native evaluation disclosure, L1 independence and replay/receipt boundaries,
CLI/lint opt-in, whole/finite splits and optional publication-helper overclaims.
No installed personal hook, real research packet, Lean compiler or dependency
download is required by these tests.
