LABEL_TO_COLOR = {
    "טכנית": "6",      # orange
    "מבצעים": "11",       # Red
    "גנק": "3",    # Purple
    "פיקוד": "8",  # Graphite
    "סונר": "9",  # blueberry
    "נשק": "10",  # basil green
    "מפקד": "7",  # cyan
    "סגן": "2",  # peacock
    "צוות": "1",  # lavender
}

def color_for_label(label):
    return LABEL_TO_COLOR.get(label, None)