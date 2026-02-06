COLORID_TO_EMOJI = {
    "10": "🔱",   # basil - סגן
    "5": "👂🏼",   # banana - סונאר
    "6": "🐋",    # tangerine - צוות
    "11": "👑",   # tomato - מפקד
    "2": "⚓️",   # sage - סגל
    "3": "⚔️",   # grape - נשק
    "1": "📐",    # lavender - גנק
    "8": "⚙️",   # graphite - טכנית
    "4": "🧑🏼‍💻", # flamingo - מבצעים
}


def emoji_for_color(color_id):
    return COLORID_TO_EMOJI.get(str(color_id), "")
