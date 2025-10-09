# helpers/contacts.py
import re
from typing import Dict, List

import pandas as pd

_CONTACTS = None
_LABEL_EMAILS = None
_LABELS = None

_LABEL_SPLIT_RE = re.compile(r"[;,/|]+")

# Map common header variants (Heb/Eng) to canonical names
HEADER_MAP = {
    # label/tag
    "label": "label", "labels": "label", "category": "label", "tag": "label", "tags": "label",
    "תגית": "label", "תגיות": "label", "תווית": "label", "סיווג": "label", "קבוצה": "label",

    # email
    "email": "email", "e-mail": "email", "mail": "email",
    "דוא\"ל": "email", "דואל": "email", "אימייל": "email", "מייל": "email",
    "כתובת אימייל": "email", "כתובת דוא\"ל": "email", "כתובת מייל": "email",

    # optional name
    "name": "name", "full name": "name", "שם": "name", "שם מלא": "name",
}

def _canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    rename = {c: HEADER_MAP.get(c, c) for c in df.columns}
    return df.rename(columns=rename)

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


def load_contacts(path="tag_contacts.xlsx"):
    """
    Loads contacts from Excel and builds:
      - _LABEL_EMAILS: dict(label -> [emails])
      - _LABELS: sorted list of labels with emails
    Required columns (any recognized variant): label/tag, email.
    Cells in the label column may contain multiple labels separated by
    commas, semicolons, slashes, pipes, or newlines.
    """
    global _CONTACTS, _LABEL_EMAILS, _LABELS
    df = pd.read_excel(path, header=0).fillna("")
    df = _canonicalize_columns(df)

    required = {"label", "email"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Excel must include columns for label & email. "
            f"Detected: {list(df.columns)}. Missing: {list(missing)}"
        )

    df["label"] = df["label"].apply(_split_labels)
    df["email"] = df["email"].astype(str).str.strip()

    label_emails: Dict[str, List[str]] = {}
    all_labels_set: set[str] = set()

    for _, row in df.iterrows():
        labels = row["label"] or []
        email = row["email"].strip()
        for label in labels:
            if not label:
                continue
            all_labels_set.add(label)
            emails = label_emails.setdefault(label, [])
            if email and email not in emails:
                emails.append(email)
        if not labels:
            continue
        if not email:
            for label in labels:
                label_emails.setdefault(label, [])

    for label in all_labels_set:
        label_emails.setdefault(label, [])

    exploded = df.explode("label")
    exploded["label"] = exploded["label"].fillna("").astype(str).str.strip()

    _CONTACTS = exploded
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
