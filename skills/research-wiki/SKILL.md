---
name: research-wiki
description: Query the persistent Research Wiki before external search and propose auditable updates after evidence review.
allowed-tools: read_run, read_artifact, query_wiki, propose_wiki_changes
---

# Research Wiki

## Read Path

Query the Wiki for each normalized literature query before external retrieval. An uninitialized, empty, no-match, or degraded Wiki is a normal state: return the stable warning and continue the workflow.

## Write Proposal

After evidence synthesis, propose canonical paper, gap, idea, experiment, and claim nodes plus typed edges. Preserve source provenance and verification status. Adding a local paper to Wiki improves retrieval but does not make it an exportable citation.

Return a `WikiChangeSet`; never commit directly. Supervisor owns the mutation gate and audit log.
