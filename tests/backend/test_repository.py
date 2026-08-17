from concurrent.futures import ThreadPoolExecutor
import json
import threading

from backend.app.storage.repository import Repository


def test_repository_creates_run_and_versioned_artifacts(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    run = repo.create_run(problem_input="train a compact cnn", title="CNN robustness")

    artifact_1 = repo.add_artifact(
        run_id=run.id,
        artifact_type="hypothesis",
        title="Hypothesis v1",
        content={"claim": "augmentation improves robustness"},
        source_step="hypothesis_generation",
        created_by="hypothesis_agent",
    )
    artifact_2 = repo.add_artifact(
        run_id=run.id,
        artifact_type="hypothesis",
        title="Hypothesis v2",
        content={"claim": "augmentation improves corruption robustness"},
        source_step="feedback_revision",
        created_by="critic_agent",
        parent_artifact_id=artifact_1.id,
    )

    loaded = repo.get_run(run.id)

    assert artifact_1.version == 1
    assert artifact_2.version == 2
    assert loaded.artifacts[0].content["claim"] == "augmentation improves robustness"
    assert loaded.artifacts[1].parent_artifact_id == artifact_1.id


def test_locked_artifact_is_not_replaced_by_rerun(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    run = repo.create_run(problem_input="train model", title="Locked plan")
    artifact = repo.add_artifact(
        run_id=run.id,
        artifact_type="plan",
        title="Plan",
        content={"metric": "accuracy"},
        source_step="research_plan",
        created_by="planner_agent",
    )

    locked = repo.lock_artifact(run.id, artifact.id, locked=True)

    assert locked.locked is True
    assert repo.get_artifact(run.id, artifact.id).locked is True


def test_repository_serializes_cross_run_artifact_and_event_transactions(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    first = repo.create_run(problem_input="first", title="First")
    second = repo.create_run(problem_input="second", title="Second")
    start = threading.Barrier(2)
    mutation_count = 20

    def mutate(run_id: str, label: str):
        start.wait()
        for index in range(mutation_count):
            repo.add_artifact(
                run_id=run_id,
                artifact_type="note",
                title=f"{label} artifact {index}",
                content={"label": label, "index": index},
                source_step="problem_understanding",
                created_by="concurrency-test",
            )
            repo.append_event(
                run_id,
                "problem_understanding",
                "concurrency-test",
                f"{label} event {index}",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(mutate, first.id, "first"),
            executor.submit(mutate, second.id, "second"),
        ]
        for future in futures:
            future.result(timeout=15)

    stored_first = repo.get_run(first.id)
    stored_second = repo.get_run(second.id)
    for stored, label in [(stored_first, "first"), (stored_second, "second")]:
        notes = [artifact for artifact in stored.artifacts if artifact.type == "note"]
        assert len(notes) == mutation_count
        assert [artifact.version for artifact in notes] == list(range(1, mutation_count + 1))
        assert {artifact.content["label"] for artifact in notes} == {label}
        assert len(stored.events) == mutation_count

    raw = json.loads((tmp_path / "runs.json").read_text(encoding="utf-8"))
    assert set(raw) == {first.id, second.id}
    assert not (tmp_path / "runs.tmp").exists()
