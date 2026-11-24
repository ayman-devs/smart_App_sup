"""Compare the 3 modes (classic, agent, multi-agent) on a list of questions.

The API must be running (uvicorn app.main:app --port 8000). Then:
    python -m eval.compare                 # uses eval/questions.txt
    python -m eval.compare my_questions.txt

In the questions file, start a line with "!" for a TRAP question: the
brochure does not answer it, so the right answer is "not found". This
measures how often each mode invents an answer.

Prints a table and writes eval/results.csv.
"""

import csv
import sys
from pathlib import Path

import httpx

API = "http://localhost:8000"
HERE = Path(__file__).parent
MODES = ["classic", "agent", "multi"]
NOT_FOUND_MARKERS = ["pas trouvé", "uniquement", "seulement aux questions",
                     "ne peux pas répondre", "ne connais que",
                     "only answer", "not found"]


def says_not_found(answer: str) -> bool:
    return any(m in answer.lower() for m in NOT_FOUND_MARKERS)


def main(questions_file: Path) -> None:
    lines = [q.strip() for q in questions_file.read_text(encoding="utf-8")
             .splitlines() if q.strip() and not q.startswith("#")]
    rows = []
    traps = {m: [0, 0] for m in MODES}            # mode → [correct refusals, traps]

    for line in lines:
        trap = line.startswith("!")
        question = line.lstrip("! ").strip()
        print(f"\n{'[PIÈGE] ' if trap else ''}? {question}")
        r = httpx.post(f"{API}/compare", json={"question": question}, timeout=1800)
        r.raise_for_status()
        data = r.json()
        row = {"question": question, "trap": trap}
        for mode in MODES:
            s = data["summary"][mode]
            answer = data[mode]["answer"]
            refused = says_not_found(answer)
            if trap:
                traps[mode][1] += 1
                traps[mode][0] += refused
            row |= {f"{mode}_s": s["seconds"], f"{mode}_pages": s["pages"],
                    f"{mode}_tools": s["tool_calls"], f"{mode}_answer": answer}
            verdict = (" ✔ refuse" if refused else " ✘ invente") if trap else ""
            agents = f" agents {' → '.join(s['agents'])}" if s["agents"] else ""
            print(f"  {mode:<8}{s['seconds']:>7}s  pages {s['pages']}"
                  f"  outils {s['tool_calls']}{agents}{verdict}")
        rows.append(row)

    if any(t for _, t in traps.values()):
        print("\nQuestions pièges correctement refusées :")
        for mode, (ok, total) in traps.items():
            print(f"  {mode:<8} {ok}/{total}")

    out = HERE / "results.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "questions.txt")
