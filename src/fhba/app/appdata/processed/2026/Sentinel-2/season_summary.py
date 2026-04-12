"""
Flint Hills Burn Season Summary
================================
Unions all per-date burn-mask GeoTIFFs produced by plot_sentinel2_30m_pdf.py
to produce a single multi-page PDF report of cumulative burned acreage.

Key rules
---------
* A pixel burned on multiple dates is counted ONLY ONCE (logical-OR union).
* No cloud masking is applied here – it was already baked into each per-date
  GeoTIFF when plot_sentinel2_30m_pdf.py ran.
* The NLCD land-use mask (classes 71 Grassland / 81 Pasture/Hay) is re-applied
  to compute the burnable-acres denominator and to guard against any stray
  pixels at raster edges after merging.
* Only pixels inside an FH county boundary are counted.

Usage
-----
    # Pass files explicitly
    python season_summary.py processed/2026/Sentinel-2/*_burn_mask.tif

    # Optional output path
    python season_summary.py *.tif --output my_report.pdf

Output
------
    <output_dir>/season_summary_<first_date>_to_<last_date>.pdf
    (default: same directory as the first GeoTIFF)

PDF structure
-------------
    Page 1   – Region overview: county reference map | cumulative burn union
    Pages 2+ – Per-county zoom: county context | burn detail
    Last     – Summary table: acres burned, burnable acres, % burned per county
"""

import os
import re
import math
import glob as _glob
import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_pdf import PdfPages
import geopandas as gpd
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds, reproject as rio_reproject
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.merge import merge as rio_merge

# ── Command-line arguments ─────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="Flint Hills season burn summary from per-date GeoTIFFs")
_parser.add_argument(
    "tif_files",
    nargs="+",
    help="One or more *_burn_mask.tif files produced by plot_sentinel2_30m_pdf.py")
_parser.add_argument(
    "--output", "-o",
    default=None,
    help="Output PDF path (default: season_summary_<dates>.pdf next to first TIF)")
_args = _parser.parse_args()

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE    = os.path.dirname(os.path.abspath(__file__))
APPDATA = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

NLCD_TIF   = os.path.join(APPDATA, "annual_nlcd",
                           "Annual_NLCD_LndCov_2024_CU_C1V1.tif")
COUNTY_SHP = os.path.join(APPDATA, "_static",
                           "FH_Counties_Updated.shp", "FH_Counties_Updated.shp")

# ── Settings ──────────────────────────────────────────────────────────────────
KEEP_CLASSES   = {71, 81}          # Grassland/Herbaceous, Pasture/Hay
TARGET_RES_M   = 30
DST_CRS        = CRS.from_epsg(3857)
# PIXEL_ACRES computed after Step 2 merge (needs actual pixel size + Mercator correction)
COUNTY_PAD_FRAC = 0.08

# Display colours (RGB 0-255)
CLR_BACKGROUND = np.array([255, 255, 255], dtype=np.uint8)   # white
CLR_COUNTY_BG  = np.array([235, 235, 235], dtype=np.uint8)   # light gray
CLR_BURN       = np.array([220,  30,  30], dtype=np.uint8)   # red
CLR_BURN_NORM  = CLR_BURN / 255.0


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_date(path):
    """Pull YYYY-MM-DD from a filename; return empty string if not found."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return m.group(1) if m else ""


def _build_display(county_raster, burn_union_fh, ny, nx):
    """
    Build an (ny, nx, 3) float32 RGB array:
      white background → county areas light gray → burns red.
    """
    img = np.ones((ny, nx, 3), dtype=np.float32)           # white
    img[county_raster > 0] = CLR_COUNTY_BG / 255.0         # county area gray
    img[burn_union_fh]     = CLR_BURN_NORM                  # burns red
    return img


def _add_county_labels(ax, counties, name_field, fontsize=7, color="0.25"):
    """Plot county name at the interior centroid of each geometry."""
    for _, row in counties.iterrows():
        pt = row.geometry.representative_point()
        ax.text(pt.x, pt.y, str(row[name_field]),
                ha="center", va="center", fontsize=fontsize,
                color=color, clip_on=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1  Validate + sort input GeoTIFFs
#
#    Windows cmd.exe does NOT expand glob wildcards before passing arguments to
#    Python (unlike bash/zsh).  We expand every argument with glob.glob() so
#    that patterns like  *_burn_mask.tif  work on all platforms.
#    When a pattern contains no path separator the script's own directory is
#    used as the search root, matching the behaviour a user expects when they
#    cd into the directory and run  python season_summary.py *_burn_mask.tif
# ═══════════════════════════════════════════════════════════════════════════════
def _expand(pattern):
    """Expand a single glob pattern; return [pattern] unchanged if it is an
    existing literal path (no wildcards present)."""
    if os.path.exists(pattern):
        return [pattern]
    # If the pattern has no directory part, search in the script's directory
    if not os.path.dirname(pattern):
        pattern = os.path.join(HERE, pattern)
    matches = _glob.glob(pattern)
    return sorted(matches) if matches else []

raw_args  = _args.tif_files
expanded  = []
for arg in raw_args:
    hits = _expand(arg)
    if hits:
        expanded.extend(hits)
    else:
        print(f"  WARNING: no files matched '{arg}' – skipping.")

tif_files = sorted(expanded, key=_extract_date)
if not tif_files:
    raise SystemExit("No GeoTIFF files found. Check the pattern or path.")

dates = [_extract_date(f) for f in tif_files]
dates_clean = [d for d in dates if d]

print(f"Season summary: {len(tif_files)} GeoTIFF(s)")
for f, d in zip(tif_files, dates):
    print(f"  {d or '?':10s}  {os.path.basename(f)}")

first_date = dates_clean[0]  if dates_clean else "unknown"
last_date  = dates_clean[-1] if dates_clean else "unknown"
year_str   = first_date[:4]  if dates_clean else ""

# Output PDF path
if _args.output:
    OUT_PDF = os.path.abspath(_args.output)
else:
    _out_dir = os.path.dirname(os.path.abspath(tif_files[0]))
    _pdf_name = f"season_summary_{first_date}_to_{last_date}.pdf"
    OUT_PDF = os.path.join(_out_dir, _pdf_name)


# ═══════════════════════════════════════════════════════════════════════════════
# 2  Union all burn-mask GeoTIFFs → single boolean burn_union array
#
#    rasterio.merge with method='max' on binary (0/1) rasters produces the
#    logical OR: a pixel is 1 if ANY input date detected a burn there.
#    Different scene extents are handled automatically – the output covers the
#    union of all input bounding boxes; pixels not covered by any input are 0.
# ═══════════════════════════════════════════════════════════════════════════════
print("\nStep 2 – Merging burn GeoTIFFs (union / logical-OR) ...")

open_datasets = [rasterio.open(f) for f in tif_files]
try:
    merged_arr, merged_transform = rio_merge(open_datasets, method="max")
finally:
    for ds in open_datasets:
        ds.close()

# merged_arr shape: (1, ny, nx)
burn_union = merged_arr[0].astype(bool)      # True = burned on ≥ 1 date

ny_tgt, nx_tgt = burn_union.shape
dst_transform   = merged_transform

# Reconstruct spatial extent from transform + shape
x_left   = merged_transform.c                          # left edge
y_top    = merged_transform.f                          # top edge
x_right  = x_left + merged_transform.a * nx_tgt
y_bottom = y_top  + merged_transform.e * ny_tgt        # e is negative

print(f"  Merged grid : {nx_tgt} x {ny_tgt} px")
print(f"  Extent      : X [{x_left:.0f}, {x_right:.0f}]  "
      f"Y [{y_bottom:.0f}, {y_top:.0f}]  (EPSG:3857)")

# ── PIXEL_ACRES: correct for EPSG:3857 Mercator area distortion ───────────────
# GeoTIFFs are in EPSG:3857; pixel spacing is ~30 m in projected units, but
# Web Mercator stretches distances by 1/cos(lat), so each pixel covers only
#   px_m × py_m × cos²(lat)  true m² of ground (~562 m² at 38°N, not 900 m²).
_px_m   = abs(merged_transform.a)   # pixel width  in EPSG:3857 m
_py_m   = abs(merged_transform.e)   # pixel height in EPSG:3857 m
_R_earth = 6_378_137.0
_y_center_3857  = (y_bottom + y_top) / 2.0
_lat_center_rad = 2.0 * math.atan(math.exp(_y_center_3857 / _R_earth)) - math.pi / 2.0
_cos_lat        = math.cos(_lat_center_rad)
_pixel_ground_m2 = _px_m * _py_m * (_cos_lat ** 2)
PIXEL_ACRES = _pixel_ground_m2 / 4046.856
print(f"  Centre lat {math.degrees(_lat_center_rad):.2f}°, "
      f"Mercator scale={_cos_lat:.4f}, "
      f"pixel ground area={_pixel_ground_m2:.1f} m²  →  {PIXEL_ACRES:.4f} ac/px")

print(f"  Burn pixels : {burn_union.sum():,}  "
      f"({burn_union.sum() * PIXEL_ACRES:,.0f} acres, pre-masking)")


# ═══════════════════════════════════════════════════════════════════════════════
# 3  NLCD land-use mask on the merged grid
#
#    Re-applied here so that the burnable-acres denominator is consistent with
#    the merged spatial extent, and to catch any edge pixels that crept in
#    during the merge step.
# ═══════════════════════════════════════════════════════════════════════════════
print("\nStep 3 – Building NLCD land-use mask on merged grid ...")

with rasterio.open(NLCD_TIF) as nlcd_src:
    nlcd_crs = nlcd_src.crs
    nl, nb, nr, nt = transform_bounds(
        "EPSG:3857", nlcd_crs, x_left, y_bottom, x_right, y_top)
    pad = 0.01
    window = nlcd_src.window(
        nl - (nr - nl) * pad, nb - (nt - nb) * pad,
        nr + (nr - nl) * pad, nt + (nt - nb) * pad,
    ).intersection(
        rasterio.windows.Window(0, 0, nlcd_src.width, nlcd_src.height))
    win_transform = nlcd_src.window_transform(window)
    win_data = nlcd_src.read(1, window=window)

nlcd_30 = np.zeros((ny_tgt, nx_tgt), dtype=np.uint8)
rio_reproject(
    source=win_data, destination=nlcd_30,
    src_transform=win_transform, src_crs=nlcd_crs,
    dst_transform=dst_transform, dst_crs=DST_CRS,
    resampling=Resampling.nearest,
)
land_ok = np.isin(nlcd_30, list(KEEP_CLASSES))
print(f"  NLCD mask keeps {land_ok.mean() * 100:.1f}% of merged-grid pixels")


# ═══════════════════════════════════════════════════════════════════════════════
# 4  County rasterization on the merged grid
# ═══════════════════════════════════════════════════════════════════════════════
print("\nStep 4 – Rasterizing FH county boundaries ...")

counties    = gpd.read_file(COUNTY_SHP).to_crs(epsg=3857)
counties    = counties.reset_index(drop=True)
counties["_cid"] = counties.index + 1

_name_candidates = ["NAME", "Name", "name", "COUNTY", "County", "county",
                    "COUNTYNAME", "CountyName"]
_name_field = next((c for c in _name_candidates if c in counties.columns),
                   counties.columns[0])

# Build a display name that includes the state abbreviation, e.g. "Osage County OK"
_abbr_candidates = ["STATE_ABBR", "State_Abbr", "state_abbr", "STATEABBR", "ST"]
_abbr_field = next((c for c in _abbr_candidates if c in counties.columns), None)
if _abbr_field:
    counties["_display_name"] = (counties[_name_field].str.strip()
                                 + " " + counties[_abbr_field].str.strip())
else:
    counties["_display_name"] = counties[_name_field]

county_raster = rasterize(
    [(geom, cid) for geom, cid in zip(counties.geometry, counties["_cid"])],
    out_shape=(ny_tgt, nx_tgt),
    transform=dst_transform,
    fill=0, dtype=np.int32,
)
fh_mask = county_raster > 0
print(f"  {int(counties['_cid'].max())} counties rasterized")


# ═══════════════════════════════════════════════════════════════════════════════
# 5  Apply masks and compute per-county statistics
#
#    burn_union already has NLCD and cloud masking baked in from each per-date
#    run.  We re-intersect with land_ok and fh_mask as a safety net (handles
#    any sub-pixel shifts at raster edges after merging).
#
#    Burnable acres = NLCD 71/81 pixels inside the county boundary.
#    No cloud masking is applied to the denominator; the season-wide burnable
#    area reflects the full valid landcover, not just cloud-free observations.
# ═══════════════════════════════════════════════════════════════════════════════
print("\nStep 5 – Computing per-county statistics ...")

burn_union_fh = burn_union & land_ok & fh_mask   # final masked union

county_stats = {}
print(f"\n  {'County':<22}  {'Burned ac':>11}  {'Burnable ac':>12}  {'%':>6}")
print(f"  {'-'*22}  {'-'*11}  {'-'*12}  {'-'*6}")
for _, row in counties.iterrows():
    cid  = row["_cid"]
    name = str(row["_display_name"])
    in_county      = county_raster == cid
    burned_acres   = float((burn_union_fh & in_county).sum() * PIXEL_ACRES)
    burnable_acres = float((land_ok       & in_county).sum() * PIXEL_ACRES)
    pct            = (burned_acres / burnable_acres * 100) if burnable_acres > 0 else 0.0
    county_stats[name] = {"burned": burned_acres, "burnable": burnable_acres, "pct": pct}
    print(f"  {name:<22}  {burned_acres:>11,.0f}  {burnable_acres:>12,.0f}  {pct:>5.1f}%")

total_burned   = sum(v["burned"]   for v in county_stats.values())
total_burnable = sum(v["burnable"] for v in county_stats.values())
total_pct      = (total_burned / total_burnable * 100) if total_burnable > 0 else 0.0
print(f"  {'TOTAL':<22}  {total_burned:>11,.0f}  {total_burnable:>12,.0f}  {total_pct:>5.1f}%\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 6  Build display image
#    Since we have no satellite imagery, we use a clean synthetic map:
#      white background → county interiors light gray → burns red
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 6 – Building display image ...")

display_img = _build_display(county_raster, burn_union_fh, ny_tgt, nx_tgt)

dx = abs(merged_transform.a)   # pixel width  (positive)
dy = abs(merged_transform.e)   # pixel height (positive)
extent = (x_left  - dx / 2, x_right + dx / 2,
          y_bottom - dy / 2, y_top   + dy / 2)

# Legend handles (shared across pages)
_burn_patch = mpatches.Patch(color=CLR_BURN_NORM,      label="Burned (union)")
_land_patch = mpatches.Patch(color=CLR_COUNTY_BG/255., label="Burnable land (NLCD 71/81)")
_none_patch = mpatches.Patch(color="white",            label="Non-burnable / outside FH",
                              edgecolor="0.75")

date_label = (f"{first_date}" if first_date == last_date
              else f"{first_date} → {last_date}")
n_dates    = len(tif_files)


# ═══════════════════════════════════════════════════════════════════════════════
# 7  Render PDF
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 7 – Rendering PDF ...")

with PdfPages(OUT_PDF) as pdf:

    # ── Page 1: region overview ────────────────────────────────────────────────
    fig, (ax_l, ax_r) = plt.subplots(
        1, 2, figsize=(20, 18), dpi=150,
        gridspec_kw={"wspace": 0.04},
    )
    fig.patch.set_facecolor("white")

    # Left – county reference map (labels only, no burns)
    county_ref = _build_display(county_raster,
                                np.zeros_like(burn_union_fh), ny_tgt, nx_tgt)
    ax_l.imshow(county_ref, extent=extent, origin="upper",
                interpolation="nearest", aspect="equal")
    counties.boundary.plot(ax=ax_l, color="black", linewidth=0.7, zorder=3)
    _add_county_labels(ax_l, counties, "_display_name", fontsize=7)
    ax_l.set_title(
        f"Flint Hills Region – County Reference\n"
        f"Burn Season {year_str}  |  {n_dates} date(s): {date_label}",
        fontsize=10)
    ax_l.set_xlabel("Easting (m, EPSG:3857)")
    ax_l.set_ylabel("Northing (m, EPSG:3857)")
    ax_l.tick_params(labelsize=7)

    # Right – cumulative burn union
    ax_r.imshow(display_img, extent=extent, origin="upper",
                interpolation="nearest", aspect="equal")
    counties.boundary.plot(ax=ax_r, color="black", linewidth=0.7, zorder=3)
    ax_r.set_title(
        f"Cumulative Burn Union  –  {n_dates} date(s): {date_label}\n"
        f"Burns (red) = {total_burned:,.0f} acres  "
        f"({total_pct:.1f}% of burnable land)  |  NLCD 71/81 only",
        fontsize=10)
    ax_r.set_xlabel("Easting (m, EPSG:3857)")
    ax_r.set_ylabel("")
    ax_r.tick_params(labelsize=7)
    ax_r.legend(handles=[_burn_patch, _land_patch, _none_patch],
                loc="lower right", fontsize=7, framealpha=0.85)

    # Date list annotation on the right panel
    date_lines = "\n".join(f"  • {d}" for d in dates_clean)
    ax_r.text(0.01, 0.01,
              f"Dates included:\n{date_lines}",
              transform=ax_r.transAxes,
              fontsize=6.5, verticalalignment="bottom",
              bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.8))

    plt.tight_layout()
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)
    print("  Page 1 (overview) saved.")

    # ── Pages 2+: one page per county ─────────────────────────────────────────
    for _, row in counties.iterrows():
        name   = str(row["_display_name"])
        stats  = county_stats[name]

        bounds = row.geometry.bounds        # (minx, miny, maxx, maxy)
        cw     = bounds[2] - bounds[0]
        ch     = bounds[3] - bounds[1]
        pad_x  = cw * COUNTY_PAD_FRAC
        pad_y  = ch * COUNTY_PAD_FRAC
        xlim   = (bounds[0] - pad_x, bounds[2] + pad_x)
        ylim   = (bounds[1] - pad_y, bounds[3] + pad_y)

        # Skip counties entirely outside the merged raster extent
        if xlim[1] < x_left or xlim[0] > x_right or ylim[1] < y_bottom or ylim[0] > y_top:
            continue

        burned_str   = f"{stats['burned']:,.0f} ac burned"
        burnable_str = f"{stats['burnable']:,.0f} ac burnable"
        pct_str      = f"{stats['pct']:.1f}% burned"

        fig2, (ax2_l, ax2_r) = plt.subplots(
            1, 2, figsize=(20, 12), dpi=150,
            gridspec_kw={"wspace": 0.04},
        )
        fig2.patch.set_facecolor("white")

        # Left – full-region overview with this county highlighted (context)
        ax2_l.imshow(display_img, extent=extent, origin="upper",
                     interpolation="nearest", aspect="equal")
        counties.boundary.plot(ax=ax2_l, color="black", linewidth=0.6, zorder=3)
        gpd.GeoSeries([row.geometry]).boundary.plot(
            ax=ax2_l, color="#e67e00", linewidth=2.5, zorder=4)
        ax2_l.set_title(
            f"{name} County  –  Regional Context\n"
            f"Orange outline = {name} Co.  |  Burns in red",
            fontsize=11)
        ax2_l.set_xlabel("Easting (m, EPSG:3857)")
        ax2_l.set_ylabel("Northing (m, EPSG:3857)")
        ax2_l.tick_params(labelsize=7)

        # Right – zoomed to this county
        ax2_r.imshow(display_img, extent=extent, origin="upper",
                     interpolation="nearest", aspect="equal")
        counties.boundary.plot(ax=ax2_r, color="black", linewidth=0.8, zorder=3)
        gpd.GeoSeries([row.geometry]).boundary.plot(
            ax=ax2_r, color="#e67e00", linewidth=2.5, zorder=4)
        ax2_r.set_xlim(xlim)
        ax2_r.set_ylim(ylim)
        ax2_r.set_title(
            f"{name} County  –  {burned_str}  |  {burnable_str}  |  {pct_str}\n"
            f"Cumulative burn union  |  {n_dates} date(s)  |  NLCD 71/81",
            fontsize=11)
        ax2_r.set_xlabel("Easting (m, EPSG:3857)")
        ax2_r.set_ylabel("")
        ax2_r.tick_params(labelsize=7)
        ax2_r.legend(handles=[_burn_patch, _land_patch],
                     loc="lower right", fontsize=7, framealpha=0.85)

        plt.tight_layout()
        pdf.savefig(fig2, bbox_inches="tight")
        plt.close(fig2)
        print(f"  Page – {name} County  ({burned_str}, {pct_str})")

    # ── Last page: summary table ───────────────────────────────────────────────
    table_rows = sorted(
        [(name, v["burned"], v["burnable"], v["pct"])
         for name, v in county_stats.items()
         if v["burnable"] > 0],
        key=lambda r: r[1],
        reverse=True,
    )
    totals_burned   = sum(r[1] for r in table_rows)
    totals_burnable = sum(r[2] for r in table_rows)
    totals_pct      = (totals_burned / totals_burnable * 100
                       if totals_burnable > 0 else 0.0)

    col_labels = ["County", "Acres Burned", "Burnable Acres\n(non-masked)", "% Burned"]
    cell_text  = [
        [name, f"{burned:,.0f}", f"{burnable:,.0f}", f"{pct:.1f}%"]
        for name, burned, burnable, pct in table_rows
    ]
    cell_text.append([
        "TOTAL",
        f"{totals_burned:,.0f}",
        f"{totals_burnable:,.0f}",
        f"{totals_pct:.1f}%",
    ])

    TABLE_FONT   = 12
    HEADER_ROW_H = 0.08    # taller header only – data rows keep matplotlib default

    n_rows = len(cell_text)
    fig_h  = max(6, 0.45 * n_rows + 2.5)

    fig3, ax3 = plt.subplots(figsize=(15, fig_h), dpi=150)
    fig3.patch.set_facecolor("white")
    ax3.axis("off")

    ax3.set_title(
        f"Season Burn Summary by County  –  {year_str}\n"
        f"{n_dates} date(s): {date_label}  |  "
        f"Burnable = NLCD 71 (Grassland) or 81 (Pasture/Hay)  |  "
        f"Same-pixel burns counted once",
        fontsize=13, pad=16,
    )

    tbl = ax3.table(
        cellText=cell_text,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(TABLE_FONT)
    tbl.auto_set_column_width([0, 1, 2, 3])

    # Only the header row gets an explicit (taller) height so the two-line
    # "Burnable Acres\n(non-masked)" label is never clipped.
    # Data rows are left at matplotlib's default compact height.
    for col in range(len(col_labels)):
        tbl[0, col].set_height(HEADER_ROW_H)

    # Style header
    for col in range(len(col_labels)):
        cell = tbl[0, col]
        cell.set_facecolor("#2c5f2e")
        cell.set_text_props(color="white", fontweight="bold")

    # Style totals row (last)
    totals_row_idx = n_rows   # 1-indexed; header = 0
    for col in range(len(col_labels)):
        cell = tbl[totals_row_idx, col]
        cell.set_facecolor("#d4edda")
        cell.set_text_props(fontweight="bold")

    # Alternating / burn highlight for data rows
    for row_idx in range(1, totals_row_idx):
        name, burned, burnable, pct = table_rows[row_idx - 1]
        for col in range(len(col_labels)):
            cell = tbl[row_idx, col]
            if burned > 0:
                cell.set_facecolor("#fff3cd")    # warm yellow for burned counties
            elif row_idx % 2 == 0:
                cell.set_facecolor("#f8f8f8")

    plt.tight_layout()
    pdf.savefig(fig3, bbox_inches="tight")
    plt.close(fig3)
    print("  Last page (summary table) saved.")

    # PDF metadata
    d = pdf.infodict()
    d["Title"]   = f"Flint Hills Burn Season Summary {year_str} – {date_label}"
    d["Subject"] = f"Cumulative burn union from {n_dates} Sentinel-2 HLS S30 date(s)"

print(f"\nSaved -> {OUT_PDF}")
