# S11 — B.0.6 : les traductions déjà produites sont-elles tronquées ?

Relevé en lecture seule, 16/09/2026, sur les quatre lots de S10-B et S10-C
(37 briefs distincts, lots disjoints). Condition posée par l'utilisateur :
S10-C ne reprend pas avant ce relevé.

**Réponse : non. 3 114 textes traduits comparés champ par champ — zéro fin
abrupte, zéro liste raccourcie, zéro ratio hors bande.**

## Pourquoi ce négatif est crédible

Un audit qui ne trouve rien doit d'abord prouver qu'il regardait au bon
endroit. Deux garanties :

1. **Calibrage indépendant.** Les statistiques mesurées ici sur S10-B
   reproduisent au chiffre près celles inscrites en commentaire dans
   `scripts/translate_brief_panel.py` l. 664, calculées à l'époque par un
   autre code : EN→FR n=374, médiane 1,20, p1 1,03, p99 1,42, bornes
   0,91-1,48 ; FR→EN n=530, bornes 0,73-1,11. L'appariement des cartes se
   fait par `reviewer_persona` et non par index : un négatif dû à un défaut
   de pairage est écarté.
2. **Les angles morts ont été ciblés.** Les listes sont traduites en un bloc
   séparé par `---`, et un décompte divergent déclenche déjà un repli par
   item : une coupure avant un `---` était rattrapée. Restaient deux cas sans
   garde — **le dernier élément d'une liste** et **les champs string**
   (`recommendation`, `critical_path`, `final_recommendation`, `max_tokens`
   2500). Ce sont précisément ceux qui ont été contrôlés.

## Périmètre et distributions

| Lot | EN→FR | FR→EN | Vulgarisation régénérée |
|---|---|---|---|
| S10-B (9 briefs) | 374 | 611 | 81 |
| S10-C lot 1 (8) | 132 | 470 | 72 |
| S10-C lot 2 (10) | 164 | 591 | 90 |
| S10-C lot 3 (10) | 121 | 651 | 90 |
| **Total** | **791** | **2 323** | **333** |

| Direction | Type | n | min | p10 | méd | p90 | max |
|---|---|---|---|---|---|---|---|
| EN→FR | carte reviewer | 745 | 0,91 | 1,11 | 1,19 | 1,29 | 1,48 |
| EN→FR | meta-review | 46 | 1,05 | 1,07 | 1,18 | 1,28 | 1,33 |
| FR→EN | carte reviewer | 1 652 | 0,73 | 0,84 | 0,91 | 0,98 | 1,15 |
| FR→EN | meta-review | 338 | 0,80 | 0,87 | 0,94 | 1,01 | 1,17 |
| FR→EN | vulgarisation | 333 | 0,71 | 0,84 | 0,92 | 1,00 | 1,52 |

Aucun champ source ne fait moins de 40 caractères : aucun percentile n'est
biaisé par un petit dénominateur. Portée réelle du EN→FR : 81 cartes anglaises
et 6 meta-reviews anglaises (toutes en S10-B) ; **aucune carte déjà française
n'a été modifiée** dans aucun lot.

## Couverture négative, explicitement

- **Fins de texte** : 0/791 cible EN→FR sans ponctuation forte. 31/2 323 en
  FR→EN, et les 31 reproduisent une source elle aussi sans ponctuation (30
  `title_fr`, plus une question terminée par une parenthèse complète).
- **Listes** : 666 listes EN→FR et 666 FR→EN appariées, **0** avec un nombre
  d'éléments différent.
- **Délimiteurs** : 0 cible déséquilibrée sur une source équilibrée.
- **Cellules NULL** : aucune, sur 37 briefs × 5 colonnes.

Cas les plus limites, lus en plein texte : `SPR-2026-C7A9` industrialist
strengths[0] à 0,73 (l'anglais est simplement plus compact, aucune proposition
perdue) ; `SPR-2026-1BA4` funding_strategist critical_questions[1], seule cible
longue sans ponctuation terminale, qui finit sur une parenthèse complète après
le point d'interrogation, comme sa source.

## Angle mort levé : le brief rejoué deux fois

`SPR-2026-4B85` figure dans S10-B **et** dans `s10c-20260915T074941Z` : une
troncature du premier rejeu écrasée par le second serait invisible à une
comparaison en deux temps. Comparaison faite **en trois temps** (avant S10-B →
état intermédiaire → actuel) sur `panel_data` : 47 textes modifiés, 0 fin
abrupte et 0 liste raccourcie aux deux étapes. Le seul état intermédiaire
recouvrable de tout l'audit est sain.

## Ce qui a réellement perdu de la matière : la vulgarisation

`vulgarization_data` avant/après n'est **pas** une traduction : les deux côtés
sont français, le rejeu l'a **régénérée** — conformément à la décision de
S10-B de ne pas réinjecter l'ancienne vulgarisation dans l'état reconstruit.
Plusieurs champs rétrécissent nettement :

| Brief | Champ | Avant | Après | Ratio |
|---|---|---|---|---|
| `SPR-2026-66E7` | `concretely.phase1` | 368 | 185 | 0,50 |
| `SPR-2026-FBF3` | `concretely.phase1` | 311 | 163 | 0,52 |
| `SPR-2026-816D` | `title_fr` | 103 | 49 | 0,48 |

**Toutes ces cibles se terminent sur une phrase complète** : c'est du contenu
réécrit plus court, pas coupé. La distribution complète de la régénération va
de 0,48 à 1,97, médiane 0,99. C'est le seul endroit du corpus où de la matière
a disparu, et c'est un effet voulu du rejeu, pas une troncature. À rouvrir si
l'on tient à la longueur de la vulgarisation d'origine.

## Conséquence

La condition posée à la reprise de S10-C est levée : aucune des 28 traductions
déjà produites n'est tronquée. Les lots 4, 5 et le lot final (4469, FBCA,
A2C5 — 6FEB retiré par la quarantaine) restent en pause pour la seule raison
qui subsiste : B.6 n'a pas eu lieu.
