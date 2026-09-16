"""Paquet de tests SPORE — isole la base de données de la production.

Depuis S11/B.1, tout appel LLM écrit une ligne dans ``llm_calls``. Les doubles
de test passent par la vraie couche client, donc les tests qui ne redirigeaient
pas ``SPORE_DB_PATH`` eux-mêmes ont commencé à écrire dans ``data/spore.db`` :
378 lignes de mesure factice y sont apparues en une seule session.

Ce module, importé avant tout module de test par ``python -m tests.<module>``,
redirige la base et le répertoire de sortie vers un dossier temporaire tant que
l'environnement ne les fixe pas déjà. Les tests qui gèrent leur propre
environnement (``TempEnvironment``, ``TempDatabase``) continuent de le faire :
ils surchargent ces valeurs.

Pour exercer la vraie base — les scripts de calibration, qui appellent l'API en
conditions réelles — poser ``SPORE_TEST_USE_REAL_DB=1``.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

if os.environ.get("SPORE_TEST_USE_REAL_DB") != "1":
    _sandbox = tempfile.mkdtemp(prefix="spore-tests-")
    os.environ["SPORE_DB_PATH"] = os.path.join(_sandbox, "spore.db")
    os.environ.setdefault("SPORE_OUTPUT_DIR", os.path.join(_sandbox, "outputs"))
    atexit.register(shutil.rmtree, _sandbox, True)
