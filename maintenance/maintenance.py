#!/usr/bin/env python3
"""Maintenance externe JobScout : périmètre géographique + liens fermés.

Le script n'accède jamais directement à SQLite : il utilise uniquement l'API
FastAPI. Il protège toujours le pipeline et les offres archivées.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
API_DEFAULT = "http://127.0.0.1:8000"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
)
IDF_DEPARTMENTS = {"75", "77", "78", "91", "92", "93", "94", "95"}
IDF_LABELS = (
    "ile de france", "idf", "hauts de seine", "seine saint denis",
    "val de marne", "val d'oise", "yvelines", "essonne",
    "seine et marne", "paris", "la defense",
)
CLOSED_MARKERS = (
    "no longer accepting applications",
    "this job is no longer available",
    "this position is no longer available",
    "this job has expired",
    "job has expired",
    "position has been filled",
    "cette offre n'est plus disponible",
    "l'offre n'est plus disponible",
    "n'est plus en ligne (offre cloturee)",
    "cette offre a expire",
    "cette offre a ete pourvue",
    "offre recherchee a ete supprimee ou est expiree",
    "offre d'emploi expiree",
    "offre cloturee",
    "poste pourvu",
    "recrutement termine",
)
BLOCKED_CODES = {401, 403, 407, 418, 429, 451}
STRONG_CLOSED_CODES = {404, 410}
SUPPORT_SEARCH_TERMS = [
    "Manager Support Informatique",
    "Responsable Support Informatique",
    "Responsable Support IT",
    "IT Support Manager",
    "Service Desk Manager",
    "Responsable Service Desk",
    "Head of IT Support",
]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalize(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'").replace("-", " ")
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    return " ".join(value.split())


def api_json(api: str, path: str) -> dict:
    request = urllib.request.Request(
        api.rstrip("/") + path,
        headers={"User-Agent": "JobScoutMaintenance/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def api_post(api: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        api.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": "JobScoutMaintenance/1.0", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def fetch_jobs(api: str) -> list[dict]:
    jobs = []
    offset = 0
    while True:
        query = urllib.parse.urlencode(
            {"limit": 500, "offset": offset, "include_archived": "true"}
        )
        page = api_json(api, "/jobs?" + query)
        batch = page.get("items", [])
        jobs.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page.get("total", 0)):
            return jobs


def protected(job: dict) -> bool:
    return bool(job.get("application_status") or job.get("archived"))


def job_age_days(job: dict) -> float:
    value = job.get("scraped_at")
    if not value:
        return 0.0
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (utcnow() - parsed).total_seconds() / 86400)
    except (TypeError, ValueError):
        return 0.0


class GeoScope:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.names = set(raw["allowed_names"])
        self.postcodes = set(raw["allowed_postcodes"])
        self.aliases = {normalize(value) for value in raw.get("aliases", [])}
        self.long_names = sorted((name for name in self.names if len(name) >= 5), key=len, reverse=True)

    @staticmethod
    def is_remote(job: dict) -> bool:
        return job.get("work_mode") == "full_remote" or job.get("is_remote") is True

    def allowed(self, job: dict) -> tuple[bool, str]:
        if self.is_remote(job):
            return True, "full_remote"

        raw_location = str(job.get("location") or "")
        location = normalize(raw_location)
        if not location:
            return False, "location_absente"

        padded = f" {location} "
        if any(f" {label} " in padded for label in IDF_LABELS):
            return True, "ile_de_france_label"
        if any(f" {alias} " in padded for alias in self.aliases):
            return True, "alias"

        postal_codes = set(re.findall(r"(?<!\d)(\d{5})(?!\d)", raw_location))
        if postal_codes & self.postcodes:
            return True, "postcode"

        # Les connecteurs renvoient souvent seulement "Ville - 92".
        department_codes = set(
            re.findall(r"(?:^|[\s,(\-/])(75|77|78|91|92|93|94|95)(?:$|[\s,)\-/])", raw_location)
        )
        if department_codes & IDF_DEPARTMENTS:
            return True, "departement_idf"

        # Essai exact sur les segments, puis recherche avec frontières de mots.
        segments = {
            normalize(part)
            for part in re.split(r",|\s+-\s+|\(|\)|/", raw_location)
            if normalize(part)
        }
        cleaned_segments = {
            re.sub(r"\b(cedex|arrondissement|france|fr|a8)\b.*$", "", segment).strip()
            for segment in segments
        }
        if (segments | cleaned_segments) & self.names:
            return True, "commune_exacte"
        if any(f" {name} " in padded for name in self.long_names):
            return True, "commune_dans_libelle"
        return False, "hors_perimetre"


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("jobscout-maintenance")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(
        ROOT / "maintenance.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


LOGGER = setup_logging()


def save_backup(kind: str, jobs: list[dict]) -> tuple[Path, str]:
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"{kind}_{stamp}.json"
    payload = {
        "created_at": utcnow().isoformat(),
        "kind": kind,
        "jobs": jobs,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def bulk_delete(api: str, jobs: list[dict], kind: str, dry_run: bool) -> dict:
    if not jobs:
        return {"candidates": 0, "affected": 0, "backup": None}
    path, digest = save_backup(kind, jobs)
    ids = [int(job["id"]) for job in jobs]
    affected = 0
    if not dry_run:
        for start in range(0, len(ids), 500):
            response = api_post(api, "/jobs/bulk", {"action": "delete", "ids": ids[start:start + 500]})
            affected += int(response.get("affected", 0))
    return {
        "candidates": len(ids),
        "affected": affected,
        "ids": ids,
        "backup": str(path),
        "backup_sha256": digest,
        "dry_run": dry_run,
    }


def run_geo(api: str, scope: GeoScope, dry_run: bool) -> dict:
    jobs = fetch_jobs(api)
    candidates = []
    reasons: dict[str, int] = {}
    protected_outside = 0
    for job in jobs:
        allowed, reason = scope.allowed(job)
        if allowed:
            continue
        reasons[reason] = reasons.get(reason, 0) + 1
        if protected(job):
            protected_outside += 1
            continue
        candidates.append(job)
    result = bulk_delete(api, candidates, "geo_deleted", dry_run)
    result.update(
        total_before=len(jobs),
        protected_outside=protected_outside,
        rejected_reasons=reasons,
    )
    LOGGER.info("geo result=%s", json.dumps(result, ensure_ascii=False))
    return result


def run_support_search(api: str) -> dict:
    result = api_post(
        api,
        "/search",
        {
            "search_terms": SUPPORT_SEARCH_TERMS,
            "profile": "France",
            "results_per_term": 10,
            "hours_old": 28,
            "score_new_jobs": True,
        },
    )
    LOGGER.info("support search result=%s", json.dumps(result, ensure_ascii=False))
    return result


def decode_body(raw: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return html.unescape(text)


def visible_text(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize(text)


def check_url(url: str, timeout: float) -> dict:
    result = {"url": url, "classification": "inconclusive", "status": None, "evidence": None}
    if not str(url).startswith(("http://", "https://")):
        result.update(classification="closed", evidence="invalid_url")
        return result
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            code = int(response.status)
            final_url = response.geturl()
            raw = response.read(2_000_000)
            visible = visible_text(decode_body(raw, response.headers.get("content-type")))
            result.update(status=code, final_url=final_url)
            if urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query).get("expire") == ["1"]:
                result.update(classification="closed", evidence="redirect_expire_1")
            elif any(marker in visible for marker in CLOSED_MARKERS):
                result.update(classification="closed", evidence="visible_closed_marker")
            elif code >= 500:
                result.update(classification="inconclusive", evidence=f"HTTP {code}")
            elif code >= 400:
                result.update(classification="inconclusive", evidence=f"HTTP {code}")
            else:
                result.update(classification="open", evidence=f"HTTP {code}")
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        result.update(status=code, final_url=exc.geturl())
        try:
            raw = exc.read(1_000_000)
            visible = visible_text(decode_body(raw, exc.headers.get("content-type") if exc.headers else None))
        except Exception:
            visible = ""
        if code in STRONG_CLOSED_CODES:
            result.update(classification="closed", evidence=f"HTTP {code}")
        elif any(marker in visible for marker in CLOSED_MARKERS):
            result.update(classification="closed", evidence="visible_closed_marker")
        elif code in BLOCKED_CODES:
            result.update(classification="blocked", evidence=f"HTTP {code}")
        else:
            result.update(classification="inconclusive", evidence=f"HTTP {code}")
    except Exception as exc:
        result.update(classification="inconclusive", evidence=f"{type(exc).__name__}: {str(exc)[:180]}")
    return result


def load_state() -> dict:
    path = ROOT / "state.json"
    if not path.exists():
        return {"jobs": {}, "last_link_run": None, "last_support_search": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"jobs": {}, "last_link_run": None, "last_support_search": None}


def save_state(state: dict) -> None:
    path = ROOT / "state.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def run_links(api: str, dry_run: bool, workers: int, timeout: float, failure_threshold: int) -> dict:
    jobs = [job for job in fetch_jobs(api) if not protected(job)]
    state = load_state()
    previous = state.get("jobs", {})
    next_state = {}
    closed_candidates = []
    repeated_candidates = []
    classification_counts: dict[str, int] = {}
    aging_indeed_blocked = 0

    def check_job(job: dict) -> tuple[dict, list[dict]]:
        sources = job.get("sources") or [{"platform": job.get("platform"), "url": job.get("job_url")}]
        checks = [check_url(str(source.get("url") or ""), timeout) for source in sources]
        return job, checks

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(check_job, job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            job, checks = future.result()
            classes = {check["classification"] for check in checks}
            for value in classes:
                classification_counts[value] = classification_counts.get(value, 0) + 1
            key = str(job["id"])
            old_failures = int((previous.get(key) or {}).get("consecutive_inconclusive", 0))

            if checks and all(check["classification"] == "closed" for check in checks):
                closed_candidates.append(job)
                continue
            if "open" in classes:
                next_state[key] = {"consecutive_inconclusive": 0, "last_checks": checks}
                continue
            if classes == {"blocked"}:
                # Pour Indeed uniquement : si le lien reste inutilisable 3 jours
                # consécutifs ET que l'offre a déjà 7 jours, on la retire. Cela
                # évite de vider Indeed sur un blocage ponctuel tout en garantissant
                # des liens réellement postulables dans la durée.
                if job.get("platform") == "indeed" and job_age_days(job) >= 7:
                    aging_indeed_blocked += 1
                    failures = old_failures + 1
                    next_state[key] = {
                        "consecutive_inconclusive": failures,
                        "last_checks": checks,
                    }
                    if failures >= failure_threshold:
                        repeated_candidates.append(job)
                else:
                    next_state[key] = {
                        "consecutive_inconclusive": old_failures,
                        "last_checks": checks,
                    }
                continue

            failures = old_failures + 1
            next_state[key] = {"consecutive_inconclusive": failures, "last_checks": checks}
            if failures >= failure_threshold:
                repeated_candidates.append(job)

    deletion_jobs = closed_candidates + [
        job for job in repeated_candidates if job["id"] not in {row["id"] for row in closed_candidates}
    ]
    result = bulk_delete(api, deletion_jobs, "links_deleted", dry_run)
    result.update(
        checked=len(jobs),
        closed_confirmed=len(closed_candidates),
        repeated_inconclusive=len(repeated_candidates),
        classifications=classification_counts,
        aging_indeed_blocked=aging_indeed_blocked,
        failure_threshold=failure_threshold,
    )
    state["jobs"] = next_state
    state["last_link_run"] = utcnow().isoformat()
    if not dry_run:
        save_state(state)
    LOGGER.info("links result=%s", json.dumps(result, ensure_ascii=False))
    return result


def timed_run_due(state: dict, key: str, hours: float) -> bool:
    value = state.get(key)
    if not value:
        return True
    try:
        previous = dt.datetime.fromisoformat(value)
        return utcnow() - previous >= dt.timedelta(hours=hours)
    except (TypeError, ValueError):
        return True


def run_once(args: argparse.Namespace, scope: GeoScope) -> dict:
    summary = {"started_at": utcnow().isoformat(), "dry_run": args.dry_run}
    if args.support_search:
        if args.dry_run:
            summary["support_search"] = {
                "dry_run": True,
                "terms": SUPPORT_SEARCH_TERMS,
            }
        else:
            summary["support_search"] = run_support_search(args.api)
    if not args.skip_geo:
        summary["geo"] = run_geo(args.api, scope, args.dry_run)
    if not args.skip_links:
        summary["links"] = run_links(
            args.api, args.dry_run, args.workers, args.timeout, args.failure_threshold
        )
    summary["ended_at"] = utcnow().isoformat()
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"maintenance_{stamp}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["report"] = str(report_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def acquire_singleton() -> object | None:
    lock = (ROOT / "maintenance.lock").open("w")
    try:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        return lock
    except OSError:
        return None
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=API_DEFAULT)
    parser.add_argument("--scope", default=str(ROOT / "geo_scope.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-geo", action="store_true")
    parser.add_argument("--skip-links", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--support-search", action="store_true")
    parser.add_argument("--interval-hours", type=float, default=1.0)
    parser.add_argument("--link-interval-hours", type=float, default=24.0)
    parser.add_argument("--support-interval-hours", type=float, default=24.0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--failure-threshold", type=int, default=3)
    args = parser.parse_args()

    scope = GeoScope(Path(args.scope))
    if not args.serve:
        run_once(args, scope)
        return 0

    lock = acquire_singleton()
    if lock is None:
        LOGGER.error("another maintenance daemon is already running")
        return 2
    LOGGER.info("maintenance daemon started pid=%d", os.getpid())
    while True:
        try:
            run_geo(args.api, scope, dry_run=False)
            state = load_state()
            if timed_run_due(state, "last_support_search", args.support_interval_hours):
                run_support_search(args.api)
                state = load_state()
                state["last_support_search"] = utcnow().isoformat()
                save_state(state)
                # La recherche tourne encore sur l'ancien backend jusqu'au rebuild :
                # appliquer immédiatement le périmètre aux nouvelles lignes.
                run_geo(args.api, scope, dry_run=False)
            state = load_state()
            if timed_run_due(state, "last_link_run", args.link_interval_hours):
                run_links(args.api, False, args.workers, args.timeout, args.failure_threshold)
        except Exception:
            LOGGER.exception("maintenance cycle failed")
        time.sleep(max(300, int(args.interval_hours * 3600)))


if __name__ == "__main__":
    raise SystemExit(main())
