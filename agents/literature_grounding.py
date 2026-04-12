"""Literature Grounding Agent for SPORE post-fire pipeline.

Takes a fire-rated hypothesis and anchors it in real scientific literature
via Semantic Scholar. Produces a novelty assessment, evidence base,
counter-evidence analysis, and gap manifest update.

Kill conditions:
- novelty_assessment.verdict == "already_proven" -> STOP
- counter_evidence contains severity == "fatal" -> STOP
"""

import json
from typing import Any, Optional, TypedDict

from agents.base import load_prompt
from knowledge.semantic_scholar import SemanticScholarClient, get_semantic_scholar_client
from llm import get_llm_client
from logging_config import get_logger, get_token_tracker

logger = get_logger("literature_grounding")


class GroundingInput(TypedDict):
    """Input for the Literature Grounding Agent."""

    hypothesis: str
    domains: list[str]
    mechanisms: str
    keywords: list[str]
    gap_manifest: dict[str, Any]


class GroundingOutput(TypedDict):
    """Output of the Literature Grounding Agent."""

    novelty_assessment: dict[str, Any]
    evidence_base: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]
    gap_manifest_update: dict[str, Any]
    all_papers: list[dict[str, Any]]
    search_queries: list[dict[str, str]]
    kill_reason: Optional[str]


def _extract_json(content: str) -> dict[str, Any]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return json.loads(content.strip())


def _extract_doi(paper: dict[str, Any]) -> Optional[str]:
    """Extract DOI from a Semantic Scholar paper dict."""
    external_ids = paper.get("externalIds") or {}
    doi = external_ids.get("DOI")
    return doi if doi else None


def _format_paper_for_llm(paper: dict[str, Any]) -> dict[str, Any]:
    """Format a paper dict for inclusion in the LLM prompt."""
    authors = paper.get("authors") or []
    author_names = [a.get("name", "") for a in authors[:5]]
    if len(authors) > 5:
        author_names.append(f"... +{len(authors) - 5} more")

    tldr = paper.get("tldr")
    tldr_text = tldr.get("text", "") if isinstance(tldr, dict) else ""

    return {
        "paper_id": paper.get("paperId", ""),
        "title": paper.get("title", ""),
        "year": paper.get("year"),
        "citation_count": paper.get("citationCount", 0),
        "authors": author_names,
        "doi": _extract_doi(paper),
        "abstract": (paper.get("abstract") or "")[:500],
        "tldr": tldr_text,
    }


async def _step1_extract_queries(
    hypothesis: str,
    domains: list[str],
    mechanisms: str,
) -> list[dict[str, str]]:
    """Step 1: Use LLM to generate optimized search queries.

    Returns:
        List of {"query": str, "type": str, "rationale": str} dicts.
    """
    client = get_llm_client("literature_grounding")
    tracker = get_token_tracker()

    prompt_template = load_prompt("literature_grounding_queries")
    prompt = prompt_template.format(
        hypothesis=hypothesis,
        domains=", ".join(domains),
        mechanisms=mechanisms,
    )

    logger.info("extracting_search_queries", domains=domains)
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.4,
    )

    tracker.log_call(
        agent="literature_grounding",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    try:
        data = _extract_json(response.content)
        queries = data.get("queries", [])
        logger.info("queries_extracted", count=len(queries))
        return queries
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("query_extraction_failed", error=str(exc))
        # Fallback: generate basic queries from hypothesis and domains
        fallback = [
            {"query": hypothesis[:80], "type": "novelty", "rationale": "direct hypothesis search"},
        ]
        for domain in domains:
            fallback.append(
                {"query": f"{domain} {mechanisms[:40]}", "type": "evidence", "rationale": "domain+mechanism"}
            )
        return fallback


async def _step2_search_papers(
    queries: list[dict[str, str]],
    ss_client: SemanticScholarClient,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Step 2: Execute searches on Semantic Scholar.

    Returns:
        Tuple of (deduplicated_papers, papers_by_query_type).
    """
    seen_ids: set[str] = set()
    all_papers: list[dict[str, Any]] = []
    papers_by_type: dict[str, list[dict[str, Any]]] = {
        "novelty": [],
        "evidence": [],
        "cross_domain": [],
    }

    for q in queries:
        query_text = q.get("query", "")
        query_type = q.get("type", "evidence")

        if not query_text:
            continue

        logger.info("ss_searching", query=query_text, type=query_type)
        results = await ss_client.search_papers(
            query=query_text,
            limit=10,
        )

        for paper in results:
            pid = paper.get("paperId", "")
            if not pid or pid in seen_ids:
                continue

            # Filter: require abstract and citationCount >= 5 (except for novelty checks)
            has_abstract = bool(paper.get("abstract"))
            citation_count = paper.get("citationCount", 0) or 0

            if query_type != "novelty" and citation_count < 5:
                continue

            if has_abstract:
                seen_ids.add(pid)
                all_papers.append(paper)
                papers_by_type.setdefault(query_type, []).append(paper)

    logger.info(
        "search_complete",
        total_unique=len(all_papers),
        novelty=len(papers_by_type.get("novelty", [])),
        evidence=len(papers_by_type.get("evidence", [])),
        cross_domain=len(papers_by_type.get("cross_domain", [])),
    )

    return all_papers, papers_by_type


async def _step3_analyze(
    hypothesis: str,
    domains: list[str],
    mechanisms: str,
    all_papers: list[dict[str, Any]],
    gap_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Step 3: LLM analysis of search results.

    Returns:
        Parsed JSON analysis with novelty_assessment, evidence_base,
        counter_evidence, gap_manifest_update.
    """
    client = get_llm_client("literature_grounding")
    tracker = get_token_tracker()

    # Format papers for prompt
    formatted_papers = [_format_paper_for_llm(p) for p in all_papers]

    prompt_template = load_prompt("literature_grounding_analysis")
    prompt = prompt_template.format(
        hypothesis=hypothesis,
        domains=", ".join(domains),
        mechanisms=mechanisms,
        paper_count=len(formatted_papers),
        papers_json=json.dumps(formatted_papers, indent=2, ensure_ascii=False),
        gap_manifest=json.dumps(gap_manifest, indent=2, ensure_ascii=False),
    )

    logger.info("analyzing_papers", paper_count=len(formatted_papers))
    response = await client.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4000,
        temperature=0.3,
    )

    tracker.log_call(
        agent="literature_grounding",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        provider=response.provider,
        cache_hit=response.cache_hit,
    )

    try:
        analysis = _extract_json(response.content)

        # Validate that cited paper_ids exist in our search results
        valid_ids = {p.get("paperId") for p in all_papers}
        analysis = _validate_references(analysis, valid_ids, all_papers)

        return analysis
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("analysis_parse_failed", error=str(exc), raw=response.content[:500])
        return {
            "novelty_assessment": {
                "score": 0.5,
                "closest_existing_work": [],
                "verdict": "novel",
            },
            "evidence_base": [],
            "counter_evidence": [],
            "gap_manifest_update": {
                "closed_gaps": [],
                "new_gaps": ["LLM analysis failed — manual review needed"],
                "data_available": [],
            },
        }


def _extract_authors(paper: dict[str, Any]) -> list[str]:
    """Extract author names from a Semantic Scholar paper dict."""
    authors = paper.get("authors") or []
    names = []
    for a in authors:
        if isinstance(a, dict):
            name = a.get("name")
            if name:
                names.append(name)
        elif isinstance(a, str):
            names.append(a)
    return names


def _validate_references(
    analysis: dict[str, Any],
    valid_ids: set[str],
    all_papers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Remove any hallucinated references that aren't in our paper set.

    Also enriches each validated reference with the real authors list from
    the Semantic Scholar response, so downstream consumers (e.g., the
    Research Brief Generator) can produce proper citations.
    """
    paper_index = {p.get("paperId"): p for p in all_papers}

    # Validate closest_existing_work
    novelty = analysis.get("novelty_assessment", {})
    closest = novelty.get("closest_existing_work", [])
    validated_closest = []
    for item in closest:
        pid = item.get("paper_id", "")
        if pid in valid_ids:
            # Ensure DOI matches reality
            real_paper = paper_index.get(pid, {})
            item["doi"] = _extract_doi(real_paper)
            item["title"] = real_paper.get("title", item.get("title", ""))
            item["authors"] = _extract_authors(real_paper)
            validated_closest.append(item)
        else:
            logger.warning("hallucinated_reference_removed", paper_id=pid, section="closest_existing_work")
    novelty["closest_existing_work"] = validated_closest
    analysis["novelty_assessment"] = novelty

    # Validate evidence_base
    validated_evidence = []
    for item in analysis.get("evidence_base", []):
        pid = item.get("paper_id", "")
        if pid in valid_ids:
            real_paper = paper_index.get(pid, {})
            item["doi"] = _extract_doi(real_paper)
            item["title"] = real_paper.get("title", item.get("title", ""))
            item["citation_count"] = real_paper.get("citationCount", 0) or 0
            item["authors"] = _extract_authors(real_paper)
            validated_evidence.append(item)
        else:
            logger.warning("hallucinated_reference_removed", paper_id=pid, section="evidence_base")
    analysis["evidence_base"] = validated_evidence

    # Validate counter_evidence
    validated_counter = []
    for item in analysis.get("counter_evidence", []):
        pid = item.get("paper_id", "")
        if pid in valid_ids:
            real_paper = paper_index.get(pid, {})
            item["doi"] = _extract_doi(real_paper)
            item["title"] = real_paper.get("title", item.get("title", ""))
            item["authors"] = _extract_authors(real_paper)
            validated_counter.append(item)
        else:
            logger.warning("hallucinated_reference_removed", paper_id=pid, section="counter_evidence")
    analysis["counter_evidence"] = validated_counter

    return analysis


def _check_kill_conditions(analysis: dict[str, Any]) -> Optional[str]:
    """Check if the hypothesis should be killed.

    Returns:
        Kill reason string if killed, None otherwise.
    """
    novelty = analysis.get("novelty_assessment", {})
    verdict = novelty.get("verdict", "")

    if verdict == "already_proven":
        reason = f"Hypothesis already proven in existing literature (novelty verdict: {verdict})"
        logger.warning("hypothesis_killed", reason=reason)
        return reason

    counter_evidence = analysis.get("counter_evidence", [])
    for item in counter_evidence:
        if item.get("severity") == "fatal":
            reason = f"Fatal counter-evidence: {item.get('finding', 'unknown')}"
            logger.warning("hypothesis_killed", reason=reason)
            return reason

    return None


async def literature_grounding_agent(input_data: GroundingInput) -> GroundingOutput:
    """Run the full Literature Grounding pipeline.

    Steps:
    1. Extract search queries from hypothesis via LLM
    2. Search Semantic Scholar for each query
    3. Analyze results via LLM
    4. Check kill conditions

    Args:
        input_data: Hypothesis, domains, mechanisms, keywords, gap_manifest.

    Returns:
        GroundingOutput with analysis results and optional kill_reason.
    """
    hypothesis = input_data["hypothesis"]
    domains = input_data["domains"]
    mechanisms = input_data["mechanisms"]
    gap_manifest = input_data.get("gap_manifest", {})

    ss_client = get_semantic_scholar_client()

    # Step 1: Extract search queries
    logger.info("step1_query_extraction", hypothesis=hypothesis[:100])
    queries = await _step1_extract_queries(hypothesis, domains, mechanisms)

    # Step 2: Search Semantic Scholar
    logger.info("step2_searching", query_count=len(queries))
    all_papers, papers_by_type = await _step2_search_papers(queries, ss_client)

    if not all_papers:
        logger.warning("no_papers_found", hypothesis=hypothesis[:100])
        return GroundingOutput(
            novelty_assessment={
                "score": 0.5,
                "closest_existing_work": [],
                "verdict": "novel",
            },
            evidence_base=[],
            counter_evidence=[],
            gap_manifest_update={
                "closed_gaps": [],
                "new_gaps": ["No papers found — domain may be too niche or queries need reformulation"],
                "data_available": [],
            },
            all_papers=[],
            search_queries=queries,
            kill_reason=None,
        )

    # Step 3: LLM Analysis
    logger.info("step3_analysis", paper_count=len(all_papers))
    analysis = await _step3_analyze(
        hypothesis, domains, mechanisms, all_papers, gap_manifest
    )

    # Step 4: Check kill conditions
    kill_reason = _check_kill_conditions(analysis)

    output = GroundingOutput(
        novelty_assessment=analysis.get("novelty_assessment", {}),
        evidence_base=analysis.get("evidence_base", []),
        counter_evidence=analysis.get("counter_evidence", []),
        gap_manifest_update=analysis.get("gap_manifest_update", {}),
        all_papers=[_format_paper_for_llm(p) for p in all_papers],
        search_queries=queries,
        kill_reason=kill_reason,
    )

    logger.info(
        "grounding_complete",
        novelty_score=output["novelty_assessment"].get("score"),
        novelty_verdict=output["novelty_assessment"].get("verdict"),
        evidence_count=len(output["evidence_base"]),
        counter_evidence_count=len(output["counter_evidence"]),
        kill_reason=kill_reason,
    )

    return output
