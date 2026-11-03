# Synthetic lesson backfill

This is invented teaching data, not a claim about a real challenge. After
creating a toy lane, write a local evidence note and import one lesson:

```sh
mkdir -p .runtime/lanes/sample-a
printf '%s\n' 'Invented example: count-only control matched a toy model.' > .runtime/lanes/sample-a/control.md
python3 harness/rh.py --root .runtime lessons add --lane sample-a \
  --source backfill --tags synthetic,evaluation,shortcut \
  --lesson 'In the invented example, a count-only control matched the model.' \
  --evidence lanes/sample-a/control.md \
  --applies-when 'Testing a synthetic label-frequency shortcut'
python3 harness/rh.py --root .runtime lessons list
```

The generated runtime JSONL is local and ignored. Real evidence, human review,
and an actual enforcing file are needed before promoting a real lesson.
