COLORID_TO_EMOJI = {
    "6": "🐳",   # orange
    "10": "🔱",  # basil green
    "1": "🤿",   # cyan/peacock
    "8": "🎱",   # graphite
    "5": "👂🏼",  # yellow
    "3": "⚔️",   # grape
    "4": "🧑🏼‍💻", # red
    "2": "⚓️",   # sage
    "11": "🫡",  # pale blue
}


def emoji_for_color(color_id):
    return COLORID_TO_EMOJI.get(str(color_id), "")
