"""
Face-match accuracy report.

Measures how well the face model separates the right person from the wrong
one, using photos whose owner you know, and prints a Markdown report: the
distance distributions, how the current pass mark behaves, and a threshold
sweep. This is what to quote when asked "how accurate is it?".

Ground truth comes from a labels file you fill in (the tool cannot know who is
in a photo):

    path,person,kind
    uploads/faces/1BM23IS091.jpg,P1,ref
    uploads/checkins/5acd.../1BM23IS091.jpg,P1,selfie
    uploads/faces/1BM23IS005.jpg,P2,ref

  person  any label you like for the human in the photo (P1, P2...). The same
          human must always get the same label, even across different USNs.
          Use labels, not names: the report is meant to be shareable.
  kind    ref    = a registered reference photo
          selfie = a check-in selfie (or any later photo of the person)

Every ref is compared with every selfie, the way a real check-in does:
same person = genuine pair, different person = impostor pair. Pairs of
byte-identical files are skipped.

    # 1. list your stored photos so you can fill in the "person" column
    python tools/accuracy_report.py --template labels.csv
    # 2. after filling it in
    python tools/accuracy_report.py --labels labels.csv --models Facenet512,VGG-Face --out report.md

Run it with the full environment (DeepFace and its model weights installed).
Photos are biometric data: keep the labels file and photos out of version
control; the report itself contains only numbers and your person labels.
"""

import argparse
import csv
import glob
import hashlib
import os
import statistics
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from face_settings import REVIEW_RATIO  # noqa: E402

# The pass marks the app uses (face_settings.py), for the models worth comparing.
PASS_MARKS = {"Facenet512": 0.30, "VGG-Face": 0.50, "ArcFace": 0.68}

SWEEP = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


def write_template(path, root):
    """List stored photos so the person column can be filled in."""

    rows = []

    for photo in sorted(glob.glob(os.path.join(root, "uploads", "faces", "*.jpg"))):
        rows.append((os.path.relpath(photo, root), "", "ref"))

    for photo in sorted(glob.glob(os.path.join(root, "uploads", "checkins", "*", "*.jpg"))):
        rows.append((os.path.relpath(photo, root), "", "selfie"))

    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "person", "kind"])
        writer.writerows(rows)

    return len(rows)


def load_labels(path, root):
    """Rows with a person label; unlabelled rows are counted and skipped."""

    labelled, skipped = [], 0

    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            person = (row.get("person") or "").strip()
            kind = (row.get("kind") or "").strip().lower()
            file_path = (row.get("path") or "").strip()

            if not person or kind not in ("ref", "selfie") or not file_path:
                skipped += 1
                continue

            full = file_path if os.path.isabs(file_path) else os.path.join(root, file_path)

            if not os.path.isfile(full):
                skipped += 1
                continue

            labelled.append({"path": full, "person": person, "kind": kind})

    return labelled, skipped


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def build_pairs(rows):
    """(ref, selfie, genuine?) for every ref/selfie combination, skipping identical files."""

    refs = [r for r in rows if r["kind"] == "ref"]
    selfies = [r for r in rows if r["kind"] == "selfie"]
    digests = {r["path"]: _digest(r["path"]) for r in rows}
    pairs = []

    for ref in refs:
        for selfie in selfies:
            if digests[ref["path"]] == digests[selfie["path"]]:
                continue
            pairs.append((ref, selfie, ref["person"] == selfie["person"]))

    return pairs


def measure_pairs(pairs, model, verify=None):
    """Distances for each pair; returns (genuine, impostor, failed_count)."""

    if verify is None:
        from deepface import DeepFace

        def verify(a, b):
            return DeepFace.verify(
                img1_path=a, img2_path=b, model_name=model,
                distance_metric="cosine", enforce_detection=True
            )["distance"]

    genuine, impostor, failed = [], [], 0

    for ref, selfie, same in pairs:
        try:
            distance = float(verify(ref["path"], selfie["path"]))
        except ValueError:
            failed += 1  # no face found in one of the photos
            continue

        (genuine if same else impostor).append(distance)

    return genuine, impostor, failed


def describe(values):
    if not values:
        return None

    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def outcome_counts(distances, pass_mark, review_at):
    """How the app treats these distances: counted, held for the faculty, rejected."""

    counted = sum(1 for d in distances if d < review_at)
    held = sum(1 for d in distances if review_at <= d <= pass_mark)
    rejected = sum(1 for d in distances if d > pass_mark)
    return {"counted": counted, "held": held, "rejected": rejected}


def sweep_rows(genuine, impostor, thresholds=SWEEP):
    """False-reject / false-accept rates when accepting everything <= threshold."""

    rows = []

    for threshold in thresholds:
        rows.append({
            "threshold": threshold,
            "false_reject": sum(1 for d in genuine if d > threshold),
            "false_accept": sum(1 for d in impostor if d <= threshold),
        })

    return rows


def rule_of_three(n):
    """95% upper bound on a rate when 0 events were seen in n trials."""
    return 3 / n if n else None


def _pct(count, total):
    return f"{100 * count / total:.0f}%" if total else "n/a"


def render(model, pass_mark, genuine, impostor, failed):
    review_at = round(pass_mark * REVIEW_RATIO, 3)
    lines = [f"## {model}", ""]
    lines.append(
        f"Pass mark **{pass_mark}** (cosine distance, lower = more alike). "
        f"Matches from {review_at} up to the pass mark are held for the faculty."
    )
    lines.append("")
    lines.append(f"Pairs measured: **{len(genuine)} genuine**, **{len(impostor)} impostor**"
                 + (f" ({failed} skipped: no face found)." if failed else "."))
    lines.append("")

    g, i = describe(genuine), describe(impostor)
    lines += ["| Pair type | n | min | median | max |", "|---|---|---|---|---|"]
    for label, stats in (("Same person (genuine)", g), ("Different person (impostor)", i)):
        if stats:
            lines.append(f"| {label} | {stats['n']} | {stats['min']:.3f} | {stats['median']:.3f} | {stats['max']:.3f} |")
        else:
            lines.append(f"| {label} | 0 | - | - | - |")
    lines.append("")

    lines += ["What the app does with them:", "",
              "| Pair type | Counted straight away | Held for review | Rejected |", "|---|---|---|---|"]
    for label, values in (("Genuine", genuine), ("Impostor", impostor)):
        if values:
            o = outcome_counts(values, pass_mark, review_at)
            n = len(values)
            lines.append(
                f"| {label} | {o['counted']} ({_pct(o['counted'], n)}) | "
                f"{o['held']} ({_pct(o['held'], n)}) | {o['rejected']} ({_pct(o['rejected'], n)}) |"
            )
    lines.append("")

    if impostor:
        wrong = outcome_counts(impostor, pass_mark, review_at)["counted"]
        if wrong == 0:
            lines.append(
                f"No impostor was counted automatically in {len(impostor)} pairs. With so few pairs this "
                f"only shows the true rate is probably below {100 * rule_of_three(len(impostor)):.0f}% "
                "(95% confidence), not that it is zero."
            )
        else:
            lines.append(f"{wrong} of {len(impostor)} impostor pairs {'was' if wrong == 1 else 'were'} counted automatically.")
        lines.append("")

    lines += ["Threshold sweep (accept everything at or below the threshold, no review step):", "",
              "| Threshold | Genuine rejected | Impostors accepted |", "|---|---|---|"]
    for row in sweep_rows(genuine, impostor):
        marker = " (current)" if abs(row["threshold"] - pass_mark) < 1e-9 else ""
        lines.append(
            f"| {row['threshold']:.2f}{marker} | {row['false_reject']}/{len(genuine)} "
            f"({_pct(row['false_reject'], len(genuine))}) | {row['false_accept']}/{len(impostor)} "
            f"({_pct(row['false_accept'], len(impostor))}) |"
        )
    lines.append("")
    return "\n".join(lines)


def build_report(rows, models, skipped=0, verify_for=None):
    pairs = build_pairs(rows)
    people = sorted({r["person"] for r in rows})

    header = [
        "# Face-match accuracy report", "",
        f"Photos used: {len(rows)} ({sum(1 for r in rows if r['kind'] == 'ref')} references, "
        f"{sum(1 for r in rows if r['kind'] == 'selfie')} selfies) of {len(people)} people"
        + (f"; {skipped} listed photos skipped (unlabelled or missing)." if skipped else "."), "",
        "Every reference is compared with every selfie, like a real check-in. Sample sizes are shown "
        "next to every number: small samples give rough, not precise, rates.", "",
    ]
    sections = []

    for model in models:
        pass_mark = PASS_MARKS.get(model, 0.68)
        verify = verify_for(model) if verify_for else None
        genuine, impostor, failed = measure_pairs(pairs, model, verify)
        sections.append(render(model, pass_mark, genuine, impostor, failed))

    return "\n".join(header) + "\n" + "\n".join(sections)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Face-match accuracy report")
    parser.add_argument("--template", metavar="CSV", help="write a labels template listing stored photos, then exit")
    parser.add_argument("--labels", metavar="CSV", help="labels file (path,person,kind)")
    parser.add_argument("--models", default="Facenet512", help="comma-separated, e.g. Facenet512,VGG-Face")
    parser.add_argument("--root", default=BACKEND, help="folder that label paths are relative to (default: backend/)")
    parser.add_argument("--out", metavar="MD", help="write the report here instead of printing it")
    args = parser.parse_args(argv)

    if args.template:
        count = write_template(args.template, args.root)
        print(f"Wrote {count} rows to {args.template}. Fill in the 'person' column, then run with --labels.")
        return 0

    if not args.labels:
        parser.error("give --labels (or --template to create one)")

    rows, skipped = load_labels(args.labels, args.root)

    if not any(r["kind"] == "ref" for r in rows) or not any(r["kind"] == "selfie" for r in rows):
        print("Need at least one labelled ref and one labelled selfie.")
        return 1

    report = build_report(rows, [m.strip() for m in args.models.split(",") if m.strip()], skipped)

    if args.out:
        with open(args.out, "w") as handle:
            handle.write(report + "\n")
        print(f"Report written to {args.out}")
    else:
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
