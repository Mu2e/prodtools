#!/usr/bin/env python3
"""
Plot Mu2e tracker straw hit rates from an EventNtuple ROOT file.

Produces:
  hits_per_plane.png          -- hit count vs plane number (0-35)
  hits_per_panel.png          -- hit count vs panel number within plane (0-5)
  hits_per_straw.png          -- hit count vs straw number within panel (0-95)
  occupancy_plane_panel.png   -- 2D occupancy: plane x panel
  occupancy_panel_straw.png   -- 2D occupancy: absolute panel index x straw
  hits_per_plane_overlay.png  -- all hits vs active hits per plane

Requires:
    pip install git+https://github.com/Mu2e/pyutils.git

Usage:
    python plot_straw_hits.py <file.root> [options]

    # typical for from_dig-OnSpill output:
    python plot_straw_hits.py nts.owner.trkana-triggerMC.version.sequencer.root \\
        --tree EventNtupleTTMCTpr/ntuple

    # typical for standard reco output:
    python plot_straw_hits.py nts.mu2e.*.root \\
        --tree EventNtuple/ntuple
"""

import argparse
import os
import sys

import awkward as ak
import numpy as np

from pyutils.pyprocess import Processor
from pyutils.pyplot import Plot

# ---------------------------------------------------------------------------
# Mu2e tracker geometry constants
# ---------------------------------------------------------------------------
N_PLANES = 36   # planes in the tracker
N_PANELS = 6    # panels per plane
N_STRAWS = 96   # straws per panel


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="Plot Mu2e tracker straw hit rates using pyutils",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("file", help="EventNtuple ROOT file (local path)")
    p.add_argument(
        "--tree",
        default="EventNtupleTTMCTpr/ntuple",
        help="TTree path inside the ROOT file",
    )
    p.add_argument(
        "--outdir", default=".", help="Output directory for PNG plots"
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(file_name: str, tree_path: str) -> dict:
    processor = Processor(
        tree_path=tree_path,
        use_remote=False,
        verbosity=1,
    )
    data = processor.process_data(
        file_name=file_name,
        branches=["trkhits"],
    )
    return data


# ---------------------------------------------------------------------------
# Helper: flatten a per-track-hit field to a 1-D numpy array
# ---------------------------------------------------------------------------
def flat(hits, field: str):
    return ak.to_numpy(ak.flatten(hits[field], axis=None))


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------
def plot_hits_per_plane(hits_all, hits_active, plotter: Plot, outdir: str):
    """1-D: all hits and active-only hits per plane, overlaid."""
    plane_all    = flat(hits_all,    "plane")
    plane_active = flat(hits_active, "plane")

    plotter.plot_1D_overlay(
        {"All hits": plane_all, "Active hits": plane_active},
        nbins=N_PLANES,
        xmin=-0.5,
        xmax=N_PLANES - 0.5,
        title="Straw Hits per Plane",
        xlabel="Plane number",
        ylabel="Hit count",
        out_path=os.path.join(outdir, "hits_per_plane.png"),
        show=False,
    )
    print("  -> hits_per_plane.png")


def plot_hits_per_panel(hits_all, hits_active, plotter: Plot, outdir: str):
    """1-D: hit count per panel within plane, all and active overlaid."""
    panel_all    = flat(hits_all,    "panel")
    panel_active = flat(hits_active, "panel")

    plotter.plot_1D_overlay(
        {"All hits": panel_all, "Active hits": panel_active},
        nbins=N_PANELS,
        xmin=-0.5,
        xmax=N_PANELS - 0.5,
        title="Straw Hits per Panel (within plane)",
        xlabel="Panel number",
        ylabel="Hit count",
        out_path=os.path.join(outdir, "hits_per_panel.png"),
        show=False,
    )
    print("  -> hits_per_panel.png")


def plot_hits_per_straw(hits_all, hits_active, plotter: Plot, outdir: str):
    """1-D: hit count per straw within panel, all and active overlaid."""
    straw_all    = flat(hits_all,    "straw")
    straw_active = flat(hits_active, "straw")

    plotter.plot_1D_overlay(
        {"All hits": straw_all, "Active hits": straw_active},
        nbins=N_STRAWS,
        xmin=-0.5,
        xmax=N_STRAWS - 0.5,
        title="Straw Hits per Straw (within panel)",
        xlabel="Straw number",
        ylabel="Hit count",
        out_path=os.path.join(outdir, "hits_per_straw.png"),
        show=False,
    )
    print("  -> hits_per_straw.png")


def plot_occupancy_plane_panel(hits, plotter: Plot, outdir: str, tag: str = ""):
    """2-D occupancy: plane (x) vs panel (y)."""
    plane = flat(hits, "plane")
    panel = flat(hits, "panel")

    label = " (active only)" if tag else ""
    fname = f"occupancy_plane_panel{tag}.png"
    plotter.plot_2D(
        plane,
        panel,
        nbins_x=N_PLANES,
        nbins_y=N_PANELS,
        xmin=-0.5,
        xmax=N_PLANES - 0.5,
        ymin=-0.5,
        ymax=N_PANELS - 0.5,
        title=f"Straw Hit Occupancy{label}",
        xlabel="Plane number",
        ylabel="Panel number",
        cmap="inferno",
        out_path=os.path.join(outdir, fname),
        show=False,
    )
    print(f"  -> {fname}")


def plot_occupancy_panel_straw(hits, plotter: Plot, outdir: str, tag: str = ""):
    """2-D occupancy: absolute panel index (x) vs straw (y).

    Absolute panel index = plane * N_PANELS + panel  (range 0-215).
    """
    plane = flat(hits, "plane")
    panel = flat(hits, "panel")
    straw = flat(hits, "straw")
    abs_panel = plane * N_PANELS + panel

    label = " (active only)" if tag else ""
    fname = f"occupancy_panel_straw{tag}.png"
    plotter.plot_2D(
        abs_panel,
        straw,
        nbins_x=N_PLANES * N_PANELS,
        nbins_y=N_STRAWS,
        xmin=-0.5,
        xmax=N_PLANES * N_PANELS - 0.5,
        ymin=-0.5,
        ymax=N_STRAWS - 0.5,
        title=f"Straw Occupancy Map{label}",
        xlabel="Absolute panel index  (plane\u00d76 + panel)",
        ylabel="Straw number",
        cmap="inferno",
        out_path=os.path.join(outdir, fname),
        show=False,
    )
    print(f"  -> {fname}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    print(f"\nLoading  : {args.file}")
    print(f"Tree path: {args.tree}")

    data = load_data(args.file, args.tree)

    # Split into all-hits and active-only sub-collections
    hits_all    = data["trkhits"]
    hits_active = hits_all[hits_all["dactive"]]

    n_all    = int(ak.sum(ak.num(hits_all["plane"])))
    n_active = int(ak.sum(ak.num(hits_active["plane"])))
    n_tracks = int(ak.sum(ak.num(data["trkhits"]["plane"]) >= 0))

    print(f"\nTracks    : {n_tracks}")
    print(f"All hits  : {n_all}")
    print(f"Active    : {n_active}  ({100*n_active/max(n_all,1):.1f} %)")

    if n_all == 0:
        print("\nNo hits found — check that the tree path is correct and the")
        print("trigger SelectEvents condition was satisfied in this file.")
        sys.exit(0)

    plotter = Plot(verbosity=0)

    print(f"\nWriting plots to: {os.path.abspath(args.outdir)}/")

    plot_hits_per_plane(hits_all, hits_active, plotter, args.outdir)
    plot_hits_per_panel(hits_all, hits_active, plotter, args.outdir)
    plot_hits_per_straw(hits_all, hits_active, plotter, args.outdir)
    plot_occupancy_plane_panel(hits_all,    plotter, args.outdir, tag="")
    plot_occupancy_plane_panel(hits_active, plotter, args.outdir, tag="_active")
    plot_occupancy_panel_straw(hits_all,    plotter, args.outdir, tag="")
    plot_occupancy_panel_straw(hits_active, plotter, args.outdir, tag="_active")

    print("\nDone.")


if __name__ == "__main__":
    main()
