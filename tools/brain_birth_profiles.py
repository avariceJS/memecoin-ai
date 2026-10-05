"""Extract mechanical birth layouts from exact official IDL artifacts (offline)."""

import argparse
import copy
from pathlib import Path
from pprint import pformat

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify


def main():
    p = argparse.ArgumentParser()
    p.add_argument("roots", nargs="+", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    profiles = {}
    for root in a.roots:
        verify(root)
        for path in sorted(root.glob("*.json")):
            d = read(path)
            if "instructions" not in d:
                continue
            ix = next(i for i in d["instructions"] if i["name"] == "create_pool")
            event = next(t for t in d["types"] if t["name"] == "CreatePoolEvent")
            args = copy.deepcopy(ix["args"])
            for arg in args:
                if isinstance(arg["type"], dict):
                    typ = next(
                        t["type"] for t in d["types"] if t["name"] == arg["type"]["defined"]["name"]
                    )
                    if typ != dict(kind="struct", fields=["bool"]):
                        break
                    arg["type"] = "bool"
            else:
                profile = dict(
                    accounts=[v["name"] for v in ix["accounts"]],
                    args=args,
                    fields=event["type"]["fields"],
                    discriminator=next(
                        v["discriminator"] for v in d["events"] if v["name"] == "CreatePoolEvent"
                    ),
                )
                pin = digest(profile)
                profiles.setdefault(pin, dict(profile, profile_sha=pin, provenance=[]))[
                    "provenance"
                ].append(
                    dict(
                        commit=path.stem,
                        idl_sha=sha(path),
                        url=f"https://raw.githubusercontent.com/pump-fun/pump-public-docs/{path.stem}/idl/pump_amm.json",
                    )
                )
    with a.output.open("x") as f:
        f.write(
            '"""Official historical IDL facts; see brain_birth_profiles.py."""\n\nPROFILES = '
            + pformat(list(profiles.values()), width=95, sort_dicts=False)
            + "\n"
        )


if __name__ == "__main__":
    main()
