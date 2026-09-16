"""S11-B.3 — plafonds de sortie : une seule source, bornée et visible.

Couvre :

* le plafond vient du genome, borné entre le plancher du nœud et
  ``MAX_TOKENS_CEILING`` ;
* une valeur hors bornes est ramenée ET signalée (``max_tokens_clamped``) :
  une mutation L1 sous le plancher ne passe pas en silence ;
* le genome livré ne déclenche aucun bornage — sinon l'avertissement
  deviendrait du bruit permanent et cesserait d'alerter ;
* plus aucun plafond n'est codé en dur aux sites d'appel des nœuds, et le
  commentaire erroné sur la limite 8192 de DeepSeek a disparu.

Usage:
    cd /home/baq/Projects/spore-poc
    .venv/bin/python -m tests.test_s11_limits
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import limits  # noqa: E402
from llm.limits import (  # noqa: E402
    MAX_TOKENS_CEILING,
    MAX_TOKENS_FLOORS,
    max_tokens_for,
)

#: Nœuds dont le plafond a été relevé par S11-A, avec leur plancher.
RAISED_NODES = {
    "experimental_protocol": 16000,
    "literature_grounding": 16000,
    "reviewer_methodologist": 4000,
    "reviewer_domain_expert": 4000,
    "reviewer_contrarian": 4000,
    "reviewer_industrialist": 4000,
    "reviewer_funding_strategist": 4000,
    "meta_reviewer": 4000,
}


def with_genome(value: Any) -> Any:
    """Remplace la lecture du genome par une valeur fixe.

    Args:
        value: Valeur rendue pour ``max_tokens``, ``None`` pour « absent ».

    Returns:
        Gestionnaire de contexte de ``mock.patch``.
    """
    return mock.patch.object(limits, "genome_max_tokens", return_value=value)


def captured() -> Any:
    """Capture les événements journalisés.

    Returns:
        Gestionnaire de contexte rendant la liste des événements.
    """
    return structlog.testing.capture_logs()


class BoundsTests(unittest.TestCase):
    """Le genome propose, les bornes disposent."""

    def test_a_value_inside_the_bounds_is_used_as_is(self) -> None:
        with with_genome(20000), captured() as events:
            self.assertEqual(max_tokens_for("experimental_protocol"), 20000)
        self.assertEqual([e for e in events if e["event"] == "max_tokens_clamped"], [])

    def test_a_value_below_the_floor_is_raised_and_reported(self) -> None:
        with with_genome(2000), captured() as events:
            self.assertEqual(max_tokens_for("experimental_protocol"), 16000)
        clamped = [e for e in events if e["event"] == "max_tokens_clamped"]
        self.assertEqual(len(clamped), 1)
        self.assertEqual(clamped[0]["configured"], 2000)
        self.assertEqual(clamped[0]["applied"], 16000)
        self.assertEqual(clamped[0]["log_level"], "warning")

    def test_a_value_above_the_ceiling_is_lowered_and_reported(self) -> None:
        with with_genome(100000), captured() as events:
            self.assertEqual(max_tokens_for("experimental_protocol"), MAX_TOKENS_CEILING)
        self.assertEqual(len([e for e in events if e["event"] == "max_tokens_clamped"]), 1)

    def test_a_missing_genome_value_falls_back_to_the_floor(self) -> None:
        with with_genome(None):
            self.assertEqual(max_tokens_for("meta_reviewer"), 4000)

    def test_an_unknown_node_is_reported_rather_than_guessed(self) -> None:
        with with_genome(None), captured() as events:
            value = max_tokens_for("noeud_inexistant")
        self.assertEqual(value, limits.DEFAULT_FLOOR)
        self.assertTrue(any(e["event"] == "max_tokens_node_unknown" for e in events))

    def test_every_floor_stays_under_the_ceiling(self) -> None:
        for node, floor in MAX_TOKENS_FLOORS.items():
            self.assertLessEqual(floor, MAX_TOKENS_CEILING, node)


class ShippedGenomeTests(unittest.TestCase):
    """Le genome livré est cohérent avec les planchers."""

    def test_no_clamping_with_the_shipped_genome(self) -> None:
        with captured() as events:
            values = {node: max_tokens_for(node) for node in MAX_TOKENS_FLOORS}
        clamped = [e for e in events if e["event"] == "max_tokens_clamped"]
        self.assertEqual(clamped, [], f"bornage résiduel : {clamped}")
        for node, floor in RAISED_NODES.items():
            self.assertGreaterEqual(values[node], floor, node)

    def test_the_protocol_ceiling_doubles_within_the_ceiling(self) -> None:
        # Le rejeu de complete_json double une fois : 16000 → 32000, soit
        # exactement le plafond. Au-delà, il n'y aurait plus de marge.
        self.assertEqual(max_tokens_for("experimental_protocol") * 2, MAX_TOKENS_CEILING)


class CallSiteTests(unittest.TestCase):
    """Plus de plafond codé en dur dans les nœuds."""

    NODE_MODULES = (
        "agents/experimental_protocol.py",
        "agents/literature_grounding.py",
        "agents/hypothesis_sharpening.py",
        "agents/multi_reviewer_panel.py",
        "agents/vulgarization.py",
        "agents/critic.py",
        "agents/gate.py",
        "agents/synthesis.py",
        "agents/impact.py",
        "agents/reviewer.py",
        "agents/stub_brief.py",
        "agents/l1_critic.py",
        "agents/l1_strategist.py",
    )

    def test_no_literal_max_tokens_remains(self) -> None:
        for path in self.NODE_MODULES:
            source = Path(path).read_text(encoding="utf-8")
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("max_tokens=") and "max_tokens_for(" not in stripped:
                    self.fail(f"{path} : plafond codé en dur — {stripped}")

    def test_the_false_deepseek_limit_comment_is_gone(self) -> None:
        source = Path("agents/experimental_protocol.py").read_text(encoding="utf-8")
        self.assertNotIn("[1, 8192]", source)
        self.assertNotIn("staying under the provider cap", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
