"""S11-B.4 — exemple de sortie compact dans le prompt protocole.

Couvre :

* l'exemple du prompt est du JSON valide une fois le gabarit formaté, avec
  exactement les mêmes clés qu'avant compaction — le contenu demandé ne change
  pas, seule sa mise en forme ;
* l'exemple ne contient plus d'indentation, et le prompt demande explicitement
  une sortie compacte ;
* le panel reçoit un protocole re-sérialisé par le code, jamais le texte brut
  du modèle : c'est la condition qui rend la compaction sans effet sur la
  suite du pipeline (vérification préalable B.4.1).

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s11_prompt
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base import load_prompt  # noqa: E402
from agents.multi_reviewer_panel import (  # noqa: E402
    _format_protocol_full,
    _format_protocol_summary,
)

#: Clés attendues, relevées sur l'exemple indenté d'avant compaction.
TOP_LEVEL_KEYS = {
    "protocol_title",
    "overall_timeline",
    "overall_budget_estimate",
    "phases",
    "phase_1_quick_start",
}
PHASE_KEYS = {
    "phase_number",
    "phase_name",
    "objective",
    "methodology",
    "required_resources",
    "expected_outputs",
    "success_criteria",
    "go_nogo_decision",
    "risks",
}
RESOURCE_KEYS = {
    "equipment",
    "software",
    "datasets",
    "competences",
    "estimated_cost",
    "estimated_duration",
}

FORMAT_ARGS: dict[str, str] = {
    "title": "T",
    "formal_statement": "F",
    "independent_variables": "IV",
    "dependent_variables": "DV",
    "mechanism": "M",
    "predictions": "P",
    "boundary_conditions": "B",
    "theoretical_framework": "TF",
    "evidence_base": "EB",
}


def example_block() -> str:
    """Extrait le bloc JSON du prompt protocole, gabarit formaté.

    Returns:
        Contenu du bloc ```json``` du prompt.
    """
    rendered = load_prompt("experimental_protocol").format(**FORMAT_ARGS)
    start = rendered.index("```json\n") + len("```json\n")
    return rendered[start : rendered.index("```", start)]


class CompactExampleTests(unittest.TestCase):
    """L'exemple reste complet, mais cesse de payer son indentation."""

    def setUp(self) -> None:
        self.block = example_block()
        self.data: dict[str, Any] = json.loads(self.block)

    def test_the_example_is_valid_json(self) -> None:
        self.assertIsInstance(self.data, dict)

    def test_the_schema_is_unchanged(self) -> None:
        self.assertEqual(set(self.data), TOP_LEVEL_KEYS)
        self.assertEqual([p["phase_number"] for p in self.data["phases"]], [1, 2, 3])
        for phase in self.data["phases"]:
            self.assertEqual(set(phase), PHASE_KEYS)
            self.assertEqual(set(phase["required_resources"]), RESOURCE_KEYS)

    def test_no_indentation_remains(self) -> None:
        for line in self.block.splitlines():
            self.assertFalse(line.startswith(" "), f"ligne indentée : {line[:60]}")
        # Comparaison à la sérialisation la plus serrée possible : les seuls
        # retours à la ligne tolérés sont ceux qui séparent les phases, pour
        # que l'exemple reste relisible en revue. Aucun espace de mise en
        # forme ne subsiste — un « : » suivi d'un espace peut encore
        # apparaître dans une valeur, pas entre une clé et sa valeur.
        tightest = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(self.block.replace("\n", ""), tightest)

    def test_the_prompt_asks_for_a_compact_output(self) -> None:
        prompt = load_prompt("experimental_protocol")
        self.assertIn("COMPACT", prompt)
        self.assertIn("sans indentation", prompt)


class PanelInputTests(unittest.TestCase):
    """B.4.1 — le panel lit un protocole reconstruit, pas du texte de modèle."""

    PROTOCOL: dict[str, Any] = {
        "protocol_title": "Protocole",
        "overall_timeline": "12 mois",
        "overall_budget_estimate": "100 k€",
        "phases": [
            {
                "phase_number": 1,
                "phase_name": "In silico",
                "objective": "Objectif",
                "methodology": "Méthode",
                "required_resources": {
                    "estimated_cost": "500 €",
                    "estimated_duration": "4 semaines",
                },
                "expected_outputs": ["Sortie"],
                "success_criteria": [],
                "go_nogo_decision": {"go_if": "A", "nogo_if": "B"},
                "risks": [],
            }
        ],
    }

    def test_the_panel_renders_the_protocol_from_its_fields(self) -> None:
        summary = _format_protocol_summary(self.PROTOCOL)
        full = _format_protocol_full(self.PROTOCOL)
        # Le rendu est construit champ par champ : il ne contient ni accolade
        # JSON ni guillemet de clé, donc aucune trace de la mise en forme du
        # modèle. Compacter la sortie ne change pas ce que le panel lit.
        for rendered in (summary, full):
            self.assertIn("Title: Protocole", rendered)
            self.assertIn("Phase 1: In silico", rendered)
            self.assertNotIn('"phase_number"', rendered)
            self.assertNotIn("{", rendered)

    def test_the_node_rebuilds_the_protocol_from_the_state(self) -> None:
        source = Path("graph/post_fire_pipeline.py").read_text(encoding="utf-8")
        self.assertIn('protocol = ProtocolOutput(**state["protocol"])', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
