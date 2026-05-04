import json
import pytest


phase_state: dict = {}  # nodeid -> entry accumulated across setup/call/teardown
stream_fh = None
written: set = set()


def pytest_addoption(parser):
    parser.addoption(
        "--result-jsonl", default="/tmp/report.jsonl",
        help="Path to write streaming JSONL test results",
    )


def pytest_sessionstart(session):
    global stream_fh
    path = session.config.getoption("result_jsonl")
    stream_fh = open(path, "w", buffering=1)


def emit(entry: dict) -> None:
    nid = entry.get("nodeid", "")
    if nid in written:
        return
    written.add(nid)
    stream_fh.write(json.dumps(entry) + "\n")
    stream_fh.flush()


@pytest.hookimpl(trylast=True)
def pytest_runtest_logreport(report):
    nodeid = report.nodeid
    entry = phase_state.setdefault(nodeid, {
        "nodeid": nodeid,
        "outcome": "pending",
        "duration": 0.0,
        "longrepr": "",
    })
    entry["duration"] += report.duration
    
    if report.failed:
        entry["outcome"] = "failed" if report.when == "call" else "error"
        entry["longrepr"] = str(report.longrepr) if report.longrepr else ""
    elif report.skipped:
        if entry["outcome"] not in ("failed", "error"):
            entry["outcome"] = "xfailed" if hasattr(report, "wasxfail") else "skipped"
            entry["longrepr"] = str(report.longrepr) if report.longrepr else ""
    elif report.passed and report.when == "call":
        if hasattr(report, "wasxfail"):
            entry["outcome"] = "xpassed"
        elif entry["outcome"] not in ("failed", "error"):
            entry["outcome"] = "passed"
            
    if report.when == "teardown":
        if entry["outcome"] == "pending":
            entry["outcome"] = "error"
            if not entry["longrepr"]:
                entry["longrepr"] = "incomplete test report"
        emit(entry)
        phase_state.pop(nodeid, None)


def pytest_collectreport(report):
    if not report.nodeid:
        return
    if report.failed:
        emit({
            "nodeid": report.nodeid,
            "outcome": "error",
            "duration": 0.0,
            "longrepr": str(report.longrepr) if report.longrepr else "collection error",
        })
    elif report.skipped:
        emit({
            "nodeid": report.nodeid,
            "outcome": "skipped",
            "duration": 0.0,
            "longrepr": str(report.longrepr) if report.longrepr else "module skipped",
        })


def pytest_sessionfinish(session, exitstatus):
    # Flush tests that started but never reached teardown.
    for nid, entry in list(phase_state.items()):
        if entry["outcome"] == "pending":
            entry["outcome"] = "error"
            if not entry["longrepr"]:
                entry["longrepr"] = "incomplete test report"
        emit(entry)
    phase_state.clear()
    if stream_fh is not None:
        stream_fh.close()
