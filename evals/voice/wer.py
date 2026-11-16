"""Word error rate with Italian-aware normalisation, so formatting is not counted as an error."""

import re
import unicodedata

_UNITS = [
    "zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci",
    "undici", "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette",
    "diciotto", "diciannove",
]  # fmt: skip
_TENS = {2: "venti", 3: "trenta", 4: "quaranta", 5: "cinquanta", 6: "sessanta", 7: "settanta",
         8: "ottanta", 9: "novanta"}  # fmt: skip


def italian_number(n: int) -> str:
    """An integer 0..999999 written as one Italian word (e.g. 23 -> 'ventitre')."""
    if n < 20:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        base = _TENS[tens]
        if unit in (1, 8):  # elision: ventuno, ventotto
            base = base[:-1]
        return base + (_UNITS[unit] if unit else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        base = "cento" if hundreds == 1 else _UNITS[hundreds] + "cento"
        if rest and hundreds == 1 and (rest // 10 == 8 or rest == 8):
            base = base[:-1]  # centottanta, centotto
        return base + (italian_number(rest) if rest else "")
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        base = "mille" if thousands == 1 else italian_number(thousands) + "mila"
        return base + (italian_number(rest) if rest else "")
    return str(n)


def normalize(text: str) -> list[str]:
    text = text.lower().replace("%", " per cento ")
    text = re.sub(r"\d+", lambda m: f" {italian_number(int(m.group()))} ", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z\s]", " ", text)  # punctuation and apostrophes split words
    return text.split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: (substitutions + deletions + insertions) / reference words."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)
