"""Progress parser + streaming unit tests (design: honest, deterministic)."""

from api.admin import AdminService
from api.progress import ProgressUpdate, parse


def test_parse_standard_eta_line():
    update = parse("  [observations] 1,234/5,678 (22%) eta 12:34")
    assert update is not None
    assert update.phase == "observations"
    assert update.done == 1234 and update.total == 5678
    assert update.pct == 22.0
    assert update.eta_seconds == 12 * 60 + 34


def test_parse_eta_with_hours():
    update = parse("[parties] 100/900 (11%) eta 1:02:03")
    assert update is not None
    assert update.eta_seconds == 3600 + 2 * 60 + 3


def test_parse_ignores_non_progress_lines():
    for line in ("current -> generations/gen_x", "Wrote 10 artifacts", "", "some debug text"):
        assert parse(line) is None, line


def test_parse_clamps_pct_and_normalizes_commas():
    update = parse("[queries] 0/1,000 (130%) eta 00:05")
    assert update is not None
    assert update.pct == 100.0
    update_low = parse("[queries] 0/100 (0%) eta 00:05")
    assert update_low is not None and update_low.pct == 0.0


def test_stream_parse_forwards_only_matching_lines():
    received: list[ProgressUpdate] = []

    AdminService.stream_parse(
        [
            "some noise",
            "[parties] 1/10 (10%) eta 00:01",
            "more noise",
            "[parties] 5/10 (50%) eta 00:02",
        ],
        received.append,
    )
    assert [u.pct for u in received] == [10.0, 50.0]
    assert received[0].done == 1
