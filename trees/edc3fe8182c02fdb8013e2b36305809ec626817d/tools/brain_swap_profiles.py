"""Extract exact historical swap ABIs from sealed official IDLs (offline)."""

import argparse
from pathlib import Path
from pprint import pformat

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify


def main():
    p = argparse.ArgumentParser()
    p.add_argument("roots", nargs="+", type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    profiles = {}
    for root in a.roots:
        verify(root)
        for path in sorted(root.glob("*.json")):
            d = read(path)
            if "instructions" not in d:
                continue
            profile = dict(address=d["address"], events={}, fields={}, instructions={})
            for name in ("BuyEvent", "SellEvent"):
                profile["events"][name] = next(
                    x["discriminator"] for x in d["events"] if x["name"] == name
                )
                profile["fields"][name] = [
                    (f["name"], f["type"])
                    for t in d["types"]
                    if t["name"] == name
                    for f in t["type"]["fields"]
                ]
            for ix in d["instructions"]:
                if ix["name"] in ("buy", "sell", "buy_exact_quote_in"):
                    profile["instructions"][ix["name"]] = dict(
                        discriminator=ix["discriminator"],
                        data_lengths=[
                            8
                            + sum(
                                8
                                if f["type"] == "u64"
                                else 1
                                if f["type"] == {"defined": {"name": "OptionBool"}}
                                else 100000
                                for f in ix["args"]
                            )
                        ],
                        accounts=[x["name"] for x in ix["accounts"]],
                    )
            pin = digest(profile)
            profiles.setdefault(pin, dict(profile, profile_sha=pin, provenance=[]))[
                "provenance"
            ].append(dict(commit=path.stem, sha=sha(path)))
    with a.output.open("x") as f:
        f.write(
            '"""Official historical swap schemas; see brain_swap_profiles.py."""\n\nPROFILES = '
            + pformat(list(profiles.values()), width=95, sort_dicts=False)
            + "\n"
        )


if __name__ == "__main__":
    main()
