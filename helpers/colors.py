LABEL_TO_COLOR = {
    "פיקוד": "11",      # red
    "הנדסה": "7",       # green
    "כלל צוות": "2",    # lavender
    # add your own mappings here…
}


def color_for_label(label: str):
    """Return the calendar colorId for a label.

    The incoming label is normalised to match the keys in
    ``LABEL_TO_COLOR``.  If no colour is configured for the label,
    ``None`` is returned so the default (blue) colour is used.
    """
    if not label:
        return None
    return LABEL_TO_COLOR.get(label.strip())
