# helpers/contacts.py
import os
import re
from typing import Dict, List

from firebase_admin import credentials, firestore, get_app, initialize_app

_CONTACTS = None
_LABEL_EMAILS = None
_LABELS = None

_LABEL_SPLIT_RE = re.compile(r"[;,/|]+")
_FIRESTORE = None


def _get_firestore_client():
    """Initialize and cache a Firestore client using a service account."""
    global _FIRESTORE
    if _FIRESTORE is not None:
        return _FIRESTORE

    cred_path = os.getenv("FIREBASE_CREDENTIALS")
    if not cred_path:
        raise ValueError("FIREBASE_CREDENTIALS environment variable is required")
    if not os.path.exists(cred_path):
        raise FileNotFoundError(f"Service account file not found at: {cred_path}")

    credentials_obj = credentials.Certificate(cred_path)
    try:
        get_app()
    except ValueError:
        initialize_app(credentials_obj)
    _FIRESTORE = firestore.client()
    return _FIRESTORE

def _split_labels(value) -> List[str]:
    """Split a raw label cell into a list of cleaned labels."""
    if value is None:
        return []
    if isinstance(value, list):
        labels: List[str] = []
        for item in value:
            labels.extend(_split_labels(item))
        return labels

    text = str(value).strip()
    if not text:
        return []

    text = text.replace("\n", ",")
    parts = _LABEL_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def load_contacts():
    """
    Loads contacts from the Firebase collection "INS Leviathan" and builds:
      - _LABEL_EMAILS: dict(label -> [emails])
      - _LABELS: sorted list of labels with emails
    Documents are expected to contain an "email" string and a "labels" array.
    """
    global _CONTACTS, _LABEL_EMAILS, _LABELS
    db = _get_firestore_client()
    collection = db.collection("INS Leviathan")

    label_emails: Dict[str, List[str]] = {}
    all_labels_set: set[str] = set()
    contacts_rows = []

    for doc in collection.stream():
        data = doc.to_dict() or {}
        labels = data.get("labels") or []
        email = str(data.get("email", "")).strip()

        labels = _split_labels(labels)
        for label in labels:
            all_labels_set.add(label)
            emails = label_emails.setdefault(label, [])
            if email and email not in emails:
                emails.append(email)
        if not labels:
            continue
        if not email:
            for label in labels:
                label_emails.setdefault(label, [])

        contacts_rows.append({"id": doc.id, "label": labels, "email": email})

    for label in all_labels_set:
        label_emails.setdefault(label, [])

    _CONTACTS = contacts_rows
    _LABEL_EMAILS = label_emails
    _LABELS = sorted(all_labels_set)
    return _LABELS

def emails_for_label(label: str):
    if not label:
        return []
    if _LABEL_EMAILS is None:
        load_contacts()
    return _LABEL_EMAILS.get(label.strip(), [])

def all_labels():
    if _LABELS is None:
        load_contacts()
    return _LABELS
