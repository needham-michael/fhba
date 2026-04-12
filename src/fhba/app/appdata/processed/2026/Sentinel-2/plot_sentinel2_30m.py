"""
Sentinel-2 (HLS S30) burn-scar analysis – native 30 m pixels.

The Sentinel-2 HLS S30 bands (B2, B3, B4, B8A, B11, B12) are already at
30 m resolution in EPSG:3857, so no upsampling step is needed.  Every pixel
that does not pass ALL masks is set fully transparent in the output PNG:

  1. NLCD class mask  – keep only classes 71 (Grassland) and 81 (Pasture/Hay)
  2. HLS Fmask bits   – cloud, cloud shadow, cirrus, snow/ice
                        (loaded from the companion cloud-mask NetCDF file)
  3. Spectral guard   – supplementary brightness / NIR-ratio test for thin /
                        missed cloud missed by Fmask

NBR = (B8A – B12) / (B8A + B12) is used for burn classification.
Fresh char: dark visible (< BURN_VIS_THRESH %) AND low NBR (< BURN_NBR_THRESH).

Output
------
<nc_stem>_burn.png – two-panel RGBA figure saved beside the input .nc file
"""

import os
import math
import argparse
import glob
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
    description="Sentinel-2 HLS S30 burn analysis – 30 m native resolution")
_parser.add_argument(
    "nc_file",
    help="Path to the processed NetCDF file, "
         "e.g. Sentinel-2_flinthills_2026-04-08.nc")
_args = _parser.parse_args()

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE    = os.path.dirname(os.path.abspath(__file__))
APPDATA = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
# appdata/processed/2026/Sentinel-2  →  ../../..  →  appdata/

NC_FILE   = os.path.abspath(_args.nc_file)
_nc_stem  = os.path.splitext(os.path.basename(NC_FILE))[0]  # e.g. Sentinel-2_flinthills_2026-04-08
_date_str = _nc_stem[-10:]                                    # YYYY-MM-DD
_nc_dir   = os.path.dirname(NC_FILE)

# Cloud mask companion file: <satellite>_<spatial>_cloud_mask_<date>.nc
# Support both Sentinel-2 (new) and Sentinel-2A (legacy) naming.
_sat_prefix = _nc_stem.rsplit("_flinthills_", 1)[0]          # 'Sentinel-2' or 'Sentinel-2A'
CLOUD_MASK_NC = os.path.join(
    _nc_dir, f"{_sat_prefix}_flinthills_cloud_mask_{_date_str}.nc")
# Fall back: glob for any cloud_mask file matching the date if primary not found
if not os.path.exists(CLOUD_MASK_NC):
    _cm_glob = glob.glob(os.path.join(_nc_dir, f"*cloud_mask_{_date_str}.nc"))
    if _cm_glob:
        CLOUD_MASK_NC = _cm_glob[0]

NLCD_TIF   = os.path.join(APPDATA, "annual_nlcd",
                           "Annual_NLCD_LndCov_2024_CU_C1V1.tif")
COUNTY_SHP = os.path.join(APPDATA, "_static",
                           "FH_Counties_Updated.shp", "FH_Counties_Updated.shp")
OUT_PNG    = os.path.join(_nc_dir, f"{_nc_stem}_burn.png")

# ── Settings ──────────────────────────────────────────────────────────────────
KEEP_CLASSES = {71, 81}   # Grassland/Herbaceous, Pasture/Hay
TARGET_RES_M = 30         # pixels are already at native 30 m

# Data CRS: processed NC files are stored in EPSG:3857 (Web Mercator), matching
# the satpy area definition used by the app.  The HORIZONTAL_CS_CODE attribute
# on the individual bands reflects the original HLS tile CRS (EPSG:32615), not
# the reprojected output grid.
DST_CRS = CRS.from_epsg(3857)

# ── HLS Fmask bit flags ───────────────────────────────────────────────────────
FMASK_CIRRUS       = 0x01   # bit 0
FMASK_CLOUD        = 0x02   # bit 1
FMASK_ADJ_CLOUD    = 0x04   # bit 2  (adjacent to cloud)
FMASK_CLOUD_SHADOW = 0x08   # bit 3
FMASK_SNOW_ICE     = 0x10   # bit 4
FMASK_WATER        = 0x20   # bit 5

# ── Spectral cloud-detection thresholds (supplemental to Fmask) ───────────────
# Sentinel-2 percent reflectance (0–100).
#   Dry Kansas grassland median vis_mean ≈ 6–9 %
#   Clouds typically > 18–20 %
VIS_BRIGHT_THRESH = 18.0   # % mean visible (B4+B3+B2)/3 – above → likely cloud
NIR_RED_THRESH    = 1.5    # B8A/B4 – vegetation >> 1.5; cloud ≈ 0.8–1.2
WHITENESS_THRESH  = 0.35   # spectral flatness – clouds are spectrally neutral
SWIR_RED_THRESH   = 1.0    # B12/B4 – water clouds absorb at 2.2 µm (< 1.0)
CLOUD_BUFFER_M    = 300    # EDT dilation buffer around cloud pixels

# ── Burn classification thresholds ───────────────────────────────────────────
# Sentinel-2 percent reflectance.
#   Fresh char:           vis_mean < 7 %, NBR < 0.10
#   Unburned dry grass:   vis_mean ≈ 7–12 %, NBR ≈ 0.20–0.50
BURN_VIS_THRESH   = 6.0    # % vis_mean – burned char is very dark
BURN_NBR_THRESH   = 0.10   # NBR threshold – fresh burns have depressed near-IR
WATER_NDWI_THRESH = 0.0    # NDWI > 0 → water; excluded from burns
# PIXEL_ACRES computed after NC load (needs Mercator cos²(lat) correction)

SHADOW_BUFFER_M = 300    # EDT dilation buffer around Fmask cloud-shadow pixels


# ── Helper ────────────────────────────────────────────────────────────────────
def pct_stretch(arr, lo=2, hi=98):
    """Percentile contrast stretch → [0, 1]."""
    vmin = np.nanpercentile(arr, lo)
    vmax = np.nanpercentile(arr, hi)
    return np.clip((arr - vmin) / max(vmax - vmin, 1e-6), 0.0, 1.0)


def dilate_edt(mask_bad, buffer_m):
    """Expand a boolean 'bad pixel' mask by *buffer_m* metres using EDT."""
    if buffer_m <= 0:
        return mask_bad
    dist_to_bad = distance_transform_edt(~mask_bad) * TARGET_RES_M
    return dist_to_bad <= buffer_m


# ═══════════════════════════════════════════════════════════════════════════════
# 1  Load Sentinel-2 bands at native 30 m
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 1 – Loading Sentinel-2 bands ...")
ds = xr.open_dataset(NC_FILE)

B2  = ds['B2'].values.astype(np.float32)    # Blue   490 nm
B3  = ds['B3'].values.astype(np.float32)    # Green  560 nm
B4  = ds['B4'].values.astype(np.float32)    # Red    665 nm
B8A = ds['B8A'].values.astype(np.float32)   # NIR    865 nm
B11 = ds['B11'].values.astype(np.float32)   # SWIR1  1610 nm
B12 = ds['B12'].values.astype(np.float32)   # SWIR2  2190 nm

x_src = ds.x.values
y_src = ds.y.values
ny_src, nx_src = B4.shape

x_left   = float(x_src.min())
x_right  = float(x_src.max())
y_bottom = float(y_src.min())
y_top    = float(y_src.max())

src_px = abs(float(x_src[1] - x_src[0]))   # should be ~30 m
src_py = abs(float(y_src[0] - y_src[1]))   # should be ~30 m

# Target grid == source grid (already at 30 m)
nx_tgt, ny_tgt = nx_src, ny_src
dst_transform = rasterio.transform.from_bounds(
    x_left, y_bottom, x_right, y_top, nx_tgt, ny_tgt)

print(f"  Grid: {nx_tgt} x {ny_tgt} px @ {src_px:.0f} m  "
      f"({nx_tgt * ny_tgt / 1e6:.1f} M pixels)")

# ── PIXEL_ACRES: correct for EPSG:3857 Mercator area distortion ───────────────
# NC is in EPSG:3857; pixel spacing is ~30 m in projected units, but Web Mercator
# stretches distances by 1/cos(lat).  Each pixel covers only
#   src_px × src_py × cos²(lat)  true m² of ground (~562 m² at 38°N, not 900 m²).
_R_earth = 6_378_137.0
_y_center_3857 = (y_bottom + y_top) / 2.0
_lat_center_rad = 2.0 * math.atan(math.exp(_y_center_3857 / _R_earth)) - math.pi / 2.0
_cos_lat = math.cos(_lat_center_rad)
_pixel_ground_m2 = src_px * src_py * (_cos_lat ** 2)
PIXEL_ACRES = _pixel_ground_m2 / 4046.856
print(f"  Centre lat {math.degrees(_lat_center_rad):.2f}°, "
      f"Mercator scale={_cos_lat:.4f}, "
      f"pixel ground area={_pixel_ground_m2:.1f} m²  →  {PIXEL_ACRES:.4f} ac/px")

# Display (stretched) RGB from B4/B3/B2
r_disp = pct_stretch(B4)
g_disp = pct_stretch(B3)
b_disp = pct_stretch(B2)

nodata = np.isnan(B4) | np.isnan(B8A) | np.isnan(B12)

# Pre-compute spectral indices (percent reflectance)
vis_mean  = (B4 + B3 + B2) / 3.0
nir_red   = B8A  / (B4  + 1e-6)
swir_red  = B12  / (B4  + 1e-6)
whiteness = (np.abs(B4 - vis_mean) + np.abs(B3 - vis_mean) + np.abs(B2 - vis_mean)) \
            / (vis_mean + 1e-6)


# ═══════════════════════════════════════════════════════════════════════════════
# 2  NLCD class mask (windowed read → reproject to 30 m target grid)
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
# 3  HLS Fmask cloud + shadow masking
#
#    The HLS S30 product includes a per-pixel quality flag (Fmask) that encodes
#    cloud, cloud shadow, cirrus, snow/ice, and water as individual bits.  This
#    is more reliable than spectral-only tests for Sentinel-2 because it is
#    computed at the native 10–30 m resolution during HLS processing.
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 3 – Applying HLS Fmask cloud / shadow mask ...")

contaminated = np.zeros((ny_tgt, nx_tgt), dtype=bool)

if os.path.exists(CLOUD_MASK_NC):
    print(f"  Cloud mask file: {os.path.basename(CLOUD_MASK_NC)}")
    cm_ds  = xr.open_dataset(CLOUD_MASK_NC)
    fmask  = cm_ds['Fmask'].values.astype(np.uint8)

    cloud_px  = (fmask & FMASK_CLOUD)        > 0
    cirrus_px = (fmask & FMASK_CIRRUS)       > 0
    shadow_px = (fmask & FMASK_CLOUD_SHADOW) > 0
    snow_px   = (fmask & FMASK_SNOW_ICE)     > 0

    print(f"  Fmask cloud:  {cloud_px.mean()*100:.2f}%")
    print(f"  Fmask shadow: {shadow_px.mean()*100:.2f}%")
    print(f"  Fmask cirrus: {cirrus_px.mean()*100:.2f}%")

    cloud_bad  = dilate_edt(cloud_px | cirrus_px, CLOUD_BUFFER_M)
    shadow_bad = dilate_edt(shadow_px,            SHADOW_BUFFER_M)
    contaminated = cloud_bad | shadow_bad | snow_px
    print(f"  After EDT buffer: {contaminated.mean()*100:.2f}% masked")
else:
    print(f"  WARNING: Cloud mask file not found ({os.path.basename(CLOUD_MASK_NC)})")
    print("  Proceeding with spectral-only cloud detection.")


# ═══════════════════════════════════════════════════════════════════════════════
# 4  Supplemental spectral cloud detection
#
#    Thin cloud and cloud edges that Fmask may have underclassified are caught
#    by requiring all three spectral tests to fire simultaneously:
#      Test 1 – Bright in visible (vis_mean > VIS_BRIGHT_THRESH)
#      Test 2 – Not vegetation (NIR/Red < NIR_RED_THRESH)
#      Test 3 – Water-cloud SWIR absorption OR spectrally neutral (whiteness)
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 4 – Supplemental spectral cloud check ...")

cloud_spectral = (
    ~nodata                             &
    (vis_mean  >  VIS_BRIGHT_THRESH)   &
    (nir_red   <  NIR_RED_THRESH)      &
    ((swir_red <  SWIR_RED_THRESH)     |
     (whiteness < WHITENESS_THRESH))
)

pct_spec = cloud_spectral.mean() * 100
print(f"  Spectral cloud pixels detected: {pct_spec:.2f}%")
spec_bad = dilate_edt(cloud_spectral, CLOUD_BUFFER_M)
contaminated = contaminated | spec_bad
print(f"  Total masked after spectral supplement: {contaminated.mean()*100:.2f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# 5  Burn classification + county acreage
#
#    NBR = (B8A – B12) / (B8A + B12).
#    Burned pixel criteria (both must be satisfied):
#      vis_mean < BURN_VIS_THRESH  → fresh char is very dark in visible
#      NBR      < BURN_NBR_THRESH  → depressed NIR reflectance from charred surface
#    Only pixels already passing NLCD and cloud/shadow masks are considered.
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 5 – Burn classification + county acreage ...")

nbr = (B8A - B12) / (B8A + B12 + 1e-6)

burn = (
    ~nodata                        &
    (vis_mean  <  BURN_VIS_THRESH) &
    (nbr       <  BURN_NBR_THRESH)
)
print(f"  Burn pixels (scene-wide, no masking): {burn.mean()*100:.2f}%")

# ── NDWI water mask (excludes lakes / rivers misclassified as burns) ──────────
with np.errstate(invalid='ignore'):
    ndwi  = (B3 - B8A) / (B3 + B8A + 1e-6)
    water = ~nodata & (ndwi > WATER_NDWI_THRESH)
print(f"  Water pixels (NDWI > {WATER_NDWI_THRESH}): {water.mean()*100:.2f}%")

burn_valid = burn & land_ok & ~contaminated & ~water
print(f"  Burn pixels (valid land only):        {burn_valid.mean()*100:.2f}%  "
      f"({burn_valid.sum() * PIXEL_ACRES:,.0f} total acres)")

# ── County-level acreage ──────────────────────────────────────────────────────
counties = gpd.read_file(COUNTY_SHP).to_crs(epsg=3857)
counties = counties.reset_index(drop=True)
counties["_cid"] = counties.index + 1

_name_candidates = ["NAME", "Name", "name", "COUNTY", "County", "county",
                    "COUNTYNAME", "CountyName"]
_name_field = next((c for c in _name_candidates if c in counties.columns),
                   counties.columns[0])

county_raster = rasterize(
    [(geom, cid) for geom, cid in zip(counties.geometry, counties["_cid"])],
    out_shape=(ny_tgt, nx_tgt),
    transform=dst_transform,
    fill=0, dtype=np.int32,
)

print(f"\n  {'County':<22}  {'Burn acres':>12}")
print(f"  {'-'*22}  {'-'*12}")
county_acres = {}
for _, row in counties.iterrows():
    cid   = row["_cid"]
    name  = str(row[_name_field])
    acres = float((burn_valid & (county_raster == cid)).sum() * PIXEL_ACRES)
    county_acres[name] = acres
    if acres > 0:
        print(f"  {name:<22}  {acres:>12,.1f}")

total_acres = sum(county_acres.values())
print(f"  {'TOTAL':<22}  {total_acres:>12,.1f}")
print()


# ═══════════════════════════════════════════════════════════════════════════════
# 6  Build RGBA – valid pixels opaque, burns highlighted orange, rest transparent
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 6 – Building RGBA image ...")

valid = land_ok & ~contaminated & ~nodata & ~water
print(f"  Valid (visible) pixels: {valid.mean()*100:.2f}%")

rgba = np.zeros((ny_tgt, nx_tgt, 4), dtype=np.uint8)
rgba[..., 0] = np.clip(r_disp * 255, 0, 255).astype(np.uint8)
rgba[..., 1] = np.clip(g_disp * 255, 0, 255).astype(np.uint8)
rgba[..., 2] = np.clip(b_disp * 255, 0, 255).astype(np.uint8)
rgba[..., 3] = (valid * 255).astype(np.uint8)

# Highlight burned pixels in orange over the true-colour base
rgba[burn_valid, 0] = 230
rgba[burn_valid, 1] = 90
rgba[burn_valid, 2] = 0
rgba[burn_valid, 3] = 255

# Water pixels shown in blue (painted after burns so they never overlap)
rgba[water, 0] = 30
rgba[water, 1] = 130
rgba[water, 2] = 220
rgba[water, 3] = 255


# ═══════════════════════════════════════════════════════════════════════════════
# 7  Compose two-panel figure
#    Left  – true colour at 30 m (no masking)
#    Right – masked output: NLCD 71/81 | cloud & shadow removed | burns orange
# ═══════════════════════════════════════════════════════════════════════════════
print("Step 7 – Composing two-panel figure ...")

counties = gpd.read_file(COUNTY_SHP).to_crs(epsg=3857)

dx = (x_right - x_left)  / (nx_tgt - 1)
dy = (y_top   - y_bottom) / (ny_tgt - 1)
extent = (x_left  - dx / 2, x_right + dx / 2,
          y_bottom - dy / 2, y_top   + dy / 2)

# True-colour RGB (no masks, NaN pixels filled black)
tc_rgb = np.stack([
    np.clip(r_disp * 255, 0, 255).astype(np.uint8),
    np.clip(g_disp * 255, 0, 255).astype(np.uint8),
    np.clip(b_disp * 255, 0, 255).astype(np.uint8),
], axis=-1)
tc_rgb[nodata] = 0

fig, (ax_l, ax_r) = plt.subplots(
    1, 2,
    figsize=(20, 18),
    dpi=150,
    gridspec_kw={"wspace": 0.04},
)
fig.patch.set_facecolor("white")

# ── Left: true colour ─────────────────────────────────────────────────────────
ax_l.imshow(tc_rgb, extent=extent, origin="upper",
            interpolation="bilinear", aspect="equal")
counties.boundary.plot(ax=ax_l, color="black", linewidth=0.6, zorder=3)
ax_l.set_title(f"True colour  –  30 m native\nSentinel-2 (HLS S30) {_date_str}",
               fontsize=10)
ax_l.set_xlabel("Easting (m, EPSG:3857)")
ax_l.set_ylabel("Northing (m, EPSG:3857)")
ax_l.tick_params(labelsize=7)

# ── Right: masked + burns ─────────────────────────────────────────────────────
# Composite RGBA over a white background so masked areas show as white
rgba_f = rgba.astype(np.float32) / 255.0
alpha  = rgba_f[..., 3:4]
bg     = np.ones((ny_tgt, nx_tgt, 3), dtype=np.float32)
composited = rgba_f[..., :3] * alpha + bg * (1.0 - alpha)
composited = np.clip(composited, 0, 1)

ax_r.imshow(composited, extent=extent, origin="upper",
            interpolation="bilinear", aspect="equal")
counties.boundary.plot(ax=ax_r, color="black", linewidth=0.6, zorder=3)
ax_r.set_title(
    f"NLCD 71 (Grassland) & 81 (Pasture/Hay)  |  Cloud & shadow removed  |  Water (NDWI) excluded\n"
    f"Burns (orange) = {total_acres:,.0f} acres  |  Water (blue)  |  Sentinel-2 (HLS S30) {_date_str}",
    fontsize=10,
)
ax_r.set_xlabel("Easting (m, EPSG:3857)")
ax_r.set_ylabel("")
ax_r.tick_params(labelsize=7)

plt.tight_layout()
plt.savefig(OUT_PNG, dpi=150, bbox_inches="tight", transparent=False)
print(f"Saved -> {OUT_PNG}")
plt.close(fig)
