LABEL_TO_COLOR = {
    "טכנית": "8",      # pale-green
    "מבצעים": "4",    # Red
    "גנק": "1",        # Peacock - pale Blue
    "סגל": "2",      # Sage - pale green
    "סונר": "5",       # Yellow
    "נשק": "3",       # Grape
    "מפקד": "11",       # Pale Blue
    "סגן": "10",        # Basil green
    "צוות": "6",       # orange
}

COLORID_TO_EMOJI = {
    "6": "🐳",  # orange
    "10": "🔱",  # peacock
    "1": "📐",  # peacock
    "8": "⚙️",  # Graphite
    "1": "🤿",  # cyan
    "8": "🎱",  # graphite
    "5": "👂🏼",  # Yellow
    "3": "⚔️", # grape
    "4": "🧑🏼‍💻", # red
    "2": "⚓️" # sage
}


def color_for_label(label):
    return LABEL_TO_COLOR.get(label, None)


def emoji_for_color(color_id):
    return COLORID_TO_EMOJI.get(str(color_id), "")
