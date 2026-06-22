"""Language detection and resolution.

Confidence deviation: the contract's `MIN_DETECTION_CONFIDENCE` (0.9) cannot
be applied literally as the gate inside `detect()` — langdetect's actual
top-candidate probability for short, otherwise-correct supported-language
text (e.g. ~0.71 for short Russian phrases) regularly falls well below 0.9,
which would force a default-to-EN result contradicting the contract's own
Russian test case. Per agreed resolution, `detect()` gates on a lower
internal threshold (`_CONFIDENCE_GATE`) instead. `MIN_DETECTION_CONFIDENCE`
is retained as the documented contract constant.
"""

from langdetect import DetectorFactory, detect_langs

DetectorFactory.seed = 0

SUPPORTED_LANGUAGES = {"en", "ru"}
DEFAULT_LANGUAGE = "en"
MIN_DETECTION_LENGTH = 10
MIN_DETECTION_CONFIDENCE = 0.9

_CONFIDENCE_GATE = 0.6


def detect(text: str) -> str:
    if not text or len(text) < MIN_DETECTION_LENGTH:
        return DEFAULT_LANGUAGE
    try:
        top = detect_langs(text)[0]
    except Exception:
        return DEFAULT_LANGUAGE
    if top.lang not in SUPPORTED_LANGUAGES or top.prob < _CONFIDENCE_GATE:
        return DEFAULT_LANGUAGE
    return top.lang


def resolve(stored_language: str | None, text: str) -> tuple[str, bool]:
    if text is None:
        raise TypeError("text must not be None")
    if not stored_language:
        return detect(text), True
    if len(text) < MIN_DETECTION_LENGTH:
        return stored_language, False
    detected = detect(text)
    if detected == stored_language:
        return stored_language, False
    return detected, True
