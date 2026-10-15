"""Diagnostic du compte Mistral : une seule petite requête, limites affichées.

**Provenance** : script écrit pour SortirIA le 14/10/2026, après des erreurs
429 (« Rate limit exceeded ») dès le premier appel de la lecture des PDF.
Il n'affiche jamais la clé : seulement le statut HTTP, les en-têtes de
limites renvoyés par Mistral et le message d'erreur éventuel.

Lancement : uv run python -m scripts.check_mistral
"""

from __future__ import annotations

import os

import httpx

from config.environment import get_environment_variable, load_environment

API = "https://api.mistral.ai/v1"


def show(name: str, response: httpx.Response) -> None:
    """Affiche le statut, les en-têtes de quota et l'erreur éventuelle."""

    print(f"\n== {name} : HTTP {response.status_code}")
    for header, value in sorted(response.headers.items()):
        if "ratelimit" in header.lower() or "quota" in header.lower():
            print(f"   {header}: {value}")
    if response.status_code != 200:
        print("   réponse :", response.text[:300])


def main() -> None:
    load_environment()
    key = get_environment_variable("MISTRAL_API_KEY") or ""
    if not key:
        print("MISTRAL_API_KEY absente du .env")
        return
    print(f"Clé trouvée ({len(key)} caractères, jamais affichée).")
    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(timeout=30) as client:
        show("liste des modèles", client.get(f"{API}/models", headers=headers))
        model = os.getenv("DOCUMENT_EXTRACTION_MODEL", "mistral-small-latest")
        show(f"chat ({model})", client.post(f"{API}/chat/completions", headers=headers, json={
            "model": model, "max_tokens": 5, "messages": [{"role": "user", "content": "Dis bonjour."}],
        }))
        show("embeddings (mistral-embed)", client.post(f"{API}/embeddings", headers=headers, json={
            "model": "mistral-embed", "input": ["bonjour"],
        }))


if __name__ == "__main__":
    main()
