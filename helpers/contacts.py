# helpers/contacts.py
import pandas as pd

_CONTACTS = None
_LABEL_EMAILS = None
_LABELS = None

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

def load_contacts(path="tag_contacts.xlsx"):
    """
    Loads contacts from Excel and builds:
      - _LABEL_EMAILS: dict(label -> [emails])
      - _LABELS: sorted list of labels with emails
    Required columns (any recognized variant): label/tag, email
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

    df["label"] = df["label"].astype(str).str.strip()
    df["email"] = df["email"].astype(str).str.strip()

    grouped = (
        df.groupby("label")["email"]
        .apply(lambda s: [e for e in s if e])
        .to_dict()
    )

    _CONTACTS = df
    _LABEL_EMAILS = grouped
    _LABELS = sorted([k for k, v in grouped.items() if v])
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
