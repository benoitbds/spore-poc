"""Tests du parseur JSON partagé (C17b).

Usage:
    cd /home/baq/Projects/spore-poc
    python -m tests.test_json_parse

Deux familles :
  * les classes d'échec réellement observées en production, reconstruites à
    partir des messages d'erreur de /var/log/spore.log ;
  * les cas limites de la réparation, dont celui qui sépare un scanner correct
    d'une regex naïve : une virgule suivie d'une accolade À L'INTÉRIEUR d'une
    chaîne.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.json_parse import _repair, extract_json  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(f"{name}{' — ' + detail if detail else ''}")
    print(f"  {'✓' if condition else '✗'} {name}{'  ' + detail if detail and not condition else ''}")


def expect_ok(name: str, payload: str, level: str, **expected) -> None:
    try:
        data, got_level = extract_json(payload, agent="test")
    except json.JSONDecodeError as exc:
        check(name, False, f"a levé : {exc}")
        return
    if got_level != level:
        check(name, False, f"niveau attendu {level!r}, obtenu {got_level!r}")
        return
    for key, value in expected.items():
        if data.get(key) != value:
            check(name, False, f"{key}={data.get(key)!r}, attendu {value!r}")
            return
    check(name, True)


def expect_raise(name: str, payload: str) -> None:
    try:
        extract_json(payload, agent="test")
    except json.JSONDecodeError:
        check(name, True)
        return
    check(name, False, "aurait dû lever")


print("\n── Classes d'échec observées en production ──")

# vulgarization_parse_failed x3 : « Expecting property name enclosed in double
# quotes » — fence markdown + virgule finale avant }. Le défaut V4.
expect_ok(
    "fence ```json + virgule finale avant }",
    '```json\n{\n  "title_fr": "Fourmis jardinières",\n  "score": 3,\n}\n```',
    "repaired",
    title_fr="Fourmis jardinières",
    score=3,
)

# json_parse_failed : « Expecting value » — virgule finale avant ]
expect_ok(
    "virgule finale avant ]",
    '{"queries": ["a", "b",], "n": 2}',
    "repaired",
    n=2,
)

# json_parse_failed : « Invalid control character at: » — saut de ligne nu
# au milieu d'un littéral de chaîne.
expect_ok(
    "saut de ligne nu dans une chaîne",
    '{"mechanism": "ligne un\nligne deux", "ok": true}',
    "repaired",
    mechanism="ligne un\nligne deux",
    ok=True,
)

expect_ok(
    "tabulation nue dans une chaîne",
    '{"s": "avant\tapres"}',
    "repaired",
    s="avant\tapres",
)

print("\n── Le cas qui sépare un scanner d'une regex ──")

# Une regex ,\s*} corromprait le littéral "trailing, }".
TRAP = '{"note": "trailing, }", "n": 1,}'
expect_ok("virgule + } DANS une chaîne : une seule virgule retirée", TRAP,
          "repaired", note="trailing, }", n=1)

repaired, ops = _repair(TRAP)
check("la chaîne piège est rendue intacte", '"trailing, }"' in repaired,
      f"obtenu : {repaired}")
check("une seule réparation déclarée", ops == ["trailing_comma"], f"ops={ops}")

# Variante : la chaîne contient une virgule suivie de ] .
expect_ok(
    "virgule + ] dans une chaîne",
    '{"note": "a, ] b", "xs": [1, 2,]}',
    "repaired",
    note="a, ] b",
)

# Guillemet échappé juste avant le piège : l'état de chaîne doit tenir.
expect_ok(
    "guillemet échappé puis virgule finale",
    r'{"q": "il a dit \"bonjour, }\"", "n": 2,}',
    "repaired",
    q='il a dit "bonjour, }"',
    n=2,
)

# Antislash échappé en fin de chaîne : la chaîne se ferme bien.
expect_ok(
    "antislash échappé en fin de chaîne",
    r'{"path": "C:\\", "n": 1,}',
    "repaired",
    path="C:\\",
    n=1,
)

print("\n── Texte parasite et fences ──")

expect_ok("préambule avant l'objet",
          'Voici le JSON demandé :\n{"a": 1}', "direct", a=1)
expect_ok("commentaire après l'objet",
          '{"a": 1}\n\nJ\'espère que cela convient.', "direct", a=1)
expect_ok("préambule ET postambule",
          'Bien sûr !\n{"a": 1}\nDis-moi si tu veux autre chose.', "direct", a=1)
expect_ok("fence nue sans étiquette",
          '```\n{"a": 1}\n```', "direct", a=1)
expect_ok("fence de fermeture manquante (réponse tronquée au plafond)",
          '```json\n{"a": 1}', "direct", a=1)
expect_ok("accolade dans une chaîne avant l'objet réel",
          '{"tpl": "utilise {ceci}", "a": 1}', "direct", a=1)

print("\n── Le parseur ne complète jamais ──")

expect_raise("objet tronqué : lève, ne complète pas",
             '{"a": 1, "b": {"c":')
expect_raise("texte sans JSON : lève",
             "Je ne peux pas répondre à cette demande.")
expect_raise("réponse vide : lève", "")
expect_raise("tableau au lieu d'un objet : lève", '[1, 2, 3]')
expect_raise("scalaire au lieu d'un objet : lève", '"just a string"')

# Une virgule finale n'est pas un permis d'inventer des clés.
data, _ = extract_json('{"a": 1,}', agent="test")
check("aucune clé ajoutée par la réparation", set(data) == {"a"}, f"clés={set(data)}")

print("\n── Non-régression : ce que les 5 copies faisaient déjà ──")

expect_ok("JSON nu valide", '{"a": 1}', "direct", a=1)
expect_ok("fence ```json valide", '```json\n{"a": 1}\n```', "direct", a=1)
expect_ok("JSON déjà valide : aucune réparation",
          '{"note": "trailing, }", "n": 1}', "direct", n=1)

print(f"\n{'─' * 60}")
print(f"{len(PASSED)} réussis, {len(FAILED)} échoués")
for f in FAILED:
    print(f"  ÉCHEC : {f}")
sys.exit(1 if FAILED else 0)
