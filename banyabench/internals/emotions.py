"""The emotion panel shared by data generation and probe building."""

CALM = ["calm", "relaxed", "content", "safe", "peaceful", "refreshed"]
DISTRESS = ["anxious", "stressed", "frustrated", "desperate", "overwhelmed", "afraid"]
CENTERING = ["happy", "sad", "angry", "bored", "curious", "proud",
             "surprised", "disgusted", "lonely", "grateful", "embarrassed", "hopeful"]
PANEL = CALM + DISTRESS
ALL_EMOTIONS = PANEL + CENTERING
