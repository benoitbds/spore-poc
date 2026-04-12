"""Research Brief Generator for SPORE post-fire pipeline.

Compiles all pipeline outputs into a structured markdown document (4-6 pages)
and a JSON version for programmatic access.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

from agents.hypothesis_sharpening import SharpeningOutput
from agents.experimental_protocol import ProtocolOutput
from agents.multi_reviewer_panel import PanelOutput, ReviewerOutput, MetaReviewOutput
from config import get_settings
from logging_config import get_logger

logger = get_logger("research_brief_generator")


def _doi_link(doi: str | None) -> str:
    """Format DOI as a clickable link."""
    if doi:
        return f"[{doi}](https://doi.org/{doi})"
    return "N/A"


def _ref_line(paper: dict[str, Any], index: int) -> str:
    """Format a single reference line in academic citation style.

    Format: "Author1, Author2, et al. (year). *Title*. DOI: [...](...)"
    """
    authors = paper.get("authors", []) or []

    # Normalize authors to a list of name strings
    names: list[str] = []
    for a in authors:
        if isinstance(a, dict):
            name = a.get("name")
            if name:
                names.append(name)
        elif isinstance(a, str) and a:
            names.append(a)

    if names:
        # Show up to 2 authors then "et al." if more
        if len(names) <= 2:
            author_str = ", ".join(names)
        else:
            author_str = f"{names[0]}, {names[1]}, et al."
    else:
        author_str = "Unknown authors"

    doi = paper.get("doi")
    doi_str = f" DOI: [{doi}](https://doi.org/{doi})" if doi else ""
    year = paper.get("year", "?")
    title = paper.get("title", "Untitled")

    return f"[{index}] {author_str} ({year}). *{title}*.{doi_str}"


def generate_brief_markdown(
    brief_id: str,
    hypothesis: str,
    domains: list[str],
    grounding: dict[str, Any],
    sharpened: SharpeningOutput,
    protocol: ProtocolOutput,
    panel: PanelOutput,
) -> str:
    """Generate the full markdown brief.

    Args:
        brief_id: SPORE brief ID (SPR-2026-XXXX).
        hypothesis: Original hypothesis text.
        domains: Scientific domains.
        grounding: Literature Grounding output.
        sharpened: Hypothesis Sharpening output.
        protocol: Experimental Protocol output.
        panel: Panel review output.

    Returns:
        Complete markdown string.
    """
    novelty = grounding.get("novelty_assessment", {})
    meta = panel["meta_review"]
    reviews = panel["reviews"]
    today = date.today().isoformat()

    # Collect all referenced papers for the References section
    all_refs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for paper in grounding.get("evidence_base", []):
        pid = paper.get("paper_id", "")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            all_refs.append(paper)

    for paper in grounding.get("counter_evidence", []):
        pid = paper.get("paper_id", "")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            all_refs.append(paper)

    for paper in novelty.get("closest_existing_work", []):
        pid = paper.get("paper_id", "")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            all_refs.append(paper)

    # ── Build markdown ───────────────────────────────────────

    md = []

    # Title
    md.append(f"# {sharpened['title']}\n")

    # Metadata
    md.append("## Metadata\n")
    md.append(f"- **SPORE ID**: {brief_id}")
    md.append(f"- **Domaines**: {' x '.join(domains)}")
    md.append(f"- **Date de generation**: {today}")
    md.append(f"- **Panel consensus score**: {meta['consensus_score']:.1f}/10")
    md.append(f"- **Novelty score**: {novelty.get('score', 'N/A')}/1.0")
    md.append(f"- **Panel verdict**: {meta['verdict']}")
    md.append("")

    # Abstract — generated from formal statement + mechanism
    md.append("## Abstract\n")
    md.append(sharpened["formal_statement"])
    md.append("")
    mechanism = sharpened["proposed_mechanism"]
    chain = mechanism.get("causal_chain", [])
    if chain:
        md.append(f"The proposed mechanism involves {len(chain)} causal steps: "
                   + " -> ".join(f"({i+1}) {step.split(':')[-1].strip()[:80]}" for i, step in enumerate(chain[:4]))
                   + ".")
    md.append("")
    md.append(f"Literature grounding on {len(all_refs)} verified references yields a novelty score of "
              f"{novelty.get('score', 'N/A')} ({novelty.get('verdict', 'N/A')}). "
              f"A {len(protocol['phases'])}-phase experimental protocol (budget: {protocol['overall_budget_estimate']}, "
              f"timeline: {protocol['overall_timeline']}) is proposed, starting with in silico validation. "
              f"A panel of 5 expert reviewers reached a consensus score of {meta['consensus_score']:.1f}/10.")
    md.append("")

    # Section 1 — Hypothesis & mechanism
    md.append("## 1. Hypothese et mecanisme propose\n")

    md.append("### 1.1 Formulation formelle\n")
    md.append(sharpened["formal_statement"])
    md.append("")

    md.append("### 1.2 Variables\n")
    md.append("**Variables independantes :**\n")
    md.append("| Variable | Type | Plage | Unite |")
    md.append("|----------|------|-------|-------|")
    for v in sharpened["independent_variables"]:
        md.append(f"| {v.get('name', '?')} | {v.get('type', '?')} | {v.get('range', '?')} | {v.get('unit', '?')} |")
    md.append("")
    md.append("**Variables dependantes :**\n")
    md.append("| Variable | Type | Direction attendue | Unite |")
    md.append("|----------|------|-------------------|-------|")
    for v in sharpened["dependent_variables"]:
        md.append(f"| {v.get('name', '?')} | {v.get('type', '?')} | {v.get('expected_direction', '?')} | {v.get('unit', '?')} |")
    md.append("")

    md.append("### 1.3 Chaine causale\n")
    for step in mechanism.get("causal_chain", []):
        md.append(f"1. {step}")
    md.append("")

    md.append("**Hypotheses cles :**\n")
    for a in mechanism.get("key_assumptions", []):
        md.append(f"- {a}")
    md.append("")

    md.append("**Inconnues identifiees :**\n")
    for u in mechanism.get("known_unknowns", []):
        md.append(f"- {u}")
    md.append("")

    md.append("### 1.4 Conditions aux limites\n")
    for c in sharpened["boundary_conditions"]:
        md.append(f"- **{c.get('condition', '?')}** — {c.get('justification', '')}")
    md.append("")

    md.append("### 1.5 Cadre theorique\n")
    md.append(sharpened["theoretical_framework"])
    md.append("")

    # Section 2 — State of the art
    md.append("## 2. Etat de l'art et positionnement\n")

    md.append("### 2.1 Travaux les plus proches\n")
    closest = novelty.get("closest_existing_work", [])
    if closest:
        for w in closest:
            doi_str = f" — {_doi_link(w.get('doi'))}" if w.get("doi") else ""
            md.append(f"- **[{w.get('year', '?')}] {w.get('title', '?')}**{doi_str}")
            md.append(f"  - Similarite: {w.get('similarity', '?')}")
            md.append(f"  - Difference cle: {w.get('key_difference', '?')}")
    else:
        md.append("Aucun travail tres proche identifie.")
    md.append("")

    md.append("### 2.2 Base de preuves\n")
    evidence = grounding.get("evidence_base", [])
    if evidence:
        for p in evidence:
            doi_str = f" — {_doi_link(p.get('doi'))}" if p.get("doi") else ""
            md.append(f"- **[{p.get('year', '?')}] {p.get('title', '?')}**{doi_str}")
            md.append(f"  - Type: {p.get('support_type', '?')} | Citations: {p.get('citation_count', '?')}")
            md.append(f"  - {p.get('relevance', '')}")
    else:
        md.append("Aucune preuve directe trouvee.")
    md.append("")

    md.append("### 2.3 Contre-preuves et limitations connues\n")
    counter = grounding.get("counter_evidence", [])
    if counter:
        for p in counter:
            md.append(f"- **[{p.get('severity', '?')}] {p.get('title', '?')}**")
            md.append(f"  - {p.get('finding', '')}")
    else:
        md.append("Aucune contre-preuve identifiee.")
    md.append("")

    md.append("### 2.4 Evaluation de nouveaute\n")
    md.append(f"- **Score**: {novelty.get('score', '?')}/1.0")
    md.append(f"- **Verdict**: {novelty.get('verdict', '?')}")
    md.append("")

    # Section 3 — Falsifiable predictions
    md.append("## 3. Predictions falsifiables\n")
    md.append("| # | Prediction | Borne quantitative | Methode | H0 | Test statistique |")
    md.append("|---|-----------|-------------------|---------|-----|-----------------|")
    for i, p in enumerate(sharpened["falsifiable_predictions"], 1):
        pred = p.get("prediction", "?").replace("|", "/")
        bound = p.get("quantitative_bound", "?").replace("|", "/")
        method = p.get("measurement_method", "?").replace("|", "/")
        h0 = p.get("null_hypothesis", "?").replace("|", "/")
        test = p.get("statistical_test", "?").replace("|", "/")
        md.append(f"| {i} | {pred} | {bound} | {method} | {h0} | {test} |")
    md.append("")

    # Section 4 — Experimental protocol
    md.append("## 4. Protocole experimental\n")
    md.append(f"**Timeline globale**: {protocol['overall_timeline']}")
    md.append(f"**Budget global**: {protocol['overall_budget_estimate']}")
    md.append("")

    for phase in protocol["phases"]:
        pn = phase.get("phase_number", "?")
        name = phase.get("phase_name", "?")
        md.append(f"### 4.{pn} Phase {pn} — {name}\n")
        md.append(f"**Objectif**: {phase.get('objective', '?')}")
        md.append("")
        md.append(f"**Methodologie**: {phase.get('methodology', '?')}")
        md.append("")
        res = phase.get("required_resources", {})
        md.append(f"- Cout: {res.get('estimated_cost', '?')}")
        md.append(f"- Duree: {res.get('estimated_duration', '?')}")
        if res.get("equipment"):
            md.append(f"- Equipement: {', '.join(res['equipment'])}")
        if res.get("software"):
            md.append(f"- Logiciels: {', '.join(res['software'])}")
        md.append("")

        criteria = phase.get("success_criteria", [])
        if criteria:
            md.append("**Criteres de succes :**\n")
            for c in criteria:
                md.append(f"- {c.get('metric', '?')}: {c.get('threshold', '?')}")
            md.append("")

        gonogo = phase.get("go_nogo_decision", {})
        md.append(f"- **GO**: {gonogo.get('go_if', '?')}")
        md.append(f"- **NO-GO**: {gonogo.get('nogo_if', '?')}")
        md.append(f"- **PIVOT**: {gonogo.get('pivot_if', '?')}")
        md.append("")

    qs = protocol["phase_1_quick_start"]
    md.append("### 4.4 Quick start : comment demarrer aujourd'hui\n")
    md.append(f"- **Peut demarrer maintenant**: {'Oui' if qs.get('can_start_today') else 'Non'}")
    md.append(f"- **Premiere action**: {qs.get('first_action', '?')}")
    if qs.get("tools_needed"):
        md.append(f"- **Outils**: {', '.join(qs['tools_needed'])}")
    if qs.get("open_data_sources"):
        md.append(f"- **Donnees ouvertes**: {', '.join(qs['open_data_sources'])}")
    md.append("")

    # Section 5 — Impact analysis (from reviewers)
    md.append("## 5. Analyse d'impact\n")

    # Find industrialist and funding strategist reviews
    industrialist_review = next((r for r in reviews if r["reviewer_persona"] == "industrialist"), None)
    funding_review = next((r for r in reviews if r["reviewer_persona"] == "funding_strategist"), None)

    md.append("### 5.1 Impact scientifique\n")
    md.append(f"Novelty score: {novelty.get('score', '?')}/1.0 ({novelty.get('verdict', '?')})")
    md.append("")

    md.append("### 5.2 Applications industrielles et marche\n")
    if industrialist_review:
        md.append(f"**Score industriel**: {industrialist_review['overall_score']}/10")
        md.append("")
        md.append("**Forces :**")
        for s in industrialist_review["strengths"]:
            md.append(f"- {s}")
        md.append("")
        md.append("**Faiblesses :**")
        for w in industrialist_review["weaknesses"]:
            md.append(f"- {w}")
        md.append("")
        md.append(f"**Recommandation**: {industrialist_review['recommendation']}")
    md.append("")

    md.append("### 5.3 Opportunites de financement\n")
    if funding_review:
        funding_programs = funding_review.get("funding_programs", [])  # type: ignore[typeddict-item]
        if funding_programs:
            md.append("| Programme | Agence | Fit | Budget type | Taux succes |")
            md.append("|-----------|--------|-----|-------------|-------------|")
            for fp in funding_programs:
                md.append(f"| {fp.get('program', '?')} | {fp.get('agency', '?')} | "
                          f"{fp.get('fit_score', '?')} | {fp.get('typical_budget', '?')} | "
                          f"{fp.get('success_rate', '?')} |")
            md.append("")
            for fp in funding_programs:
                md.append(f"- **{fp.get('program', '?')}** ({fp.get('agency', '?')}): {fp.get('rationale', '')}")
        md.append("")
        md.append(f"**Recommandation financement**: {funding_review['recommendation']}")
    md.append("")

    # Section 6 — Panel review summary
    md.append("## 6. Panel Review Summary\n")
    md.append("| Reviewer | Score | Verdict | Point cle |")
    md.append("|----------|-------|---------|-----------|")
    for r in reviews:
        key_point = r["strengths"][0] if r["strengths"] else r["recommendation"][:60]
        md.append(f"| {r['reviewer_persona']} | {r['overall_score']}/10 | {r['verdict']} | {key_point} |")
    md.append("")

    md.append(f"**Consensus score**: {meta['consensus_score']:.1f}/10")
    md.append(f"**Verdict final**: {meta['verdict']}")
    md.append("")

    md.append("### 6.1 Consensus\n")
    for c in meta["key_consensus"]:
        md.append(f"- {c}")
    md.append("")

    md.append("### 6.2 Points de desaccord\n")
    if meta["key_disagreements"]:
        for d in meta["key_disagreements"]:
            md.append(f"- {d}")
    else:
        md.append("Aucun desaccord majeur.")
    md.append("")

    md.append("### 6.3 Critical path\n")
    md.append(meta["critical_path"])
    md.append("")

    md.append(f"**Recommandation finale**: {meta['final_recommendation']}")
    md.append("")

    # Section 7 — Gap manifest
    md.append("## 7. Gap Manifest residuel\n")
    gap_update = grounding.get("gap_manifest_update", {})

    md.append("### 7.1 Data gaps\n")
    new_gaps = gap_update.get("new_gaps", [])
    if new_gaps:
        for g in new_gaps:
            md.append(f"- {g}")
    else:
        md.append("Aucun gap de donnees residuel.")
    md.append("")

    md.append("### 7.2 Competence gaps\n")
    # Inferred from protocol resources
    for phase in protocol["phases"]:
        res = phase.get("required_resources", {})
        comps = res.get("competences", [])
        for c in comps:
            md.append(f"- Phase {phase.get('phase_number', '?')}: {c}")
    if not any(phase.get("required_resources", {}).get("competences") for phase in protocol["phases"]):
        md.append("Voir les competences requises dans le protocole ci-dessus.")
    md.append("")

    md.append("### 7.3 Epistemic gaps\n")
    for u in mechanism.get("known_unknowns", []):
        md.append(f"- {u}")
    md.append("")

    # References
    md.append("## References\n")
    for i, ref in enumerate(all_refs, 1):
        md.append(_ref_line(ref, i))
    md.append("")

    # Annexes
    md.append("## Annexes\n")

    md.append("### A. Detailed Reviewer Reports\n")
    for r in reviews:
        md.append(f"#### {r['reviewer_persona'].replace('_', ' ').title()}\n")
        md.append(f"- **Score**: {r['overall_score']}/10 | **Verdict**: {r['verdict']} | **Confidence**: {r['confidence']}")
        md.append(f"- **Strengths**: {'; '.join(r['strengths'])}")
        md.append(f"- **Weaknesses**: {'; '.join(r['weaknesses'])}")
        md.append(f"- **Questions**: {'; '.join(r['critical_questions'])}")
        md.append(f"- **Recommendation**: {r['recommendation']}")
        md.append("")

    md.append("### B. Semantic Scholar Search Queries\n")
    queries = grounding.get("search_queries", [])
    for q in queries:
        md.append(f"- [{q.get('type', '?')}] `{q.get('query', '?')}` — {q.get('rationale', '')}")
    md.append("")

    md.append("---\n")
    md.append(f"*Generated by SPORE (Systeme de Production d'Opportunites de Recherche par Exploration) on {today}*")

    return "\n".join(md)


def generate_brief_json(
    brief_id: str,
    hypothesis: str,
    domains: list[str],
    grounding: dict[str, Any],
    sharpened: SharpeningOutput,
    protocol: ProtocolOutput,
    panel: PanelOutput,
) -> dict[str, Any]:
    """Generate the structured JSON brief.

    Returns:
        Dict suitable for JSON serialization.
    """
    return {
        "brief_id": brief_id,
        "generated_at": date.today().isoformat(),
        "domains": domains,
        "original_hypothesis": hypothesis,
        "grounding": {
            "novelty_assessment": grounding.get("novelty_assessment", {}),
            "evidence_base": grounding.get("evidence_base", []),
            "counter_evidence": grounding.get("counter_evidence", []),
            "gap_manifest_update": grounding.get("gap_manifest_update", {}),
            "search_queries": grounding.get("search_queries", []),
        },
        "sharpened": dict(sharpened),
        "protocol": dict(protocol),
        "panel": {
            "reviews": [dict(r) for r in panel["reviews"]],
            "meta_review": dict(panel["meta_review"]),
        },
    }


async def save_brief(
    brief_id: str,
    hypothesis: str,
    domains: list[str],
    grounding: dict[str, Any],
    sharpened: SharpeningOutput,
    protocol: ProtocolOutput,
    panel: PanelOutput,
) -> tuple[Path, Path]:
    """Generate and save the brief as markdown and JSON.

    Args:
        brief_id: SPORE brief ID.
        hypothesis: Original hypothesis.
        domains: Domains.
        grounding: Literature grounding output.
        sharpened: Sharpened hypothesis.
        protocol: Experimental protocol.
        panel: Panel review output.

    Returns:
        Tuple of (markdown_path, json_path).
    """
    settings = get_settings()
    briefs_dir = settings.output_dir / "briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)

    # Generate markdown
    md_content = generate_brief_markdown(
        brief_id, hypothesis, domains, grounding, sharpened, protocol, panel
    )
    md_path = briefs_dir / f"{brief_id}.md"
    md_path.write_text(md_content, encoding="utf-8")
    logger.info("brief_markdown_saved", path=str(md_path))

    # Generate JSON
    json_content = generate_brief_json(
        brief_id, hypothesis, domains, grounding, sharpened, protocol, panel
    )
    json_path = briefs_dir / f"{brief_id}.json"
    json_path.write_text(json.dumps(json_content, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("brief_json_saved", path=str(json_path))

    return md_path, json_path
