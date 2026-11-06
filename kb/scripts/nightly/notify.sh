#!/usr/bin/env bash
# notify.sh: the ONE delivery seam for scheduled jobs. Plug your channel in here.
#
#   notify.sh digest <file> <subject>    deliver the digest
#   notify.sh notice <subject> <body>    deliver a failure notice (and raise an alert)
#
# Configure with env (or edit the two functions):
#   DIGEST_SEND_CMD  a command run as: $DIGEST_SEND_CMD <file> <subject>   (mail it, post it, publish it)
#   NOTICE_SEND_CMD  a command run as: $NOTICE_SEND_CMD <subject> <body>   (phone push, chat webhook)
#   e.g. NOTICE_SEND_CMD='sh -c "curl -s -d \"$0: $1\" https://ntfy.sh/<your-private-topic>"'
#
# Rules: a notice ALWAYS also lands in the alerts dir (scripts/alert.py), so the next digest and any
# push loop see it even if the channel is down. An unconfigured channel is an error, never a silent
# success: exit 3 and say so.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kind="${1:-}"; shift || true

case "$kind" in
  digest)
    file="$1"; subject="$2"
    [ -s "$file" ] || { echo "notify: digest file missing or empty: $file" >&2; exit 2; }
    if [ -z "${DIGEST_SEND_CMD:-}" ]; then
      python3 "$here/alert.py" raise --key digest-delivery --msg "Digest written but no DIGEST_SEND_CMD configured: $file"
      echo "notify: DIGEST_SEND_CMD not set; the digest is at $file (alert raised)" >&2; exit 3
    fi
    $DIGEST_SEND_CMD "$file" "$subject" ;;
  notice)
    subject="$1"; body="${2:-}"
    python3 "$here/alert.py" raise --key "notice-$(date +%F)" --msg "$subject" || true
    if [ -z "${NOTICE_SEND_CMD:-}" ]; then
      echo "notify: NOTICE_SEND_CMD not set; notice recorded as an alert only: $subject" >&2; exit 3
    fi
    $NOTICE_SEND_CMD "$subject" "$body" ;;
  *) echo "usage: notify.sh digest <file> <subject> | notice <subject> <body>" >&2; exit 2 ;;
esac
