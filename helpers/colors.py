LABEL_TO_COLOR = {
    "טכנית": "6",      # orange
    "מבצעים": "11",    # Red
    "גנק": "3",        # Purple
    "פיקוד": "8",      # Graphite
    "סונר": "9",       # blueberry
    "נשק": "10",       # basil green
    "מפקד": "7",       # cyan
    "סגן": "2",        # peacock
    "צוות": "1",       # lavender
}

COLORID_TO_EMOJI = {
    "1": "",  # lavender
    "2": "",  # peacock
    "3": "",  # purple
    "6": "",  # orange
    "7": "",  # cyan
    "8": "",  # graphite
    "9": "",  # blueberry
    "10": "", # basil green
    "11": "", # red
}


def color_for_label(label):
    return LABEL_TO_COLOR.get(label, None)


def emoji_for_color(color_id):
    return COLORID_TO_EMOJI.get(str(color_id), "")
