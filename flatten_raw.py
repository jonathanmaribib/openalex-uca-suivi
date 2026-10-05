#!/usr/bin/env python3
"""
flatten_raw.py (v3) : transforme un dump brut (raw_works.jsonl.gz) en deux CSV
exploitables (Power BI, pandas...), sans jamais retoucher l'API.

Sorties (dans le dossier du snapshot, ou --out-dir) :
  works.csv        1 ligne par work (dédoublonné sur openalex_id)
  authorships.csv  1 ligne par work x auteur x institution
                   (auteur sans institution = 1 ligne avec champs institution vides)
  work_labs.csv    1 ligne par work x labo UCA (table de liaison pour Power BI),
                   avec is_direct_uca (ligne "UCA elle-même") et n_authors_lab

Points clés :
  - IDs en forme courte (W..., A..., I...) pour faciliter les jointures
  - doi_norm : DOI en minuscules, sans préfixe https://doi.org/
  - match_key : clé de comparaison entre snapshots = DOI normalisé,
                sinon "t:<titre normalisé>|<année>" pour les works sans DOI
  - is_direct_uca / uca_child_* : calculés depuis le lineage de chaque institution

Usage :
    python flatten_raw.py snapshots/lineage_2026-10-02/raw_works.jsonl.gz
    python flatten_raw.py <raw> --uca-id I201841394 --out-dir sortie/
"""

import argparse
import csv
import gzip
import json
import os
import re
import unicodedata
from collections import Counter

DOI_PREFIXES = ("https://doi.org/", "http://doi.org/",
                "https://dx.doi.org/", "http://dx.doi.org/", "doi:")

WORKS_COLS = [
    "openalex_id", "doi_norm", "match_key", "is_xpac", "title", "publication_year", "publication_date",
    "type", "type_crossref", "language", "is_retracted", "cited_by_count",
    "created_date", "updated_date",
    "source_id", "source_name", "source_type", "issn_l",
    "is_oa", "oa_status", "oa_url", "best_oa_license", "pmid",
    "primary_topic", "primary_field", "primary_domain",
    "n_authorships", "n_authorships_uca", "has_uca_direct", "uca_child_ids", "uca_child_names",
]

AUTH_COLS = [
    "openalex_id", "doi_norm", "match_key", "author_rank", "author_position",
    "author_id", "author_name", "author_orcid", "is_corresponding",
    "raw_author_name", "raw_affiliation_strings",
    "institution_id", "institution_name", "institution_ror", "institution_country",
    "institution_type", "in_uca_lineage", "is_direct_uca",
]


LABS_COLS = [
    "openalex_id", "doi_norm", "match_key", "publication_year", "is_xpac",
    "lab_id", "lab_name", "is_direct_uca", "n_authors_lab",
]


def short(i):
    return i.rsplit("/", 1)[-1] if i else ""


def norm_doi(d):
    if not d:
        return ""
    d = d.strip().lower()
    for p in DOI_PREFIXES:
        if d.startswith(p):
            d = d[len(p):]
    return d


def norm_title(t):
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def get(d, *keys):
    """Accès sécurisé à des dicts imbriqués potentiellement None."""
    for k in keys:
        if not isinstance(d, dict):
            return ""
        d = d.get(k)
    return "" if d is None else d


def raw_affiliations(authorship):
    strings = authorship.get("raw_affiliation_strings")
    if not strings:
        strings = [a.get("raw_affiliation_string", "")
                   for a in (authorship.get("affiliations") or [])]
    return " | ".join(s for s in strings if s)


def main():
    ap = argparse.ArgumentParser(description="Aplatit un dump brut OpenAlex")
    ap.add_argument("raw_file")
    ap.add_argument("--uca-id", default="I201841394")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.raw_file))
    os.makedirs(out_dir, exist_ok=True)
    works_path = os.path.join(out_dir, "works.csv")
    auth_path = os.path.join(out_dir, "authorships.csv")
    labs_path = os.path.join(out_dir, "work_labs.csv")
    auth_uca_path = os.path.join(out_dir, "authorships_uca.csv")

    seen = set()
    stats = Counter()
    types = Counter()

    with gzip.open(args.raw_file, "rt", encoding="utf-8") as fin, \
            open(works_path, "w", newline="", encoding="utf-8") as fw, \
            open(auth_path, "w", newline="", encoding="utf-8") as fa, \
            open(labs_path, "w", newline="", encoding="utf-8") as fl, \
            open(auth_uca_path, "w", newline="", encoding="utf-8") as fu:

        ww = csv.writer(fw)
        wa = csv.writer(fa)
        wl = csv.writer(fl)
        wu = csv.writer(fu)
        ww.writerow(WORKS_COLS)
        wa.writerow(AUTH_COLS)
        wl.writerow(LABS_COLS)
        # version allégée : sans raw_affiliation_strings (colonne n°11)
        wu.writerow(AUTH_COLS[:10] + AUTH_COLS[11:])

        for line in fin:
            line = line.strip()
            if not line:
                continue
            w = json.loads(line)
            wid = short(w.get("id"))
            stats["lignes_lues"] += 1
            if wid in seen:
                stats["doublons_ignores"] += 1
                continue
            seen.add(wid)

            doi = norm_doi(w.get("doi"))
            year = w.get("publication_year") or ""
            title = w.get("title") or ""
            match_key = doi or f"t:{norm_title(title)}|{year}"
            # is_xpac absent d'un ancien dump (corpus core) = work du corpus principal
            is_xpac = bool(w.get("is_xpac", False))

            # ---- authorships
            n_auth = n_auth_uca = 0
            has_direct = False
            children = {}
            lab_authors = {}  # inst_id -> [nom, ensemble des rangs d'auteurs]
            for rank, a in enumerate(w.get("authorships") or [], 1):
                n_auth += 1
                author = a.get("author") or {}
                raw_aff = raw_affiliations(a)
                base = [
                    wid, doi, match_key, rank, a.get("author_position", ""),
                    short(author.get("id")), author.get("display_name", ""),
                    author.get("orcid") or "", a.get("is_corresponding", ""),
                    a.get("raw_author_name", ""), raw_aff,
                ]
                insts = a.get("institutions") or []
                author_in_uca = False

                if not insts:
                    wa.writerow(base + ["", "", "", "", "", False, False])
                    continue

                for inst in insts:
                    iid = short(inst.get("id"))
                    lineage = {short(x) for x in (inst.get("lineage") or [])}
                    in_uca = args.uca_id in lineage
                    is_direct = iid == args.uca_id
                    if in_uca:
                        author_in_uca = True
                        lab_authors.setdefault(
                            iid, [inst.get("display_name", ""), set()])[1].add(rank)
                        if is_direct:
                            has_direct = True
                        else:
                            children[iid] = inst.get("display_name", "")
                    row = base + [
                        iid, inst.get("display_name", ""), inst.get("ror") or "",
                        inst.get("country_code") or "", inst.get("type") or "",
                        in_uca, is_direct,
                    ]
                    wa.writerow(row)
                    if in_uca:
                        wu.writerow(row[:10] + row[11:])
                if author_in_uca:
                    n_auth_uca += 1

            # ---- work
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            oa = w.get("open_access") or {}
            topic = w.get("primary_topic") or {}
            types[w.get("type") or "(vide)"] += 1
            if not doi:
                stats["works_sans_doi"] += 1

            ww.writerow([
                wid, doi, match_key, is_xpac, title, year, w.get("publication_date", ""),
                w.get("type", ""), w.get("type_crossref", ""), w.get("language", ""),
                w.get("is_retracted", ""), w.get("cited_by_count", ""),
                w.get("created_date", ""), w.get("updated_date", ""),
                short(src.get("id")), src.get("display_name", ""), src.get("type", ""),
                src.get("issn_l", "") or "",
                oa.get("is_oa", ""), oa.get("oa_status", ""), oa.get("oa_url", "") or "",
                get(w, "best_oa_location", "license"),
                short(get(w, "ids", "pmid")),
                topic.get("display_name", ""), get(topic, "field", "display_name"),
                get(topic, "domain", "display_name"),
                n_auth, n_auth_uca, has_direct,
                "|".join(children.keys()), " | ".join(children.values()),
            ])
            stats["works_ecrits"] += 1
            if is_xpac:
                stats["works_xpac"] += 1

            for lab_id, (lab_name, ranks) in lab_authors.items():
                wl.writerow([wid, doi, match_key, year, is_xpac, lab_id, lab_name,
                             lab_id == args.uca_id, len(ranks)])
                stats["lignes_work_labs"] += 1

    print(f"Lignes lues        : {stats['lignes_lues']}")
    print(f"Doublons ignorés   : {stats['doublons_ignores']}")
    print(f"Works écrits       : {stats['works_ecrits']}  -> {works_path}")
    print(f"  dont corpus principal : {stats['works_ecrits'] - stats['works_xpac']}")
    print(f"  dont XPAC             : {stats['works_xpac']}")
    print(f"Works sans DOI     : {stats['works_sans_doi']} "
          f"({100 * stats['works_sans_doi'] / max(stats['works_ecrits'], 1):.1f} %)")
    print(f"Authorships        : -> {auth_path}")
    print(f"Authorships UCA    : allégé (Power BI) -> {auth_uca_path}")
    print(f"Work x labo        : {stats['lignes_work_labs']} lignes -> {labs_path}")
    print("Top types          :", ", ".join(f"{t} ({n})" for t, n in types.most_common(5)))


if __name__ == "__main__":
    main()
