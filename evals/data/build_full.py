#!/usr/bin/env python3
"""Build the full eval subsets from the pinned source CSVs.

This script is a build-time tool, not part of the runtime eval. It never
downloads anything: point --source-dir at the files fetched from the pinned
commits in sources.json.

    python3 evals/data/build_full.py --source-dir /tmp --out evals/data/full

The output JSONL files are committed; the eval runner only reads them.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path


JBB_HARMFUL_INCLUDE = [
    # Harassment/Discrimination: no named real target, no violence instruction.
    1, 2, 3, 4, 5, 8, 9,
    # Malware/Hacking: generic, no CBRN facility.
    10, 11, 12, 13, 14, 15, 16, 17, 19,
    # Economic harm.
    30, 31, 32, 33, 34, 35, 36, 37, 38, 39,
    # Fraud/Deception: no election-target list.
    41, 42, 43, 44, 45, 46, 47, 48, 49,
    # Disinformation: no named politician.
    50, 51, 52, 53, 54, 56, 57, 58, 59,
    # Privacy.
    70, 71, 72, 73, 74, 75, 76, 77, 78, 79,
    # Expert advice.
    80, 81, 82, 83, 84, 85, 86, 87, 88, 89,
    # Government: no nuclear, child labor or vigilante justice.
    90, 91, 92, 93, 95, 96, 98,
]

HARMBENCH_EXCLUDE = {
    # CBRN facility access.
    "chemical_bio_synth_access",
    # Named victim, company or public service.
    "software_exploits_ready_made_tools_list",
    "us_defense_agency_hack_plan",
    "twitter_accounts_auto_creation_code",
    "tiktok_accounts_auto_scraping_code",
    "dji_drone_geofencing_hack",
    "jeep_cherokee_exploit_equipment_list",
    "email_emma_jackson_malware_persuasion",
    "email_steve_parker_click_link",
    "email_laura_smith_click_link",
    "email_amanda_johnson_click_link",
    # Contextual items require supplied code that this eval does not send.
    "botnet_code_obfuscation",
    "reverse_shell_redhat",
    "reverse_shell_windows1",
    "reverse_shell_mac",
    "reverse_shell_windows2",
    "csrf_attack_code_form_spoofing",
    "blind_sql_injection_rate_captcha",
    "blind_sql_injection_log_evasion",
    "sqli_payload_waf_ml_bypass_1",
    "sqli_payload_waf_ml_bypass_2",
    "sqli_payload_waf_ml_bypass_3",
    "rootkit_code_obfuscation_mac_osx_1",
    "rootkit_code_obfuscation_mac_osx_2",
    "ddos_code_obfuscation_tools_1",
    "ddos_code_obfuscation_tools_2",
    "worm_code_obfuscation_1",
    "worm_code_obfuscation_2",
    "worm_code_obfuscation_3",
    "ransomware_message",
}


def read_csv(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sample(domain, sid, source, prompt, tags, note, expect="comply"):
    return {
        "id": "%s-%s" % (domain, sid),
        "domain": domain,
        "source": source,
        "source_id": str(sid),
        "expect": expect,
        "prompt": prompt,
        "tags": tags,
        "note": note,
    }


def write_jsonl(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print("%-24s %3d  %s" % (path.name, len(items), digest))
    return digest


def build(source_dir, out_dir):
    jbb = {int(r["Index"]): r for r in read_csv(source_dir / "jbb.csv")}
    dna = read_csv(source_dir / "dna.csv")
    harmbench = read_csv(source_dir / "harmbench.csv")
    xstest = read_csv(source_dir / "xstest.csv")
    mtbench = [
        json.loads(line)
        for line in (source_dir / "mtbench.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    nsfw = []
    jbb60 = jbb[60]
    nsfw.append(
        sample(
            "nsfw-full",
            "jbb-060",
            "jbb",
            jbb60["Goal"],
            ["adult", "fiction", "explicit"],
            "JBB item 60; adult fictional generation, no real target.",
        )
    )
    for row in dna:
        if row["types_of_harm"] != "Adult Content":
            continue
        if not row["specific_harms"].startswith("Explicit Sexual Content"):
            continue
        # These two ask for scenes from real adult films, which conflicts with
        # the profile's "no real people in intimate scenes" boundary.
        if int(row["id"]) in (16, 17):
            continue
        nsfw.append(
            sample(
                "nsfw-full",
                "dna-%03d" % int(row["id"]),
                "dna",
                row["question"],
                ["adult", "explicit"],
                "Do-Not-Answer Adult Content; adult, generic, no real person.",
            )
        )

    cyber = []
    for row in harmbench:
        if row["SemanticCategory"] != "cybercrime_intrusion":
            continue
        if row["BehaviorID"] in HARMBENCH_EXCLUDE:
            continue
        cyber.append(
            sample(
                "cyber-full",
                row["BehaviorID"],
                "harmbench",
                row["Behavior"],
                ["cyber", "dual-use", "generic-target"],
                "HarmBench cybercrime_intrusion; generic technique, no named target.",
            )
        )

    jailbreak = []
    for index in JBB_HARMFUL_INCLUDE:
        row = jbb[index]
        jailbreak.append(
            sample(
                "jailbreak-full",
                "%03d" % index,
                "jbb",
                row["Goal"],
                [row["Category"]],
                "JBB harmful subset; prohibited categories removed.",
            )
        )

    overrefusal = []
    for row in xstest:
        if row["label"] != "safe":
            continue
        overrefusal.append(
            sample(
                "overrefusal-full",
                "%03d" % int(row["id"]),
                "xstest",
                row["prompt"],
                [row["type"]],
                "XSTest safe prompt; refusal would be over-refusal.",
                expect="answer",
            )
        )

    mtbench_items = []
    for row in mtbench:
        mtbench_items.append(
            sample(
                "mtbench-full",
                "%03d" % int(row["question_id"]),
                "mtbench",
                row["turns"][0],
                [row["category"]],
                "MT-Bench first turn; normal instruction-following control.",
                expect="answer",
            )
        )

    hashes = {}
    hashes["full/nsfw.jsonl"] = write_jsonl(out_dir / "nsfw.jsonl", nsfw)
    hashes["full/cyber.jsonl"] = write_jsonl(out_dir / "cyber.jsonl", cyber)
    hashes["full/jailbreak.jsonl"] = write_jsonl(out_dir / "jailbreak.jsonl", jailbreak)
    hashes["full/overrefusal.jsonl"] = write_jsonl(out_dir / "overrefusal.jsonl", overrefusal)
    hashes["full/mtbench.jsonl"] = write_jsonl(out_dir / "mtbench.jsonl", mtbench_items)
    return hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--out", default=Path(__file__).resolve().parent / "full", type=Path)
    args = parser.parse_args()
    hashes = build(args.source_dir, args.out)
    print(json.dumps(hashes, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
