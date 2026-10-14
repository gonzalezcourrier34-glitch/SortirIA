"""
Service de gestion de la mémoire conversationnelle courte.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/memory_service.py` (commit 18f7ce1).

Cette mémoire est :
- persistée dans un fichier JSON local
- vidée au démarrage de l'API
- limitée à une fenêtre courte par session
- utilisée uniquement pour maintenir la cohérence
    d'une conversation en cours

Le service ne sert pas de base de connaissance.
Il conserve seulement les derniers échanges utiles
à l'interprétation des questions utilisateur.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MemoryService:
    # Service de mémoire courte conversationnelle.
    # Son rôle est de conserver quelques échanges récents
    # pour maintenir le contexte d'une conversation en cours.
    ALLOWED_ROLES = {"user", "assistant"}

    def __init__(
        self,
        memory_dir: str | Path = "data/memory",
        memory_file: str = "conversations.json",
        max_turns: int = 3,
    ) -> None:
        """
        Initialise le service mémoire.

        Parameters
        ----------
        memory_dir : str | Path, default="data/memory"
            Dossier de stockage local.
        memory_file : str, default="conversations.json"
            Nom du fichier JSON de mémoire.
        max_turns : int, default=3
            Nombre maximal de tours conservés par session.
            Un tour = 1 message utilisateur + 1 message assistant.
        """
        # Configure l'emplacement du stockage local
        # et la taille maximale de la fenêtre mémoire.
        self.memory_dir = Path(memory_dir)
        self.memory_file = self.memory_dir / memory_file
        self.max_turns = max(1, int(max_turns))
        self.max_messages = self.max_turns * 2

        self.memory_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Gestion fichier
    # ------------------------------------------------------------------

    def reset_memory(self) -> None:
        """
        Réinitialise complètement la mémoire au démarrage de l'API.
        """
        # Vide entièrement le stockage mémoire.
        # Utile si l'on veut repartir d'un état propre au lancement.
        self._write_data({})

    def _read_data(self) -> dict[str, list[dict[str, str]]]:
        """
        Lit le contenu mémoire depuis le fichier JSON.

        Returns
        -------
        dict[str, list[dict[str, str]]]
            Dictionnaire des sessions stockées.
        """
        # Lit le fichier JSON de mémoire.
        # En cas d'absence ou de corruption, renvoie un dictionnaire vide
        # pour éviter de casser le service.
        if not self.memory_file.exists():
            return {}

        try:
            with self.memory_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

        if not isinstance(data, dict):
            return {}

        return data

    def _write_data(self, data: dict[str, Any]) -> None:
        """
        Écrit le contenu mémoire dans le fichier JSON.
        """
        # Écrit l'état mémoire complet sur disque
        # dans un format JSON lisible.
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        with self.memory_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Validation / nettoyage
    # ------------------------------------------------------------------

    def _normalize_session_id(self, session_id: object) -> str:
        """
        Normalise un identifiant de session.
        """
        # Convertit un identifiant en chaîne propre,
        # ou chaîne vide si absent.
        return "" if session_id is None else str(session_id).strip()

    def _normalize_role(self, role: object) -> str:
        """
        Normalise un rôle conversationnel.
        """
        # Uniformise les rôles en minuscules
        # pour éviter les incohérences de casse.
        return "" if role is None else str(role).strip().lower()

    def _normalize_content(self, content: object) -> str:
        """
        Normalise le contenu textuel d'un message.
        """
        # Nettoie légèrement le contenu message,
        # limite sa taille pour éviter qu'un historique
        # trop verbeux alourdisse inutilement le prompt.
        text = "" if content is None else str(content).strip()

        max_chars = 600
        if len(text) > max_chars:
            truncated = text[:max_chars]

            if " " in truncated:
                truncated = truncated.rsplit(" ", 1)[0]

            text = truncated.rstrip(" ,;:.-") + "..."

        return text

    def _is_valid_message(self, message: object) -> bool:
        """
        Vérifie qu'un objet ressemble à un message valide.
        """
        # Contrôle minimal d'intégrité d'un message :
        # structure dict, rôle autorisé, contenu non vide.
        if not isinstance(message, dict):
            return False

        role = self._normalize_role(message.get("role"))
        content = self._normalize_content(message.get("content"))

        return role in self.ALLOWED_ROLES and bool(content)

    def _clean_history(self, history: object) -> list[dict[str, str]]:
        """
        Nettoie une liste brute de messages et conserve uniquement
        les messages valides.
        """
        # Nettoie une liste d'historique potentiellement sale,
        # conserve seulement les messages valides,
        # puis applique la fenêtre mémoire maximale.
        if not isinstance(history, list):
            return []

        clean_history: list[dict[str, str]] = []

        for item in history:
            if not self._is_valid_message(item):
                continue

            clean_history.append(
                {
                    "role": self._normalize_role(item["role"]),
                    "content": self._normalize_content(item["content"]),
                }
            )

        return clean_history[-self.max_messages :]

    # ------------------------------------------------------------------
    # Gestion session
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[str]:
        """
        Retourne la liste des identifiants de session présents en mémoire.
        """
        # Permet d'inspecter rapidement quelles sessions existent.
        data = self._read_data()
        return sorted(str(session_id) for session_id in data.keys())

    def has_session(self, session_id: str) -> bool:
        """
        Indique si une session existe et contient au moins un message valide.
        """
        # Vérifie l'existence réelle d'une session exploitable.
        session_id = self._normalize_session_id(session_id)
        if not session_id:
            return False

        return bool(self.get_history(session_id))

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        """
        Retourne l'historique court d'une session.
        """
        # Point d'accès principal à l'historique d'une session.
        # Le résultat est systématiquement nettoyé avant retour.
        session_id = self._normalize_session_id(session_id)
        if not session_id:
            return []

        data = self._read_data()
        history = data.get(session_id, [])

        return self._clean_history(history)

    def get_recent_messages(
        self,
        session_id: str,
        max_messages: int | None = None,
    ) -> list[dict[str, str]]:
        """
        Retourne les messages les plus récents d'une session.

        Parameters
        ----------
        session_id : str
            Identifiant de session.
        max_messages : int | None, default=None
            Nombre maximal de messages à conserver.
            Si None, on utilise la fenêtre mémoire du service.
        """
        # Extrait la partie la plus récente de l'historique.
        # Pratique pour n'injecter qu'une petite fenêtre dans le prompt.
        history = self.get_history(session_id)
        if not history:
            return []

        if max_messages is None:
            return history

        try:
            max_messages = max(1, int(max_messages))
        except (TypeError, ValueError):
            max_messages = self.max_messages

        return history[-max_messages:]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        """
        Ajoute un message à la mémoire courte d'une session.
        """
        # Ajoute un message unique à une session,
        # puis recoupe l'historique à la taille maximale autorisée.
        session_id = self._normalize_session_id(session_id)
        role = self._normalize_role(role)
        content = self._normalize_content(content)

        if not session_id or role not in self.ALLOWED_ROLES or not content:
            return

        data = self._read_data()
        history = self._clean_history(data.get(session_id, []))

        history.append(
            {
                "role": role,
                "content": content,
            }
        )

        data[session_id] = history[-self.max_messages :]
        self._write_data(data)

    def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """
        Ajoute un tour complet utilisateur + assistant à une session.
        """
        # Helper pratique pour écrire un échange complet
        # plutôt que deux appels séparés.
        self.append_message(session_id, "user", user_message)
        self.append_message(session_id, "assistant", assistant_message)

    def clear_session(self, session_id: str) -> None:
        """
        Supprime l'historique d'une session donnée.
        """
        # Efface entièrement une session précise du stockage.
        session_id = self._normalize_session_id(session_id)
        if not session_id:
            return

        data = self._read_data()
        data.pop(session_id, None)
        self._write_data(data)

    def prune_empty_sessions(self) -> None:
        """
        Supprime les sessions invalides ou vides du stockage.
        """
        # Nettoie le stockage global en supprimant
        # les sessions sans identifiant valide ou sans contenu exploitable.
        data = self._read_data()
        cleaned_data: dict[str, list[dict[str, str]]] = {}

        for session_id, history in data.items():
            normalized_session_id = self._normalize_session_id(session_id)
            if not normalized_session_id:
                continue

            clean_history = self._clean_history(history)
            if clean_history:
                cleaned_data[normalized_session_id] = clean_history

        self._write_data(cleaned_data)

    # ------------------------------------------------------------------
    # Formatage pour prompts / LLM
    # ------------------------------------------------------------------

    def format_history_for_prompt(
        self,
        session_id: str,
        max_messages: int | None = None,
    ) -> str:
        """
        Formate l'historique d'une session pour l'injection dans un prompt.
        """
        # Transforme l'historique en texte concaténé lisible,
        # adapté à une injection simple dans un prompt LLM.
        history = self.get_recent_messages(
            session_id=session_id,
            max_messages=max_messages,
        )
        if not history:
            return ""

        lines: list[str] = []
        for msg in history:
            speaker = "Utilisateur" if msg["role"] == "user" else "Assistant"
            lines.append(f"{speaker} : {msg['content']}")

        return "\n".join(lines)

    def build_prompt_messages(
        self,
        session_id: str,
        max_messages: int | None = None,
    ) -> list[dict[str, str]]:
        """
        Retourne l'historique récent sous forme de messages structurés.

        Cette représentation est utile si le modèle ou la chaîne attend
        une liste de messages au lieu d'un simple texte concaténé.
        """
        # Variante structurée du contexte conversationnel,
        # utile pour les chaînes ou modèles orientés messages.
        history = self.get_recent_messages(
            session_id=session_id,
            max_messages=max_messages,
        )

        return [
            {
                "role": msg["role"],
                "content": msg["content"],
            }
            for msg in history
        ]

    # ------------------------------------------------------------------
    # Statistiques légères
    # ------------------------------------------------------------------

    def get_session_size(self, session_id: str) -> int:
        """
        Retourne le nombre de messages valides présents dans une session.
        """
        # Petit indicateur simple pour connaître
        # la taille de l'historique d'une session.
        return len(self.get_history(session_id))

    def get_stats(self) -> dict[str, int]:
        """
        Retourne quelques statistiques simples sur la mémoire.
        """
        # Fournit une vue d'ensemble légère du stockage mémoire :
        # nombre de sessions, nombre total de messages,
        # paramètres de fenêtre mémoire.
        data = self._read_data()
        n_sessions = 0
        n_messages = 0

        for session_id, history in data.items():
            normalized_session_id = self._normalize_session_id(session_id)
            if not normalized_session_id:
                continue

            clean_history = self._clean_history(history)
            if clean_history:
                n_sessions += 1
                n_messages += len(clean_history)

        return {
            "n_sessions": n_sessions,
            "n_messages": n_messages,
            "max_turns": self.max_turns,
            "max_messages": self.max_messages,
        }