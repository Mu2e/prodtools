#!/usr/bin/env python3
"""
Create a minimal template.fcl and JSON config, run json2jobdef, and list the tar.
"""
import json
import os
import subprocess
from pathlib import Path


def run(cmd):
    print("$", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(res.stdout)
    if res.returncode != 0:
        print(res.stderr)
        raise SystemExit(res.returncode)


def main():
    wd = Path.cwd()
    # 1) template.fcl
    Path("template.fcl").write_text("services: {}\nphysics: {}\n")

    # 2) minimal JSON config entry
    cfg = [{
        "simjob_setup": "/cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020w/setup.sh",
        "fcl": "template.fcl",
        "dsconf": "MDC2020zz",
        "desc": "TestDesc",
        "outloc": "disk",
        "owner": os.getenv("USER", "mu2e").replace("mu2epro", "mu2e"),
        "njobs": 1
    }]
    Path("test_jobdef.json").write_text(json.dumps(cfg, indent=2) + "\n")

    # 3) run
    run(["python3", "json2jobdef.py", "--json", "test_jobdef.json", "--index", "0"])

    # 4) show tar contents
    out = f"cnf.{cfg[0]['owner']}.{cfg[0]['desc']}.{cfg[0]['dsconf']}.0.tar"
    run(["tar", "tf", out])

    print("OK")


if __name__ == "__main__":
    main()


