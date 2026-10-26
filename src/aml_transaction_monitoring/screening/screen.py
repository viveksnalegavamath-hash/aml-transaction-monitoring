"""Screen KYC customers against the UK Sanctions List (S01) and test the matcher.

1. Screening: every SYNTHETIC KYC customer's name against the list
   (individuals vs sanctioned individuals, businesses vs sanctioned entities),
   in chunks on all CPU cores. Every name scoring at least the threshold is a
   match; date of birth and nationality set its priority (never drop it). The
   best-scoring name per customer and designation is kept. Writes
   marts.screening_matches and a marts.screening_runs audit row.
2. Test set: a labelled set of realistic variations of real list names
   (spelling, word order, missing middle name, ...), generated with a fixed
   seed into the separate schema screening_test, never mixed with the KYC
   table. A variation is caught when it scores at least the threshold against
   its OWN designation.
3. Report: docs/results/screening.md.

The KYC names are made up, so every KYC match is a false match by
construction; the test set is what shows that true matches are found.

Usage:
    uv run python -m aml_transaction_monitoring.screening.screen
"""

import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT
from aml_transaction_monitoring.screening.names import (
    dob_signal,
    load_spec,
    nationality_signal,
    normalise_name,
    prioritise,
    score_matrix,
    score_pair,
)

logger = logging.getLogger(__name__)

SCHEMA = "marts"
TEST_SCHEMA = "screening_test"
REPORT = PROJECT_ROOT / "docs" / "results" / "screening.md"
CHUNK = 5_000
KINDS = {"individual": ("individual", "Individual"), "business": ("entity", "Entity")}
NAME_TYPE_RANK = {"primary": 0, "primary_variation": 1, "alias": 2, "unknown": 3}


@dataclass(frozen=True)
class ListName:
    unique_id: str
    full_name: str
    name_type: str
    alias_quality: str | None


@dataclass(frozen=True)
class Designation:
    dob_dates: list[date]
    dob_years: list[int]
    countries: list[str]  # nationalities (individuals) or address countries (entities)


def load_list(
    conn: psycopg.Connection, table: str, entity: bool, spec: dict[str, Any]
) -> tuple[list[str], dict[str, list[ListName]], date]:
    """Distinct normalised list names, the list entries behind each, and the list date."""
    by_norm: dict[str, list[ListName]] = defaultdict(list)
    list_dates = set()
    rows = conn.execute(
        sql.SQL(
            "select unique_id, full_name, name_type, alias_quality, list_date from {}"
        ).format(sql.Identifier(SCHEMA, table))
    ).fetchall()
    for unique_id, full_name, name_type, quality, list_date in rows:
        norm = normalise_name(full_name, spec, entity)
        if norm:
            by_norm[norm].append(ListName(unique_id, full_name, name_type, quality))
        list_dates.add(list_date)
    if len(list_dates) != 1:
        raise ValueError(f"{table}: expected one list date, found {sorted(list_dates)}")
    return sorted(by_norm), by_norm, list_dates.pop()


def load_designations(conn: psycopg.Connection) -> dict[str, Designation]:
    """Dates of birth and countries per designation, for match priority."""
    rows = conn.execute(
        "select unique_id, designation_type, dob_dates, dob_years, nationality_isos,"
        " address_country_isos from marts.dim_sanctions_designation"
    ).fetchall()
    return {
        uid: Designation(
            dob_dates or [],
            dob_years or [],
            (nats if kind == "Individual" else addresses) or [],
        )
        for uid, kind, dob_dates, dob_years, nats, addresses in rows
    }


def best_matches(
    keys: list[Any],
    names: list[str],
    choices: list[str],
    by_norm: dict[str, list[ListName]],
    spec: dict[str, Any],
) -> dict[tuple[Any, str], tuple[float, ListName]]:
    """{(query key, designation): (best name score, list name)} at or above the threshold."""
    best: dict[tuple[Any, str], tuple[float, ListName]] = {}
    for start in range(0, len(names), CHUNK):
        scores = score_matrix(names[start : start + CHUNK], choices, spec)
        for i, j in zip(*np.nonzero(scores), strict=True):
            score = float(scores[i, j])
            for entry in by_norm[choices[j]]:
                key = (keys[start + i], entry.unique_id)
                rank = (
                    score,
                    -NAME_TYPE_RANK[entry.name_type],
                    entry.alias_quality != "low",
                )
                current = best.get(key)
                if current is None or rank > (
                    current[0],
                    -NAME_TYPE_RANK[current[1].name_type],
                    current[1].alias_quality != "low",
                ):
                    best[key] = (score, entry)
    return best


def screen_customers(
    conn: psycopg.Connection, spec: dict[str, Any], designations: dict[str, Designation]
) -> tuple[list[dict[str, Any]], dict[str, int], date]:
    """Screen every KYC customer; return match rows, customers screened per type, list date."""
    matches, screened, list_date = [], {}, None
    for customer_type, (table_kind, _) in KINDS.items():
        entity = table_kind == "entity"
        choices, by_norm, list_date = load_list(
            conn, f"sanctions_names_{table_kind}", entity, spec
        )
        customers = conn.execute(
            "select customer_id, full_name, date_of_birth, nationality_country"
            " from marts.dim_customer where customer_type = %s order by customer_id",
            (customer_type,),
        ).fetchall()
        screened[customer_type] = len(customers)
        clock = time.monotonic()
        best = best_matches(
            [c[0] for c in customers],
            [normalise_name(c[1], spec, entity) for c in customers],
            choices,
            by_norm,
            spec,
        )
        info = {c[0]: c for c in customers}
        for (customer_id, unique_id), (score, entry) in best.items():
            _, full_name, dob, country = info[customer_id]
            d = designations[unique_id]
            signals = prioritise(
                score,
                dob_signal(
                    dob,
                    d.dob_dates,
                    d.dob_years,
                    spec["priority"]["dob_year_tolerance"],
                ),
                nationality_signal(country, d.countries),
                entry.alias_quality == "low",
                spec,
            )
            matches.append(
                {
                    "customer_id": customer_id,
                    "customer_type": customer_type,
                    "customer_name": full_name,
                    "unique_id": unique_id,
                    "matched_name": entry.full_name,
                    "name_type": entry.name_type,
                    "alias_quality": entry.alias_quality,
                    "name_score": score,
                    "dob_signal": signals.dob,
                    "nationality_signal": signals.nationality,
                    "priority_score": signals.priority_score,
                    "priority_band": signals.priority_band,
                    "list_date": list_date,
                }
            )
        logger.info(
            "%s: %d customers screened against %d list names in %.0f s, %d matches",
            customer_type, len(customers), len(choices), time.monotonic() - clock,
            sum(m["customer_type"] == customer_type for m in matches),
        )  # fmt: skip
    return matches, screened, list_date


MATCH_COLUMNS = (
    "customer_id", "customer_type", "customer_name", "unique_id", "matched_name", "name_type",
    "alias_quality", "name_score", "dob_signal", "nationality_signal", "priority_score",
    "priority_band", "list_date",
)  # fmt: skip

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS {s}.screening_runs (
    run_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz NOT NULL DEFAULT now(),
    list_date           date        NOT NULL,
    spec_version        integer     NOT NULL,
    settings            jsonb       NOT NULL,
    customers_screened  jsonb       NOT NULL,
    matches             integer     NOT NULL
);
CREATE TABLE IF NOT EXISTS {s}.screening_matches (
    customer_id         text    NOT NULL,
    customer_type       text    NOT NULL,
    customer_name       text    NOT NULL,
    unique_id           text    NOT NULL,
    matched_name        text    NOT NULL,
    name_type           text    NOT NULL,
    alias_quality       text,
    name_score          numeric NOT NULL,
    dob_signal          text    NOT NULL,
    nationality_signal  text    NOT NULL,
    priority_score      numeric NOT NULL,
    priority_band       text    NOT NULL,
    list_date           date    NOT NULL,
    status              text    NOT NULL DEFAULT 'New',
    run_id              bigint  NOT NULL REFERENCES {s}.screening_runs (run_id),
    PRIMARY KEY (customer_id, unique_id)
);
"""


def write_matches(
    conn: psycopg.Connection,
    matches: list[dict[str, Any]],
    screened: dict[str, int],
    list_date: date,
    spec: dict[str, Any],
    started_at: datetime,
) -> int:
    """Replace the matches and log the run, in one transaction. Returns the run ID."""
    s = sql.Identifier(SCHEMA)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql.SQL(CREATE_SQL).format(s=s))
        cur.execute(
            sql.SQL(
                "INSERT INTO {}.screening_runs (started_at, list_date, spec_version, settings,"
                " customers_screened, matches) VALUES (%s, %s, %s, %s, %s, %s) RETURNING run_id"
            ).format(s),
            (started_at, list_date, spec["version"],
             Jsonb({"parameters": spec["parameters"], "priority": spec["priority"]}),
             Jsonb(screened), len(matches)),
        )  # fmt: skip
        run_id = cur.fetchone()[0]
        cur.execute(sql.SQL("DELETE FROM {}.screening_matches").format(s))
        columns = sql.SQL(", ").join(map(sql.Identifier, (*MATCH_COLUMNS, "run_id")))
        with cur.copy(
            sql.SQL("COPY {}.screening_matches ({}) FROM STDIN").format(s, columns)
        ) as copy:
            for m in matches:
                copy.write_row([*(m[c] for c in MATCH_COLUMNS), run_id])
    return run_id


# --- Labelled test set --------------------------------------------------------------

SPELLING = [
    ("mohammad", "muhammad"), ("muhammad", "mohammed"), ("mohammed", "mohamed"),
    ("abdul", "abdel"), ("abdel", "abdul"), ("hussein", "husain"), ("hussain", "hussein"),
    ("ahmad", "ahmed"), ("ahmed", "ahmad"), ("yusuf", "youssef"), ("aleksandr", "alexander"),
    ("alexander", "aleksandr"), ("sergei", "sergey"), ("sergey", "sergei"),
    ("yevgeny", "evgeny"), ("dmitry", "dmitri"), ("dmitri", "dmitry"),
    ("ou", "u"), ("kh", "h"), ("ii", "i"), ("y", "i"),
]  # fmt: skip


def spelling(name: str, rng: random.Random) -> str:
    """A realistic spelling variant: a transliteration swap, else a one-letter change."""
    words = name.split()
    for old, new in SPELLING:
        hits = [i for i, w in enumerate(words) if old in w.lower()]
        if hits:
            i = rng.choice(hits)
            words[i] = words[i].lower().replace(old, new, 1).upper()
            return " ".join(words)
    i = max(range(len(words)), key=lambda k: len(words[k]))
    w = words[i]
    if len(w) < 3:
        return name
    pos = rng.randrange(1, len(w))
    vowels = "AEIOU"
    repl = (
        rng.choice([v for v in vowels if v != w[pos].upper()])
        if w[pos].upper() in vowels
        else ""
    )
    words[i] = w[:pos] + repl + w[pos + 1 :]  # a vowel swapped, or a consonant dropped
    return " ".join(words)


def word_order(name: str) -> str:
    """Surname (last word) first, as often recorded."""
    words = name.split()
    return " ".join([words[-1], *words[:-1]])


def formatting(name: str, entity: bool) -> str:
    """Case, punctuation and accents changed; entities get a legal suffix."""
    words = [w.capitalize() for w in name.split()]
    if len(words) >= 2:
        words[0] = f"{words[0]}-{words.pop(1)}"
    text = " ".join(words)
    text = text.replace("e", "é", 1)
    return f"{text}, Ltd." if entity else text


def missing_middle(name: str, rng: random.Random) -> str | None:
    """A middle word dropped (needs three or more words)."""
    words = name.split()
    if len(words) < 3:
        return None
    words.pop(rng.randrange(1, len(words) - 1))
    return " ".join(words)


def build_test_set(
    conn: psycopg.Connection, spec: dict[str, Any]
) -> list[dict[str, Any]]:
    """Variations of real primary names, labelled with their own designation."""
    ts = spec["test_set"]
    rng = random.Random(ts["seed"])
    rows = []
    for kind, n in (("Individual", ts["individuals"]), ("Entity", ts["entities"])):
        candidates = conn.execute(
            "select unique_id, primary_name from marts.dim_sanctions_designation"
            " where designation_type = %s and primary_name like '%% %%' order by unique_id",
            (kind,),
        ).fetchall()
        for unique_id, name in rng.sample(candidates, n):
            entity = kind == "Entity"
            variants = {
                "exact": name,
                "formatting": formatting(name, entity),
                "spelling": spelling(name, rng),
                "word_order": word_order(name),
                "missing_middle": missing_middle(name, rng),
                "spelling_and_order": word_order(spelling(name, rng)),
            }
            for variation, text in variants.items():
                if text:
                    rows.append({"unique_id": unique_id, "designation_type": kind,
                                 "source_name": name, "variation": variation, "test_name": text})  # fmt: skip
    return rows


def write_test_set(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> None:
    """Replace screening_test.sanctions_variations (separate from the KYC table)."""
    s = sql.Identifier(TEST_SCHEMA)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(s))
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {}.sanctions_variations").format(s))
        cur.execute(
            sql.SQL(
                "CREATE TABLE {}.sanctions_variations (test_id integer PRIMARY KEY,"
                " unique_id text NOT NULL, designation_type text NOT NULL, source_name text NOT NULL,"
                " variation text NOT NULL, test_name text NOT NULL, own_score numeric NOT NULL,"
                " caught boolean NOT NULL)"
            ).format(s)
        )
        with cur.copy(
            sql.SQL(
                "COPY {}.sanctions_variations (test_id, unique_id, designation_type, source_name,"
                " variation, test_name, own_score, caught) FROM STDIN"
            ).format(s)
        ) as copy:
            for i, r in enumerate(rows, 1):
                copy.write_row([i, r["unique_id"], r["designation_type"], r["source_name"],
                                r["variation"], r["test_name"], r["own_score"], r["caught"]])  # fmt: skip


def score_test_set(
    conn: psycopg.Connection, rows: list[dict[str, Any]], spec: dict[str, Any]
) -> None:
    """Each variation's best score against its own designation's names (same scorer)."""
    own_names: dict[str, list[str]] = defaultdict(list)
    for table_kind in ("individual", "entity"):
        for uid, full_name in conn.execute(
            sql.SQL("select unique_id, full_name from {}").format(
                sql.Identifier(SCHEMA, f"sanctions_names_{table_kind}")
            )
        ):
            own_names[uid].append(full_name)
    threshold = spec["parameters"]["match_threshold"]
    for r in rows:
        entity = r["designation_type"] == "Entity"
        test = normalise_name(r["test_name"], spec, entity)
        r["own_score"] = max(
            score_pair(test, normalise_name(n, spec, entity), spec)
            for n in own_names[r["unique_id"]]
        )
        r["caught"] = r["own_score"] >= threshold


# --- Report ---------------------------------------------------------------------------


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def render(
    matches: list[dict[str, Any]],
    screened: dict[str, int],
    tests: list[dict[str, Any]],
    list_date: date,
    spec: dict[str, Any],
    seconds: float,
) -> str:
    """docs/results/screening.md."""
    t = spec["parameters"]["match_threshold"]
    out = [
        "# Sanctions screening (S01) — results",
        "",
        (
            f"UK Sanctions List dated **{list_date:%d %b %Y}** (FCDO, OGL v3.0) · threshold "
            f"**{t}** · spec version {spec['version']} · run took {seconds / 60:.1f} min."
        ),
        "",
        (
            "The KYC customers are SYNTHETIC (made-up names), so every KYC match is a false "
            "match by construction. The labelled test set shows that true matches are found."
        ),
        "",
        "## Matches per 10,000 customers",
        "",
        "| Customers | Screened | Customers matched | Per 10,000 | Match pairs | High | Medium | Low |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for kind in (*KINDS, "all"):
        ms = [m for m in matches if kind == "all" or m["customer_type"] == kind]
        n = sum(screened.values()) if kind == "all" else screened[kind]
        customers = len({m["customer_id"] for m in ms})
        bands = {
            b: sum(m["priority_band"] == b for m in ms)
            for b in ("High", "Medium", "Low")
        }
        out.append(
            f"| {kind} | {n:,} | {customers:,} | {10_000 * customers / n:.1f} | {len(ms):,} | "
            f"{bands['High']:,} | {bands['Medium']:,} | {bands['Low']:,} |"
        )
    out += [
        "",
        (
            "Priority = name score + evidence points (date of birth, nationality / country of "
            "incorporation, low-quality alias); High >= "
            f"{spec['priority']['bands']['high']}, Medium >= {spec['priority']['bands']['medium']}. "
            "No match is dropped for its evidence."
        ),
        "",
        "## Labelled test set: variations of real list names caught",
        "",
        (
            f"{len(tests):,} variations of {len({r['unique_id'] for r in tests}):,} real list names "
            "(schema `screening_test`, separate from the KYC table). Caught = scores at least the "
            "threshold against its own designation."
        ),
        "",
        "| Variation | Type | Tested | Caught at 85 | **Caught at 90** | Caught at 95 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in tests:
        groups[(r["variation"], r["designation_type"])].append(r["own_score"])
        groups[(r["variation"], "all")].append(r["own_score"])
    order = [
        "exact",
        "formatting",
        "spelling",
        "word_order",
        "missing_middle",
        "spelling_and_order",
    ]
    for variation in order:
        for kind in ("Individual", "Entity", "all"):
            s = np.array(groups.get((variation, kind), []))
            if s.size:
                out.append(
                    f"| {variation} | {kind} | {s.size:,} | {pct((s >= 85).mean())} | "
                    f"**{pct((s >= t).mean())}** | {pct((s >= 95).mean())} |"
                )
    s = np.array([r["own_score"] for r in tests])
    out.append(f"| **all** | all | {s.size:,} | {pct((s >= 85).mean())} | **{pct((s >= t).mean())}** | {pct((s >= 95).mean())} |")  # fmt: skip
    missed = sorted(
        (r for r in tests if not r["caught"]), key=lambda r: -r["own_score"]
    )[:6]
    out += ["", "Missed at the threshold (closest first):", "",
            "| Variation | Test name | Listed name | Score |", "| --- | --- | --- | --- |"]  # fmt: skip
    out += [
        f"| {r['variation']} | {r['test_name']} | {r['source_name']} | {r['own_score']:.0f} |"
        for r in missed
    ]
    out += [
        "",
        "## False-match examples (SYNTHETIC customers)",
        "",
        "| Customer (synthetic) | Type | Matched list name | Name type | Name score | DOB | Nationality | Priority |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    # The highest-priority false matches, then the lowest; one row per name pair
    # (synthetic customers can share a made-up name).
    seen, examples = set(), []
    for direction, wanted in ((-1, 4), (1, 2)):
        picked = 0
        for m in sorted(
            matches, key=lambda m: (direction * m["priority_score"], m["customer_id"])
        ):
            key = (m["customer_name"], m["matched_name"])
            if key not in seen and picked < wanted:
                seen.add(key)
                examples.append(m)
                picked += 1
    for m in examples:
        out.append(
            f"| {m['customer_name']} | {m['customer_type']} | {m['matched_name']} | "
            f"{m['name_type']}{' (low)' if m['alias_quality'] == 'low' else ''} | "
            f"{m['name_score']:.0f} | {m['dob_signal']} | {m['nationality_signal']} | "
            f"{m['priority_band']} ({m['priority_score']:.0f}) |"
        )
    return "\n".join(out) + "\n"


def main() -> None:
    """Screen, build and score the test set, write the report."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    spec = load_spec()
    started_at, clock = datetime.now().astimezone(), time.monotonic()
    with get_connection() as conn:
        designations = load_designations(conn)
        matches, screened, list_date = screen_customers(conn, spec, designations)
        run_id = write_matches(conn, matches, screened, list_date, spec, started_at)
        tests = build_test_set(conn, spec)
        score_test_set(conn, tests, spec)
        write_test_set(conn, tests)
    seconds = time.monotonic() - clock
    REPORT.write_text(
        render(matches, screened, tests, list_date, spec, seconds), encoding="utf-8"
    )
    logger.info("Run %d: %d matches; test set %d/%d caught; wrote %s", run_id, len(matches),
                sum(r["caught"] for r in tests), len(tests), REPORT)  # fmt: skip


if __name__ == "__main__":
    main()
