"""Automatic fact check (plain code, no AI).

A small model asked to verify another model's answer often just agrees with
it. So, after the Verifier agent, we also check mechanically: every proper
noun ("Agadir", "CentraleSupélec") and every number ("2024", "19") of the
answer must be written somewhere in the evidence (the brochure extracts the
agents found). Anything that is not is "unsupported".
"""

import re
import unicodedata

# Capitalised words that are not facts (sentence starts, common words).
IGNORED = {
    "les", "le", "la", "des", "elle", "ils", "elles", "cette", "cet", "ces",
    "voici", "selon", "dans", "pour", "avec", "aussi", "egalement", "enfin",
    "note", "page", "emsi", "brochure", "oui", "non", "the", "this", "france",
    "maroc", "diplome", "diplomes", "ecole", "filiere", "filieres", "campus",
    "programme", "programmes", "partenariats", "certificat", "certificats",
    "universite", "institut", "genie", "ingenierie", "ingenieur", "ingenieurs",
}

_WORD = re.compile(r"[^\W\d_][\w'’\-]*")


def normalize(text: str) -> str:
    """Lower case, no accents: 'Fès' → 'fes'."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _strip_pages(text: str) -> str:
    """Remove page citations, so '(p. 35)' is not checked as a number."""
    return re.sub(r"\(?\b(?:p\.|pages?)\s*\d+(?:\s*(?:,|et|-)\s*\d+)*\)?", " ",
                  text, flags=re.IGNORECASE)


def key_terms(answer: str) -> list[str]:
    """Proper nouns and numbers of an answer (the facts worth checking)."""
    text = _strip_pages(answer.replace("**", ""))
    terms: list[str] = []
    for line in text.splitlines():
        # Split into sentences. The first word of a normal sentence is skipped
        # (it is capitalised anyway: "Voici", "Elle"...), but in a list item
        # ("- Agadir", "1. ENSTA") the first word IS the fact: check it.
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            is_item = re.match(r"^\s*(?:[-*•]|\d+[.)])\s", sentence) is not None
            sentence = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", sentence)
            words = list(_WORD.finditer(sentence))
            for i, match in enumerate(words):
                word = match.group(0).strip("'’-")
                if (i == 0 and not is_item) or len(word) < 4 or not word[0].isupper():
                    continue
                if normalize(word) in IGNORED:
                    continue
                terms.append(word)
            terms += re.findall(r"\b\d{2,}\b", sentence)
    seen, unique = set(), []
    for t in terms:
        if normalize(t) not in seen:
            seen.add(normalize(t))
            unique.append(t)
    return unique


def unsupported_terms(answer: str, evidence: str) -> list[str]:
    """Key terms of `answer` that do not appear anywhere in `evidence`."""
    haystack = normalize(evidence)
    return [t for t in key_terms(answer) if normalize(t) not in haystack]


def drop_notes(answer: str) -> str:
    """Remove the model's side comments: '*(Note : ...)*', 'Note: ...'."""
    lines = [line for line in answer.splitlines()
             if not re.match(r"^\s*[*_(]*\s*note\s*:", line, re.IGNORECASE)]
    return "\n".join(lines).strip()


def remove_terms(answer: str, terms: list[str]) -> str:
    """Last resort: delete the list items / sentences that contain an
    unsupported term (keeps the rest of the answer)."""
    if not terms:
        return answer
    bad = [normalize(t) for t in terms]

    def is_bad(piece: str) -> bool:
        return any(b in normalize(piece) for b in bad)

    lines = []
    for line in answer.splitlines():
        if not is_bad(line):
            lines.append(line)
            continue
        # "Casablanca, Rabat, Agadir" → drop only the bad items.
        head, sep, tail = line.rpartition(":")
        items = re.split(r",\s*|\s+et\s+", tail if sep else line)
        kept = [i for i in items if i.strip() and not is_bad(i)]
        if len(items) > 1 and kept:
            lines.append((head + sep + " " if sep else "") + ", ".join(k.strip() for k in kept))
        # else: the whole line is about the unsupported fact → dropped
    return "\n".join(lines).strip()
