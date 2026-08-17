---
name: research-lit
description: Retrieve and synthesize local and external literature with explicit provenance and verification status.
allowed-tools: read_run, read_artifact, search_local_literature, literature_search
---

# Research Literature

## Retrieval

Use every query from the problem contract. Search the local literature library and the configured external literature provider. Keep local-only records separate from externally verified citation records.

## Evidence Rules

- Deduplicate by DOI, then arXiv identifier, then normalized title.
- Preserve source kind, local document ID, URL, identifiers, authors, year, and verification provider.
- Never infer missing bibliographic data or claim that upload status proves a citation.
- Treat provider failure as a warning and retain evidence returned by other sources.

## Output

Return `references`, `local_only`, `warnings`, `sources`, and proposed Wiki paper records. Each reference must state which claim it supports and whether it is exportable.

## Retrieval rounds

Use several diverse queries for broad/topic/gap retrieval; do not keep irrelevant
provider-default results in core references. Rank every paper using actual query
match plus source quality and recency, and persist the computed relevance score.
After a candidate exists, accept candidate-specific mechanism/gap/citation queries.
For every returned record preserve a real title, authors, year, venue when available,
abstract, URL, DOI/arXiv ID, source, relevance score, and verification status.
