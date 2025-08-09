LABEL_TO_COLOR = {
    "פיקוד": "11",      # red
    "הנדסה": "7",       # green
    "כלל צוות": "2",    # lavender
    # add your own mappings here…
}

def color_for_label(label):
    return LABEL_TO_COLOR.get(label, None)