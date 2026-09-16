"""Erreurs de génération LLM : la sortie n'est pas exploitable telle quelle.

Ces exceptions ne signalent pas une panne du fournisseur — l'appel a réussi,
la réponse est arrivée, mais elle s'est arrêtée autrement qu'en fin de
génération. Elles existent pour que le pipeline échoue franchement (fail
closed) au lieu de parser, réparer ou publier un texte coupé.

Séparées de ``llm.client`` pour que les sites d'appel puissent les attraper
sans importer les clients (et leurs dépendances SDK).
"""

from __future__ import annotations


class LLMCallError(Exception):
    """Base des erreurs portant sur le contenu d'une réponse LLM.

    Attributes:
        node: Nœud appelant (``experimental_protocol``, ``reviewer_contrarian``…).
        provider: Fournisseur ayant servi l'appel.
        model: Modèle renvoyé par l'API (et non le nom configuré).
        finish_reason: Motif de fin de génération, normalisé.
        max_tokens: Plafond demandé pour cet appel.
        output_tokens: Jetons effectivement produits.
        attempt: Numéro de tentative, à partir de 1.
    """

    def __init__(
        self,
        message: str,
        *,
        node: str,
        provider: str,
        model: str,
        finish_reason: str,
        max_tokens: int,
        output_tokens: int,
        attempt: int,
    ) -> None:
        """Construit l'erreur avec le contexte complet de l'appel.

        Args:
            message: Description lisible, préfixe du message final.
            node: Nœud appelant.
            provider: Fournisseur ayant servi l'appel.
            model: Modèle renvoyé par l'API.
            finish_reason: Motif de fin de génération, normalisé.
            max_tokens: Plafond demandé.
            output_tokens: Jetons produits.
            attempt: Numéro de tentative.
        """
        super().__init__(
            f"{message} (node={node}, provider={provider}, model={model}, "
            f"finish_reason={finish_reason}, max_tokens={max_tokens}, "
            f"output_tokens={output_tokens}, attempt={attempt})"
        )
        self.node = node
        self.provider = provider
        self.model = model
        self.finish_reason = finish_reason
        self.max_tokens = max_tokens
        self.output_tokens = output_tokens
        self.attempt = attempt


class LLMOutputTruncated(LLMCallError):
    """La génération a été coupée par le plafond de jetons (``length``).

    Le contenu peut être syntaxiquement parsable par accident ; il reste
    incomplet. Aucun niveau de réparation ne s'y applique.
    """


class LLMResourceExhausted(LLMCallError):
    """Le fournisseur a interrompu la génération faute de ressources.

    DeepSeek : ``insufficient_system_resource``. Transitoire : le rejeu à
    l'identique, avec le backoff du client de repli, est légitime.
    """


class LLMOutputIncomplete(LLMCallError):
    """La génération s'est arrêtée pour un motif autre que ``stop``.

    Filtrage de contenu, appel d'outil inattendu, motif inconnu du
    fournisseur : aucun rejeu, la cause n'est pas un plafond trop bas.
    """
