"""Helper for Claude-driven scoring of unscored JobScout jobs.

Modes:
    fetch N            -> print JSON list of N unscored jobs
    write < scores.json -> read JSON [{id, score, reasoning}], persist with enrichment

Lecture/ecriture directes sur la SQLite JobScout (volume docker). Importe
les helpers deterministes (geo/salary/freshness) depuis le backend existant.

Surcharges :
    JOBSCOUT_ROOT — racine du projet (defaut : dossier contenant ce script)
    JOBSCOUT_DB   — chemin de la SQLite live (defaut : volume docker jobscout-data)
Racine requise si la DB du volume Docker est root-owned.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = os.getenv("JOBSCOUT_ROOT", str(Path(__file__).resolve().parent))
sys.path.insert(0, f"{ROOT}/backend")
from enrichment import (  # noqa: E402
    compute_final_score,
    compute_freshness_score,
    compute_geo_score,
    compute_salary_score,
    detect_work_mode,
)

DB = os.getenv(
    "JOBSCOUT_DB",
    # Chemin par défaut pour un Docker Compose lancé depuis un dossier
    # nommé "jobscout" (Synology : /volume1/@docker/volumes/<projet>_jobscout-data/_data/jobs.db,
    # Linux générique : docker volume inspect jobscout_jobscout-data). Surcharge
    # systématiquement JOBSCOUT_DB si ton projet Compose porte un autre nom.
    "/volume1/@docker/volumes/jobscout_jobscout-data/_data/jobs.db",
)
MAX_DESC = 2000


def fetch(n: int) -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, company, job_type, platform, description "
        "FROM jobs WHERE relevance_score IS NULL "
        "ORDER BY id LIMIT ?",
        (n,),
    ).fetchall()
    for r in rows:
        desc = (r["description"] or "").strip()
        if len(desc) > MAX_DESC:
            desc = desc[:MAX_DESC] + "\n[...tronquee]"
        print(json.dumps({
            "id": r["id"],
            "title": r["title"],
            "company": r["company"] or "Inconnue",
            "job_type": r["job_type"] or "Non precise",
            "platform": r["platform"],
            "description": desc or "(aucune)",
        }, ensure_ascii=False))


def write() -> None:
    # Accept either JSON array or JSONL (one object per line).
    raw = sys.stdin.read().strip()
    if raw.startswith("["):
        scores = json.loads(raw)
    else:
        scores = [json.loads(line) for line in raw.splitlines() if line.strip()]
    con = sqlite3.connect(DB, timeout=30.0)
    con.row_factory = sqlite3.Row
    updated = 0
    skipped = 0
    for entry in scores:
        jid = int(entry["id"])
        base = float(entry["score"])
        reasoning = (entry.get("reasoning") or "")[:397]
        row = con.execute(
            "SELECT work_mode, location, description, salary_eur_min, salary_eur_max, "
            "salary_interval, date_posted, score_geo, score_salary, score_freshness, is_remote "
            "FROM jobs WHERE id=? AND relevance_score IS NULL",
            (jid,),
        ).fetchone()
        if row is None:
            skipped += 1
            continue

        wm = row["work_mode"] or detect_work_mode(row["description"], row["is_remote"])
        geo = row["score_geo"]
        if geo is None:
            geo = compute_geo_score(wm, row["location"], row["description"])
        sal = row["score_salary"]
        if sal is None:
            sal = compute_salary_score(
                row["salary_eur_min"], row["salary_eur_max"], row["salary_interval"]
            )
        fr = row["score_freshness"]
        if fr is None:
            fr = compute_freshness_score(row["date_posted"])

        final = compute_final_score(content=base, geo=geo, salary=sal, freshness=fr)
        con.execute(
            "UPDATE jobs SET base_score=?, relevance_score=?, relevance_reasoning=?, "
            "score_geo=?, score_salary=?, score_freshness=?, "
            "work_mode=COALESCE(work_mode, ?) "
            "WHERE id=?",
            (base, final, reasoning, geo, sal, fr, wm, jid),
        )
        updated += 1
    con.commit()
    print(json.dumps({"updated": updated, "skipped": skipped}))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "fetch":
        fetch(int(sys.argv[2]))
    elif mode == "write":
        write()
    else:
        sys.exit("usage: claude_score.py fetch N | write")
