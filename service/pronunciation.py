"""
Real pronunciation scoring — pure Python, no external deps.

Given a TARGET phrase (what the learner meant to say) and the HEARD text
(what STT transcribed from their speech), produce a 0-100 pronunciation score
plus per-word feedback. The premise: if speech-to-text recognized the right
words, the learner pronounced them clearly; close-but-wrong recognitions mean
the pronunciation drifted, and we score *how far* phonetically.

Method (per word): weighted blend of
  - letter-level similarity (difflib ratio)         — surface closeness
  - phonetic-key similarity (vowel-reduced, sound-mapped) — "sounds like"
plus phrase-level completeness (missing / extra words). Phonetics weighted
higher so homophone-ish slips score generously and real misses score low.

This powers the ESL / Live-Translate "score" and the Speech-Practice fork.
"""
from __future__ import annotations
import difflib, re, unicodedata

_VOWELS = set("aeiou")
# digraph -> sound substitutions applied before vowel reduction
_SUBS = [
    ("sch", "sk"), ("tch", "ch"), ("ph", "f"), ("gh", ""), ("ck", "k"),
    ("qu", "kw"), ("wh", "w"), ("kn", "n"), ("wr", "r"), ("mb", "m"),
    ("sh", "s"), ("ch", "k"), ("th", "t"), ("x", "ks"), ("c", "k"),
    ("z", "s"), ("v", "f"), ("y", "i"),
]


def _norm(w: str) -> str:
    w = unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", w)


def phonetic_key(word: str) -> str:
    """Vowel-reduced, sound-mapped key so 'fone'~'phone', 'refridge'~'refrige'."""
    w = _norm(word)
    if not w:
        return ""
    for a, b in _SUBS:
        w = w.replace(a, b)
    # collapse doubled letters
    collapsed = []
    for ch in w:
        if collapsed and collapsed[-1] == ch:
            continue
        collapsed.append(ch)
    w = "".join(collapsed)
    if not w:
        return ""
    # keep the first letter (vowel-led words matter), drop later vowels
    return w[0] + "".join(c for c in w[1:] if c not in _VOWELS)


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def word_score(target: str, heard: str) -> float:
    t, h = _norm(target), _norm(heard)
    if not t:
        return 0.0
    if t == h:
        return 1.0
    letter = _ratio(t, h)
    phon = _ratio(phonetic_key(target), phonetic_key(heard))
    return 0.45 * letter + 0.55 * phon


def score_pronunciation(target: str, heard: str) -> dict:
    """Return {score:0-100, words:[{target,heard,score,ok}], missed:[], extra:int}."""
    tw = re.findall(r"[A-Za-z']+", target or "")
    hw = re.findall(r"[A-Za-z']+", heard or "")
    if not tw:
        return {"score": 0, "words": [], "missed": [], "extra": 0, "note": "no target text"}

    used: set[int] = set()
    words, total, missed = [], 0.0, []
    for t in tw:
        best, bj = 0.0, -1
        for j, h in enumerate(hw):
            if j in used:
                continue
            s = word_score(t, h)
            if s > best:
                best, bj = s, j
        matched = bj >= 0 and best >= 0.30
        if matched:
            used.add(bj)
        else:
            missed.append(t)
        words.append({
            "target": t,
            "heard": hw[bj] if matched else None,
            "score": round(best * 100),
            "ok": best >= 0.80,
        })
        total += best

    base = total / len(tw)
    extra = max(0, len(hw) - len(used))
    penalty = min(0.15, extra * 0.03)             # mild penalty for spurious extra words
    score = max(0, min(100, round((base - penalty) * 100)))
    return {"score": score, "words": words, "missed": missed, "extra": extra}


if __name__ == "__main__":
    cases = [
        ("refrigerator", "refrigerator"),
        ("refrigerator", "refridgerator"),
        ("refrigerator", "refrigedator"),
        ("refrigerator", "refriger"),
        ("refrigerator", "elephant"),
        ("I would like to order coffee", "I would like to order coffee"),
        ("I would like to order coffee", "I would like order coffee"),
        ("I would like to order coffee", "I want to buy some tea"),
        ("the quick brown fox", "the kwick brown phox"),
        ("entrepreneur", "ontrapraneur"),
    ]
    print(f"{'TARGET':32}{'HEARD':30}{'SCORE':>6}")
    for t, h in cases:
        r = score_pronunciation(t, h)
        print(f"{t:32}{h:30}{r['score']:>5}  missed={r['missed']}")
