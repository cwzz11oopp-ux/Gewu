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
