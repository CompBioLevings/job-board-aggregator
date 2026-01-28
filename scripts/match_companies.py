#!/usr/bin/env python3
"""Match company names from a pipe-delimited text file against companies in a JSON file.

Usage example:
  python3 scripts/match_companies.py \
    --txt data/all-biotech.txt \
    --json data/workday_companies.json \
    --out data/workday_companies.filtered.json \
    --mapping-out data/workday_company_mapping.txt \
    --threshold 90
"""
from __future__ import annotations

import argparse
import json
import re
from typing import List, Any, Tuple
from multiprocessing import Pool, cpu_count

try:
    from fuzzywuzzy import fuzz
except Exception:
    fuzz = None


def normalize(s: str) -> str:
    if s is None:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def levenshtein(a: str, b: str) -> int:
    # Classic DP O(len(a)*len(b))
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            rem = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur[j] = min(ins, rem, sub)
        prev = cur
    return prev[lb]


def _best_match_for_name(args: Tuple[str, List[str]]) -> Tuple[str, str, float, float]:
    """Worker: given a json name and the list of txt names, return (json_name, best_match, score_by_shorter, fuzz_ratio)"""
    jn, names = args
    best_score = 0.0
    best_match = ""
    best_fuzz = 0.0
    jn_n = normalize(jn)
    for t in names:
        t_n = normalize(t)
        if not jn_n or not t_n:
            continue
        dist = levenshtein(jn_n, t_n)
        shorter = min(len(jn_n), len(t_n))
        score_shorter = max(0.0, (1.0 - dist / shorter) * 100.0) if shorter > 0 else 0.0
        fuzz_ratio = 0.0
        if fuzz is not None:
            try:
                fuzz_ratio = float(fuzz.ratio(jn_n, t_n))
            except Exception:
                fuzz_ratio = 0.0
        if score_shorter > best_score:
            best_score = score_shorter
            best_match = t
            best_fuzz = fuzz_ratio
    return (jn, best_match, best_score, best_fuzz)


def similarity_percent(a: str, b: str) -> float:
    a_n = normalize(a)
    b_n = normalize(b)
    if not a_n or not b_n:
        return 0.0
    dist = levenshtein(a_n, b_n)
    shorter = min(len(a_n), len(b_n))
    if shorter == 0:
        return 0.0
    return max(0.0, (1.0 - dist / shorter) * 100.0)


def read_biotech_txt(path: str) -> List[str]:
    names = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # split on pipe first, fall back to whitespace or comma
            if "|" in line:
                first = line.split("|", 1)[0]
            elif "\t" in line:
                first = line.split("\t", 1)[0]
            elif "," in line:
                first = line.split(",", 1)[0]
            else:
                first = line.split(None, 1)[0]
            names.append(first.strip())
    return names


def extract_names_from_json(data: Any, name_field: str | None = None) -> List[str]:
    # If data is a list of strings
    if isinstance(data, list):
        if not data:
            return []
        if all(isinstance(x, str) for x in data):
            return data
        # list of objects
        names = []
        for obj in data:
            if not isinstance(obj, dict):
                continue
            if name_field and name_field in obj:
                names.append(obj[name_field])
            else:
                # try common keys
                for key in ("name", "company", "company_name", "title"):
                    if key in obj:
                        names.append(obj[key])
                        break
        return names
    # If top-level is a dict, try to find a list value
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return extract_names_from_json(v, name_field)
    return []


def filter_json(data: Any, names_in_txt: List[str], threshold: float, name_field: str | None = None):
    # Returns filtered data preserving structure when possible
    if isinstance(data, list):
        if all(isinstance(x, str) for x in data):
            return [s for s in data if max((similarity_percent(s, t) for t in names_in_txt), default=0.0) >= threshold]
        filtered = []
        for obj in data:
            if isinstance(obj, dict):
                candidate = None
                if name_field and name_field in obj:
                    candidate = obj[name_field]
                else:
                    for key in ("name", "company", "company_name", "title"):
                        if key in obj:
                            candidate = obj[key]
                            break
                if candidate is None:
                    continue
                best = max((similarity_percent(candidate, t) for t in names_in_txt), default=0.0)
                if best >= threshold:
                    filtered.append(obj)
        return filtered
    if isinstance(data, dict):
        # find first list value and filter it
        new = dict(data)
        for k, v in data.items():
            if isinstance(v, list):
                new[k] = filter_json(v, names_in_txt, threshold, name_field)
                return new
    # fallback: return empty same-typed structure
    return []


def main() -> None:
    p = argparse.ArgumentParser(description="Filter companies in a JSON file by fuzzy match against a TXT list")
    p.add_argument("--txt", required=True, help="Path to pipe-delimited txt file (first column = company name)")
    p.add_argument("--json", required=True, help="Path to JSON file with companies")
    p.add_argument("--out", default=None, help="Output path for filtered JSON (default: append .filtered.json)")
    p.add_argument("--threshold", type=float, default=90.0, help="Similarity threshold percentage (default 90)")
    p.add_argument("--name-field", default=None, help="If JSON objects use a nonstandard name field, provide it")
    p.add_argument("--mapping-out", default=None, help="Optional pipe-delimited mapping output file: json_name|best_match|score")
    args = p.parse_args()

    names = read_biotech_txt(args.txt)
    with open(args.json, "r", encoding="utf-8") as f:
        data = json.load(f)

    # extract names for checking
    json_names = extract_names_from_json(data, args.name_field)

    # Optionally write mapping of matches (parallelized)
    pool_size = max(1, cpu_count() - 1)
    tasks = [(jn, names) for jn in json_names]
    mapping_lines: List[Tuple[str, str, float, float]] = []
    if tasks:
        with Pool(pool_size) as p:
            for res in p.imap_unordered(_best_match_for_name, tasks, chunksize=16):
                mapping_lines.append(res)

    # convert mapping_lines from tuples (jn, best_match, best_score, fuzz_ratio)

    # build a lookup from json name -> best_score
    best_by_name = {jn: score for (jn, _bm, score, _f) in mapping_lines}

    def _should_keep(obj_name: str) -> bool:
        return best_by_name.get(obj_name, 0.0) >= args.threshold

    # Use the mapping to filter while preserving structure
    def filter_using_map(data_obj: Any):
        if isinstance(data_obj, list):
            if all(isinstance(x, str) for x in data_obj):
                return [s for s in data_obj if _should_keep(s)]
            filtered_local = []
            for obj in data_obj:
                if isinstance(obj, dict):
                    candidate = None
                    if args.name_field and args.name_field in obj:
                        candidate = obj[args.name_field]
                    else:
                        for key in ("name", "company", "company_name", "title"):
                            if key in obj:
                                candidate = obj[key]
                                break
                    if candidate is None:
                        continue
                    if _should_keep(candidate):
                        filtered_local.append(obj)
            return filtered_local
        if isinstance(data_obj, dict):
            new = dict(data_obj)
            for k, v in data_obj.items():
                if isinstance(v, list):
                    new[k] = filter_using_map(v)
                    return new
        return []

    filtered = filter_using_map(data)

    out_path = args.out or (args.json + ".filtered.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, indent=2, ensure_ascii=False)

    if args.mapping_out:
        with open(args.mapping_out, "w", encoding="utf-8") as f:
            for jn, bm, sc, fs in mapping_lines:
                f.write(f"{jn}|{bm}|{sc:.2f}\n")

    print(f"Wrote filtered JSON to: {out_path}")


if __name__ == "__main__":
    main()
