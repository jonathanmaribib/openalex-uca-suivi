#!/usr/bin/env python3
"""
build_site_data.py : résume un snapshot (works.csv + work_labs.csv) en un petit
fichier JSON pour le site, dans docs/data/. Aucune donnée brute n'est publiée.

Usage :
    py build_site_data.py "C:\\...\\snapshots\\lineage_all_2026-10-02"
    py build_site_data.py <dossier_snapshot> <dossier_site>     # défaut : docs
"""
import csv, glob, json, os, sys
from collections import Counter, defaultdict

csv.field_size_limit(10**9)


def corpus(row):
    return "xpac" if row["is_xpac"] == "True" else "core"


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage : py build_site_data.py <dossier_snapshot> [dossier_site]")
    snap = sys.argv[1].rstrip("\\/")
    site = sys.argv[2] if len(sys.argv) > 2 else "docs"
    name = os.path.basename(snap)

    manifest = {}
    mpath = os.path.join(snap, "manifest.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)

    totals = {"core": Counter(), "xpac": Counter()}
    by_year, by_type, by_oa = (defaultdict(Counter) for _ in range(3))

    with open(os.path.join(snap, "works.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = corpus(r)
            totals[c]["works"] += 1
            if not r["doi_norm"]:
                totals[c]["sans_doi"] += 1
            if r["has_uca_direct"] == "True" and not r["uca_child_ids"]:
                totals[c]["direct_sans_labo"] += 1
            by_year[r["publication_year"] or "?"][c] += 1
            by_type[r["type"] or "(vide)"][c] += 1
            by_oa[r["oa_status"] or "(vide)"][c] += 1

    labs = {}
    with open(os.path.join(snap, "work_labs.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = labs.setdefault(r["lab_id"], {
                "id": r["lab_id"], "name": r["lab_name"],
                "direct": r["is_direct_uca"] == "True", "core": 0, "xpac": 0})
            d[corpus(r)] += 1

    out = {
        "snapshot": name,
        "date": (manifest.get("started_at") or "")[:10],
        "corpus": manifest.get("corpus", ""),
        "totals": {k: dict(v) for k, v in totals.items()},
        "by_year": {k: dict(v) for k, v in by_year.items()},
        "by_type": {k: dict(v) for k, v in by_type.items()},
        "by_oa": {k: dict(v) for k, v in by_oa.items()},
        "labs": sorted(labs.values(), key=lambda x: -(x["core"] + x["xpac"])),
    }

    data_dir = os.path.join(site, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    # index de tous les snapshots disponibles
    index = []
    for p in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        if os.path.basename(p) == "index.json":
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        index.append({"file": os.path.basename(p), "snapshot": d["snapshot"],
                      "date": d["date"], "corpus": d["corpus"]})
    with open(os.path.join(data_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    n = sum(t.get("works", 0) for t in totals.values())
    print(f"OK : {name} ({n} works, {len(labs)} labos) -> {data_dir}")


if __name__ == "__main__":
    main()
