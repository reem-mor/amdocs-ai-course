"""Smoke test the five preset queries against the live server.

Verifies (per the spec checklist):
  - All 5 preset buttons produce a 200 response
  - confidence is one of {high, medium, low}  (never 'none' for these presets)
  - sources is a non-empty list
  - answer contains the four required '## ' section markers
  - At least one query (Q1 with P1 filter) returns >=1 source after filter
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

ENDPOINT = "http://127.0.0.1:8000/api/query"

PRESETS: list[tuple[str, str, str | None]] = [
    ("Q1", "What are the triage steps for a P1 Kubernetes pod crash loop?", None),
    ("Q2", "How do I resolve PostgreSQL connection pool exhaustion?", None),
    ("Q3", "What is the SOP for Kafka consumer lag incidents?", None),
    ("Q4", "We are seeing 5xx errors spiking on our API gateway, what do I do?", None),
    ("Q5", "How do I handle an AWS IAM permission denied error in CI/CD?", None),
    ("Q1-P1", "What are the triage steps for a P1 Kubernetes pod crash loop?", "P1"),
]

REQUIRED_SECTIONS = ("## Assessment", "## Triage Steps", "## Resolution Steps", "## Escalation")


def call(question: str, severity: str | None) -> dict:
    payload: dict[str, object] = {"question": question}
    if severity is not None:
        payload["severity_filter"] = severity
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    fails: list[str] = []
    for label, question, severity in PRESETS:
        try:
            data = call(question, severity)
        except urllib.error.HTTPError as exc:
            fails.append(f"{label}: HTTP {exc.code} {exc.reason}")
            continue
        except Exception as exc:  # noqa: BLE001
            fails.append(f"{label}: {type(exc).__name__}: {exc}")
            continue

        answer: str = data.get("answer", "")
        confidence: str = data.get("confidence", "none")
        sources: list = data.get("sources", [])
        retrieved: int = data.get("retrieved_count", 0)
        elapsed: int = data.get("processing_time_ms", 0)

        missing = [s for s in REQUIRED_SECTIONS if s not in answer]
        ok_sections = not missing
        ok_sources = len(sources) >= 1
        ok_conf = confidence in {"high", "medium", "low"}

        status = "PASS" if (ok_sections and ok_sources and ok_conf) else "FAIL"
        print(
            f"{status} {label} conf={confidence} sources={len(sources)} "
            f"retrieved={retrieved} elapsed={elapsed}ms "
            f"missing_sections={missing if missing else '-'}"
        )
        if status == "FAIL":
            fails.append(label)

    if fails:
        print(f"\nFAILED: {fails}")
        return 1
    print("\nAll preset queries PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
