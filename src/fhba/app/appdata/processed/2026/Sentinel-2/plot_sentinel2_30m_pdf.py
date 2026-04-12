"""
Sentinel-2 (HLS S30) burn-scar analysis – native 30 m pixels – PDF report.

Produces a multi-page PDF beside the input .nc file:
  Page 1   – Overview two-panel figure (true colour | masked burns), same as
             the PNG produced by plot_sentinel2_30m.py
  Pages 2+ – One page per county (zoomed two-panel: true colour | burns)
  Last     – Summary table: acres burned, burnable acres, % of county burned
"""

import os
import math
import argparse
import glob
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_pdf import PdfPages
import xarray as xr
import geopandas as gpd
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds, reproject as rio_reproject
from rasterio.enums import Resampling
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt

# ── Command-line argument ──────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="Sentinel-2 HLS S30 burn analysis – 30 m native resolution – PDF")
_parser.add_argument(
    "nc_file",
    help="Path to the processed NetCDF file, "
         "e.g. Sentinel-2_flinthills_2026-04-08.nc")
_args = _parser.parse_args()

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE    = os.path.dirname(os.path.abspath(__file__))
APPDATA = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

NC_FILE   = os.path.abspath(_args.nc_file)
_nc_stem  = os.path.splitext(os.path.basename(NC_FILE))[0]
_date_str = _nc_stem[-10:]
_nc_dir   = os.path.dirname(NC_FILE)

_sat_prefix = _nc_stem.rsplit("_flinthills_", 1)[0]
CLOUD_MASK_NC = os.path.join(
    _nc_dir, f"{_sat_prefix}_flinthills_cloud_mask_{_date_str}.nc")
if not os.path.exists(CLOUD_MASK_NC):
    _cm_glob = glob.glob(os.path.join(_nc_dir, f"*cloud_mask_{_date_str}.nc"))
    if _cm_glob:
        CLOUD_MASK_NC = _cm_glob[0]

NLCD_TIF   = os.path.join(APPDATA, "annual_nlcd",
                           "Annual_NLCD_LndCov_2024_CU_C1V1.tif")
COUNTY_SHP = os.path.join(APPDATA, "_static",
                           "FH_Counties_Updated.shp", "FH_Counties_Updated.shp")
OUT_PDF    = os.path.join(_nc_dir, f"{_nc_stem}_burn_report.pdf")

# ── Settings ──────────────────────────────────────────────────────────────────
KEEP_CLASSES = {71, 81}
TARGET_RES_M = 30
DST_CRS      = CRS.from_epsg(3857)

FMASK_CIRRUS       = 0x01
FMASK_CLOUD        = 0x02
FMASK_ADJ_CLOUD    = 0x04
FMASK_CLOUD_SHADOW = 0x08
FMASK_SNOW_ICE     = 0x10
FMASK_WATER        = 0x20

VIS_BRIGHT_THRESH = 18.0
NIR_RED_THRESH    = 1.5
WHITENESS_THRESH  = 0.35
SWIR_RED_THRESH   = 1.0
CLOUD_BUFFER_M    = 300
SHADOW_BUFFER_M   = 300

BURN_VIS_THRESH    = 6.0
BURN_NBR_THRESH    = -0.1
WATER_NDWI_THRESH  = 0.0    # NDWI > 0 → water; excluded from burns
# PIXEL_ACRES is computed after loading the NC (needs actual pixel size + Mercator correction)

COUNTY_PAD_FRAC = 0.08   # padding fraction around each county zoom


# ── Helpers ───────────────────────────────────────────────────────────────────
def pct_stretch(arr, lo=2, hi=98):
    vmin = np.nanpercentile(arr, lo)
    vmax = np.nanpercentile(arr, hi)
    return np.clip((arr - vmin) / max(vmax - vmin, 1e-6), 0.0, 1.0)


def dilate_edt(mask_bad, buffer_m):
    if buffer_m <= 0:
        return mask_bad
    dist_to_bad = distance_transform_edt(~mask_bad) * TARGET_RES_M
    return dist_to_bad <= buffer_m


def composite_over_white(rgba, ny, nx):
    """Alpha-composite an RGBA uint8 array over a white background."""
    rgba_f = rgba.astype(np.float32) / 255.0
    alpha  = rgba_f[..., 3:4]
    bg     = np.ones((ny, nx, 3), dtype=np.float32)
    out    = rgba_f[..., :3] * alpha + bg * (1.0 - alpha)
    return np.clip(out, 0, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# 1  Load Sentinel-2 bands
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 1 – Loading Sentinel-2 bands ...")
ds = xr.open_dataset(NC_FILE)

B2  = ds['B2'].values.astype(np.float32)
B3  = ds['B3'].values.astype(np.float32)
B4  = ds['B4'].values.astype(np.float32)
B8A = ds['B8A'].values.astype(np.float32)
B11 = ds['B11'].values.astype(np.float32)
B12 = ds['B12'].values.astype(np.float32)

x_src = ds.x.values
y_src = ds.y.values
ny_src, nx_src = B4.shape

x_left   = float(x_src.min())
x_right  = float(x_src.max())
y_bottom = float(y_src.min())
y_top    = float(y_src.max())

src_px = abs(float(x_src[1] - x_src[0]))
src_py = abs(float(y_src[0] - y_src[1]))

nx_tgt, ny_tgt = nx_src, ny_src
dst_transform = rasterio.transform.from_bounds(
    x_left, y_bottom, x_right, y_top, nx_tgt, ny_tgt)

print(f"  Grid: {nx_tgt} x {ny_tgt} px @ {src_px:.0f} m")

# ── PIXEL_ACRES: correct for EPSG:3857 Mercator area distortion ───────────────
# The NC is stored in EPSG:3857 where pixel spacing is ~30 m, but Web Mercator
# stretches distances by 1/cos(lat), so each pixel covers only
#   src_px × src_py × cos²(lat)  true m² of ground.
# At 38°N this is ~562 m² not 900 m², so hardcoding 30² gives a 1.6× overcount.
_R_earth = 6_378_137.0   # EPSG:3857 Earth radius (m)
_y_center_3857 = (y_bottom + y_top) / 2.0
_lat_center_rad = 2.0 * math.atan(math.exp(_y_center_3857 / _R_earth)) - math.pi / 2.0
_cos_lat = math.cos(_lat_center_rad)
_pixel_ground_m2 = src_px * src_py * (_cos_lat ** 2)
PIXEL_ACRES = _pixel_ground_m2 / 4046.856
print(f"  Centre lat {math.degrees(_lat_center_rad):.2f}°, "
      f"Mercator scale={_cos_lat:.4f}, "
      f"pixel ground area={_pixel_ground_m2:.1f} m²  →  {PIXEL_ACRES:.4f} ac/px")

r_disp = pct_stretch(B4)
g_disp = pct_stretch(B3)
b_disp = pct_stretch(B2)

nodata = np.isnan(B4) | np.isnan(B8A) | np.isnan(B12)

vis_mean  = (B4 + B3 + B2) / 3.0
nir_red   = B8A  / (B4  + 1e-6)
swir_red  = B12  / (B4  + 1e-6)
whiteness = (np.abs(B4 - vis_mean) + np.abs(B3 - vis_mean) + np.abs(B2 - vis_mean)) \
            / (vis_mean + 1e-6)


# ═══════════════════════════════════════════════════════════════════════════════
# 2  NLCD class mask
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 2 – Building NLCD class mask ...")

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
print(f"  Land mask keeps {land_ok.mean() * 100:.1f}% of pixels")


# ═══════════════════════════════════════════════════════════════════════════════
# 3  HLS Fmask cloud / shadow masking
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 3 – Applying HLS Fmask ...")

contaminated = np.zeros((ny_tgt, nx_tgt), dtype=bool)

if os.path.exists(CLOUD_MASK_NC):
    print(f"  Cloud mask file: {os.path.basename(CLOUD_MASK_NC)}")
    cm_ds  = xr.open_dataset(CLOUD_MASK_NC)
    fmask  = cm_ds['Fmask'].values.astype(np.uint8)

    cloud_px  = (fmask & FMASK_CLOUD)        > 0
    cirrus_px = (fmask & FMASK_CIRRUS)       > 0
    shadow_px = (fmask & FMASK_CLOUD_SHADOW) > 0
    snow_px   = (fmask & FMASK_SNOW_ICE)     > 0

    cloud_bad  = dilate_edt(cloud_px | cirrus_px, CLOUD_BUFFER_M)
    shadow_bad = dilate_edt(shadow_px,            SHADOW_BUFFER_M)
    contaminated = cloud_bad | shadow_bad | snow_px
    print(f"  After EDT buffer: {contaminated.mean()*100:.2f}% masked")
else:
    print(f"  WARNING: Cloud mask file not found – spectral-only detection.")


# ═══════════════════════════════════════════════════════════════════════════════
# 4  Supplemental spectral cloud detection
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 4 – Supplemental spectral cloud check ...")

cloud_spectral = (
    ~nodata                             &
    (vis_mean  >  VIS_BRIGHT_THRESH)   &
    (nir_red   <  NIR_RED_THRESH)      &
    ((swir_red <  SWIR_RED_THRESH)     |
     (whiteness < WHITENESS_THRESH))
)
spec_bad = dilate_edt(cloud_spectral, CLOUD_BUFFER_M)
contaminated = contaminated | spec_bad
print(f"  Total masked: {contaminated.mean()*100:.2f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# 5  Burn classification + county acreage
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 5 – Burn classification + county acreage ...")

nbr = (B8A - B12) / (B8A + B12 + 1e-6)
BURN_SWIR2_MIN = 3.0     # % — rules out deep shadow (B12 too suppressed)

burn = (
    ~nodata                          &
    (vis_mean  <  BURN_VIS_THRESH)   &
    (nbr       <  BURN_NBR_THRESH)   &
    (B12       >  BURN_SWIR2_MIN)
)

# ── NDWI water mask (excludes lakes / rivers misclassified as burns) ──────────
with np.errstate(invalid='ignore'):
    ndwi  = (B3 - B8A) / (B3 + B8A + 1e-6)
    water = ~nodata & (ndwi > WATER_NDWI_THRESH)
print(f"  Water pixels (NDWI > {WATER_NDWI_THRESH}): {water.mean()*100:.2f}%")

burn_valid = burn & land_ok & ~contaminated & ~water
valid      = land_ok & ~contaminated & ~nodata & ~water

print(f"  Burn pixels (valid land only): {burn_valid.sum() * PIXEL_ACRES:,.0f} total acres")

counties = gpd.read_file(COUNTY_SHP).to_crs(epsg=3857)
counties = counties.reset_index(drop=True)
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

# Per-county stats: burned acres, burnable acres, % burned
county_stats = {}   # name -> {burned, burnable}
for _, row in counties.iterrows():
    cid  = row["_cid"]
    name = str(row["_display_name"])
    in_county    = county_raster == cid
    burned_acres   = float((burn_valid & in_county).sum() * PIXEL_ACRES)
    burnable_acres = float((valid      & in_county).sum() * PIXEL_ACRES)
    county_stats[name] = {
        "burned":   burned_acres,
        "burnable": burnable_acres,
        "pct":      (burned_acres / burnable_acres * 100) if burnable_acres > 0 else 0.0,
    }

# total_acres sums only county-intersected pixels – consistent with the display
total_acres = sum(v["burned"] for v in county_stats.values())
print(f"  Total burned (FH counties only): {total_acres:,.0f} acres")


# ═══════════════════════════════════════════════════════════════════════════════
# 6  Build display arrays
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 6 – Building display arrays ...")

# True-colour RGB
tc_rgb = np.stack([
    np.clip(r_disp * 255, 0, 255).astype(np.uint8),
    np.clip(g_disp * 255, 0, 255).astype(np.uint8),
    np.clip(b_disp * 255, 0, 255).astype(np.uint8),
], axis=-1)
tc_rgb[nodata] = 0

# RGBA masked+burns
rgba = np.zeros((ny_tgt, nx_tgt, 4), dtype=np.uint8)
rgba[..., 0] = np.clip(r_disp * 255, 0, 255).astype(np.uint8)
rgba[..., 1] = np.clip(g_disp * 255, 0, 255).astype(np.uint8)
rgba[..., 2] = np.clip(b_disp * 255, 0, 255).astype(np.uint8)
rgba[..., 3] = (valid * 255).astype(np.uint8)
# Restrict displayed burns and acreage totals to pixels inside an FH county
fh_mask        = county_raster > 0
burn_valid_fh  = burn_valid & fh_mask   # only within Flint Hills county boundaries

rgba[burn_valid_fh, 0] = 220
rgba[burn_valid_fh, 1] = 30
rgba[burn_valid_fh, 2] = 30
rgba[burn_valid_fh, 3] = 255

# Water pixels shown in blue (painted after burns so water never overlaps burns)
water_fh = water & fh_mask
rgba[water_fh, 0] = 30
rgba[water_fh, 1] = 130
rgba[water_fh, 2] = 220
rgba[water_fh, 3] = 255

composited_full = composite_over_white(rgba, ny_tgt, nx_tgt)

dx = (x_right - x_left)  / (nx_tgt - 1)
dy = (y_top   - y_bottom) / (ny_tgt - 1)
extent = (x_left  - dx / 2, x_right + dx / 2,
          y_bottom - dy / 2, y_top   + dy / 2)


# ═══════════════════════════════════════════════════════════════════════════════
# 6b  Save per-date burn-mask GeoTIFF (consumed by season_summary.py)
#
#     Band 1 – uint8
#       1 = confirmed burned pixel:
#             passed spectral burn test AND NLCD 71/81 AND cloud/shadow-free
#             AND falls inside an FH county boundary
#       0 = everything else (not burned, masked, outside scene, non-grassland)
#
#     The GeoTIFF is georeferenced in EPSG:3857 at 30 m resolution so that
#     season_summary.py can union multiple dates without reprojection.
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 6b – Saving burn-mask GeoTIFF ...")
OUT_TIF = os.path.join(_nc_dir, f"{_nc_stem}_burn_mask.tif")
with rasterio.open(
    OUT_TIF, "w",
    driver="GTiff",
    height=ny_tgt,
    width=nx_tgt,
    count=1,
    dtype=np.uint8,
    crs=DST_CRS,
    transform=dst_transform,
    compress="lzw",
    nodata=None,          # 0 = not burned; 1 = burned; no nodata sentinel needed
) as _tif:
    _tif.write(burn_valid_fh.astype(np.uint8), 1)
    _tif.update_tags(
        date=_date_str,
        source=os.path.basename(NC_FILE),
        burn_classes="NLCD 71 (Grassland) and 81 (Pasture/Hay)",
        water_mask=f"NDWI > {WATER_NDWI_THRESH} excluded",
        description="Per-date burn mask for Flint Hills; "
                    "union multiple dates in season_summary.py",
    )
print(f"  Saved -> {OUT_TIF}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7  Render PDF
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 7 – Rendering PDF ...")

with PdfPages(OUT_PDF) as pdf:

    # ── Page 1: overview two-panel ─────────────────────────────────────────────
    fig, (ax_l, ax_r) = plt.subplots(
        1, 2,
        figsize=(20, 18),
        dpi=150,
        gridspec_kw={"wspace": 0.04},
    )
    fig.patch.set_facecolor("white")

    ax_l.imshow(tc_rgb, extent=extent, origin="upper",
                interpolation="bilinear", aspect="equal")
    counties.boundary.plot(ax=ax_l, color="black", linewidth=0.6, zorder=3)
    ax_l.set_title(
        f"True colour  –  30 m native\nSentinel-2 (HLS S30)  {_date_str}",
        fontsize=10)
    ax_l.set_xlabel("Easting (m, EPSG:3857)")
    ax_l.set_ylabel("Northing (m, EPSG:3857)")
    ax_l.tick_params(labelsize=7)

    ax_r.imshow(composited_full, extent=extent, origin="upper",
                interpolation="bilinear", aspect="equal")
    counties.boundary.plot(ax=ax_r, color="black", linewidth=0.6, zorder=3)
    ax_r.set_title(
        f"NLCD 71 (Grassland) & 81 (Pasture/Hay)  |  Cloud & shadow removed  |  Water (NDWI) excluded\n"
        f"Burns (red) = {total_acres:,.0f} acres total (FH counties)  |  Sentinel-2 (HLS S30) {_date_str}",
        fontsize=10,
    )
    ax_r.set_xlabel("Easting (m, EPSG:3857)")
    ax_r.set_ylabel("")
    ax_r.tick_params(labelsize=7)

    plt.tight_layout()
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)
    print("  Page 1 (overview) saved.")

    # ── Pages 2+: one page per county ─────────────────────────────────────────
    for idx, row in counties.iterrows():
        name = str(row["_display_name"])
        stats = county_stats[name]

        # County bounding box in EPSG:3857
        bounds = row.geometry.bounds   # (minx, miny, maxx, maxy)
        cw = bounds[2] - bounds[0]
        ch = bounds[3] - bounds[1]
        pad_x = cw * COUNTY_PAD_FRAC
        pad_y = ch * COUNTY_PAD_FRAC
        xlim = (bounds[0] - pad_x, bounds[2] + pad_x)
        ylim = (bounds[1] - pad_y, bounds[3] + pad_y)

        # Skip counties entirely outside the scene raster extent
        if xlim[1] < x_left or xlim[0] > x_right or ylim[1] < y_bottom or ylim[0] > y_top:
            continue

        fig2, (ax2_l, ax2_r) = plt.subplots(
            1, 2,
            figsize=(20, 12),
            dpi=150,
            gridspec_kw={"wspace": 0.04},
        )
        fig2.patch.set_facecolor("white")

        burned_str   = f"{stats['burned']:,.0f} ac burned"
        burnable_str = f"{stats['burnable']:,.0f} ac burnable"
        pct_str      = f"{stats['pct']:.1f}% burned"

        # Left: true colour zoomed
        ax2_l.imshow(tc_rgb, extent=extent, origin="upper",
                     interpolation="bilinear", aspect="equal")
        counties.boundary.plot(ax=ax2_l, color="black", linewidth=0.8, zorder=3)
        # Highlight this county
        gpd.GeoSeries([row.geometry]).boundary.plot(
            ax=ax2_l, color="red", linewidth=2.0, zorder=4)
        ax2_l.set_xlim(xlim)
        ax2_l.set_ylim(ylim)
        ax2_l.set_title(
            f"{name} County  –  True colour  |  Sentinel-2 (HLS S30) {_date_str}",
            fontsize=11)
        ax2_l.set_xlabel("Easting (m, EPSG:3857)")
        ax2_l.set_ylabel("Northing (m, EPSG:3857)")
        ax2_l.tick_params(labelsize=7)

        # Right: masked + burns zoomed
        ax2_r.imshow(composited_full, extent=extent, origin="upper",
                     interpolation="bilinear", aspect="equal")
        counties.boundary.plot(ax=ax2_r, color="black", linewidth=0.8, zorder=3)
        gpd.GeoSeries([row.geometry]).boundary.plot(
            ax=ax2_r, color="red", linewidth=2.0, zorder=4)
        ax2_r.set_xlim(xlim)
        ax2_r.set_ylim(ylim)
        ax2_r.set_title(
            f"{name} County  –  {burned_str}  |  {burnable_str}  |  {pct_str}\n"
            f"NLCD 71/81  |  Cloud & shadow removed  |  Water (NDWI) excluded  |  Burns in red, water in blue",
            fontsize=11,
        )
        ax2_r.set_xlabel("Easting (m, EPSG:3857)")
        ax2_r.set_ylabel("")
        ax2_r.tick_params(labelsize=7)

        plt.tight_layout()
        pdf.savefig(fig2, bbox_inches="tight")
        plt.close(fig2)
        print(f"  Page – {name} County saved  ({burned_str}, {pct_str})")

    # ── Last page: summary table ───────────────────────────────────────────────
    # Collect rows sorted by burned acres descending
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
        [name,
         f"{burned:,.0f}",
         f"{burnable:,.0f}",
         f"{pct:.1f}%"]
        for name, burned, burnable, pct in table_rows
    ]
    # Totals row
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
        f"Burn Summary by County  –  Sentinel-2 (HLS S30)  {_date_str}\n"
        f"Burnable = NLCD class 71 (Grassland) or 81 (Pasture/Hay), "
        f"cloud / shadow free, water (NDWI > {WATER_NDWI_THRESH}) excluded",
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

    # Style header row
    for col in range(len(col_labels)):
        cell = tbl[0, col]
        cell.set_facecolor("#2c5f2e")
        cell.set_text_props(color="white", fontweight="bold")

    # Style totals row (last)
    totals_row_idx = len(cell_text)   # 1-indexed (header = 0)
    for col in range(len(col_labels)):
        cell = tbl[totals_row_idx, col]
        cell.set_facecolor("#d4edda")
        cell.set_text_props(fontweight="bold")

    # Alternating row colours for data rows; highlight rows with burns
    for row_idx in range(1, totals_row_idx):
        name, burned, burnable, pct = table_rows[row_idx - 1]
        for col in range(len(col_labels)):
            cell = tbl[row_idx, col]
            if burned > 0:
                cell.set_facecolor("#fff3cd")   # warm yellow for any burn
            elif row_idx % 2 == 0:
                cell.set_facecolor("#f8f8f8")

    plt.tight_layout()
    pdf.savefig(fig3, bbox_inches="tight")
    plt.close(fig3)
    print("  Last page (summary table) saved.")

    # PDF metadata
    d = pdf.infodict()
    d["Title"]   = f"Sentinel-2 Burn Report – {_date_str}"
    d["Subject"] = "HLS S30 burn-scar analysis, Flint Hills"

print(f"\nSaved -> {OUT_PDF}")
