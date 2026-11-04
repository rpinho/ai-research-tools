"""lane-nudge decides from idle time, running state, cadence status and its own rate limit."""
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
nudge = SourceFileLoader('lane_nudge', str(REPO / 'harness/tools/lane-nudge/lane-nudge')).load_module()
NOW = 1_000_000.0


def test_quiet_lane_with_nothing_running_is_nudged():
    assert nudge.decide(90, False, 'OK', 0, NOW) == 'quiet'


def test_cadence_behind_is_named():
    assert nudge.decide(61, False, 'STALLED', 0, NOW) == 'STALLED on cadence'


def test_running_work_or_recent_turn_is_left_alone():
    assert nudge.decide(500, True, 'STALLED', 0, NOW) is None
    assert nudge.decide(59, False, 'WARN', 0, NOW) is None


def test_rate_limited_per_lane():
    assert nudge.decide(500, False, 'WARN', NOW - 3600, NOW) is None
    assert nudge.decide(500, False, 'WARN', NOW - 7201, NOW) == 'WARN on cadence'


def test_exempt_and_unconfigured_never_nudged():
    assert nudge.decide(10_000, False, 'EXEMPT', 0, NOW) is None
    assert nudge.decide(10_000, False, 'UNCONFIGURED', 0, NOW) is None


def test_running_pattern_matches_claude_code_status_lines():
    assert nudge.RUNNING.search('done 2:41 PM · 1 shell still running')
    assert nudge.RUNNING.search('Shimmying… (esc to interrupt)')
    assert not nudge.RUNNING.search('Worked for 3m 15s · done 10:47 AM')
