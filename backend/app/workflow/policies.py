REQUIRED_REPORT_FIELDS = {
    "Problem Statement",
    "Rationale",
    "Technical Details",
    "Datasets",
    "Source",
    "Target",
    "Paper Title",
    "Paper Abstract",
    "Methods",
    "Experiments",
    "Results",
    "References",
}


def normalize_feedback_verdict(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"passed", "pass", "success", "supported"}:
        return "supported"
    if normalized in {
        "failed",
        "fail",
        "failure",
        "error",
        "errored",
        "unsuccessful",
        "unsupported",
        "rejected",
    }:
        return "failed"
    if normalized == "partial":
        return "partial"
    return "partial"


def normalize_feedback_decision(value: object) -> str:
    """Normalize the model's explicit post-experiment route.

    Missing or unknown decisions fail closed to report export.  In particular,
    this function never infers continuation from free-form feedback text.
    """
    normalized = str(value or "").strip().upper()
    if normalized in {"REVISE", "PIVOT"}:
        return normalized
    if normalized in {"REPORT", "STOP", "TERMINATE", "FINALIZE"}:
        return "REPORT"
    return "REPORT"


def feedback_requires_follow_up(content: object) -> bool:
    """Read new decision-first revisions while preserving legacy artifacts."""
    value = content if isinstance(content, dict) else {}
    raw_decision = str(value.get("decision") or "").strip()
    if raw_decision:
        # Any explicit but invalid decision fails closed to REPORT.  The legacy
        # boolean is consulted only for artifacts that predate this field.
        return normalize_feedback_decision(raw_decision) in {"REVISE", "PIVOT"}
    return value.get("requires_follow_up") is True


def competition_export_allowed(report: dict) -> tuple[bool, str]:
    results = report.get("Results", {})
    if not results.get("is_real_experiment"):
        return False, "Report export requires remote_gpu or local_gpu experiment results."
    references = report.get("References", [])
    if not references:
        return False, "Report export requires verified references."
    unverified = [ref.get("title", "untitled") for ref in references if not ref.get("verified")]
    if unverified:
        return False, f"Report export requires verified references; unverified: {', '.join(unverified)}."
    unidentified = [
        ref.get("title", "untitled")
        for ref in references
        if not any(
            str((ref.get("identifiers") or {}).get(key) or "").strip()
            for key in ("doi", "arxiv")
        )
    ]
    if unidentified:
        return False, (
            "Report export requires a verified DOI or arXiv identifier; missing: "
            + ", ".join(unidentified)
            + "."
        )
    missing = [field for field in REQUIRED_REPORT_FIELDS if field not in report]
    if missing:
        return False, f"Report is missing required fields: {', '.join(sorted(missing))}."
    return True, ""
