# helpers/contacts.py
import json
import os
from typing import Dict, List, Optional

from google.cloud import firestore
from google.oauth2 import service_account

_CLIENT: Optional[firestore.Client] = None
_CONTACTS_CACHE: Dict[str, List[dict]] = {}
_LABEL_EMAILS_CACHE: Dict[str, Dict[str, List[str]]] = {}
_LABELS_CACHE: Dict[str, List[str]] = {}

DEFAULT_COLLECTION = os.getenv("FIREBASE_COLLECTION", "INS Leviathan")
COLLECTION_MAP = os.getenv("FIREBASE_COLLECTION_MAP")

# Place your Firebase service account JSON in the FIREBASE_CREDENTIALS_JSON
# environment variable (or provide a path via FIREBASE_CREDENTIALS_FILE). The
# JSON must include the private key block exactly as received from Firebase.
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
FIREBASE_CREDENTIALS_FILE = os.getenv("FIREBASE_CREDENTIALS_FILE")


def _cache_key(user_id: Optional[int]) -> str:
    return str(user_id) if user_id is not None else "__default__"


def _get_collection_for_user(user_id: Optional[int]) -> str:
    if COLLECTION_MAP:
        try:
            mapping = json.loads(COLLECTION_MAP)
            collection = mapping.get(str(user_id))
            if collection:
                return collection
        except json.JSONDecodeError:
            pass
    return DEFAULT_COLLECTION


def _build_firestore_client() -> firestore.Client:
    global _CLIENT
    if _CLIENT:
        return _CLIENT

    credentials = None
    if FIREBASE_CREDENTIALS_JSON:
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(FIREBASE_CREDENTIALS_JSON)
        )
    elif FIREBASE_CREDENTIALS_FILE:
        credentials = service_account.Credentials.from_service_account_file(
            FIREBASE_CREDENTIALS_FILE
        )
    else:
        raise RuntimeError(
            "Firebase credentials missing. Set FIREBASE_CREDENTIALS_JSON with your service "
            "account JSON (including private key) or FIREBASE_CREDENTIALS_FILE with a path to the file."
        )

    _CLIENT = firestore.Client(credentials=credentials, project=credentials.project_id)
    return _CLIENT


def _fetch_contacts_from_firestore(collection_name: str) -> List[dict]:
    client = _build_firestore_client()
    docs = client.collection(collection_name).stream()
    contacts: List[dict] = []
    for doc in docs:
        data = doc.to_dict() or {}
        contacts.append({
            "labels": data.get("Lables") or data.get("Labels") or [],
            "email": str(data.get("Email", "")).strip(),
            "first_name": data.get("First name") or data.get("First Name") or "",
            "last_name": data.get("Last name") or data.get("Last Name") or "",
        })
    return contacts


def load_contacts(user_id: Optional[int] = None) -> List[str]:
    """Load contacts for a specific user (collection) from Firestore."""
    cache_key = _cache_key(user_id)
    if cache_key in _LABELS_CACHE:
        return _LABELS_CACHE[cache_key]

    collection_name = _get_collection_for_user(user_id)
    contacts = _fetch_contacts_from_firestore(collection_name)

    label_emails: Dict[str, List[str]] = {}
    labels_set: set[str] = set()

    for contact in contacts:
        labels = contact.get("labels") or []
        email = contact.get("email", "")
        for label in labels:
            if not label:
                continue
            labels_set.add(label)
            emails = label_emails.setdefault(label, [])
            if email and email not in emails:
                emails.append(email)
        if not labels:
            continue
        if not email:
            for label in labels:
                label_emails.setdefault(label, [])

    for label in labels_set:
        label_emails.setdefault(label, [])

    _CONTACTS_CACHE[cache_key] = contacts
    _LABEL_EMAILS_CACHE[cache_key] = label_emails
    _LABELS_CACHE[cache_key] = sorted(labels_set)
    return _LABELS_CACHE[cache_key]


def emails_for_label(label: str, user_id: Optional[int] = None) -> List[str]:
    if not label:
        return []
    load_contacts(user_id)
    cache_key = _cache_key(user_id)
    return _LABEL_EMAILS_CACHE.get(cache_key, {}).get(label.strip(), [])


def all_labels(user_id: Optional[int] = None) -> List[str]:
    load_contacts(user_id)
    return _LABELS_CACHE.get(_cache_key(user_id), [])
