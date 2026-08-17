from __future__ import annotations


EXPERIMENT_LINEAGE_TYPES = frozenset(
    {"experiment_bundle", "experiment_diagnosis"}
)


def experiment_lineage_ids(artifacts, task_id: str) -> set[str]:
    """Return every bundle/diagnosis descendant belonging to one experiment task."""
    lineage_ids = {task_id}
    changed = True
    while changed:
        changed = False
        for artifact in artifacts:
            if (
                artifact.type in EXPERIMENT_LINEAGE_TYPES
                and artifact.parent_artifact_id in lineage_ids
                and artifact.id not in lineage_ids
            ):
                lineage_ids.add(artifact.id)
                changed = True
    return lineage_ids


def experiment_bundle_ids(artifacts, task_id: str) -> set[str]:
    lineage_ids = experiment_lineage_ids(artifacts, task_id)
    return {
        artifact.id
        for artifact in artifacts
        if artifact.type == "experiment_bundle" and artifact.id in lineage_ids
    }


def result_for_experiment_task(artifacts, task):
    experiment_id = str(task.content.get("experiment_id") or "")
    bundle_ids = experiment_bundle_ids(artifacts, task.id)
    return next(
        (
            artifact
            for artifact in reversed(artifacts)
            if artifact.type == "experiment_result"
            and artifact.content.get("experiment_id") == experiment_id
            and (not bundle_ids or artifact.parent_artifact_id in bundle_ids)
        ),
        None,
    )
