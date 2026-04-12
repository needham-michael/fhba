"""
chase_county_burn_analysis_10m.py

Sentinel-2 L2A 10 m burn analysis for Chase County, KS on 2026-04-08.

Outputs
-------
  chase_county_truecolor_10m_2026-04-08.tif   – uint8 RGB, EPSG:32615, 10 m
  chase_county_burn_mask_10m_2026-04-08.tif   – uint8 (1=burned), same grid
  Interactive Panel app served at http://localhost:5006

Run
---
  .venv\\Scripts\\panel serve chase_county_burn_analysis_10m.py --show

Algorithm (from plot_sentinel2_30m_pdf.py)
------------------------------------------
  NBR = (B8A - B12) / (B8A + B12)
  burn = vis_mean < 6.0  AND  NBR < -0.1  AND  B12 > 3.0
  cloud mask: spectral brightness test + 300 m EDT buffer
  No NLCD land-use filter applied.
"""

import os
import requests
import numpy as np
import xarray as xr
import rasterio
from rasterio.crs import CRS
from rasterio.features import rasterize as rio_rasterize
from scipy.ndimage import distance_transform_edt
import geopandas as gpd
import panel as pn
import holoviews as hv
from holoviews.operation.datashader import rasterize
from holoviews import opts
import matplotlib.colors as mcolors

hv.extension('bokeh')
pn.extension(sizing_mode='stretch_width')

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE         = os.path.dirname(os.path.abspath(__file__))
OUT_RGB_TIF  = os.path.join(HERE, 'chase_county_truecolor_10m_2026-04-08.tif')
OUT_BURN_TIF = os.path.join(HERE, 'chase_county_burn_mask_10m_2026-04-08.tif')

# Raw tile cache – stored alongside the HLS granules so the same directory
# layout is shared across all Sentinel-2 sources.
CACHE_DIR = os.path.abspath(
    os.path.join(HERE, '..', '..', '..', 'raw', '2026', 'Sentinel-2-L2A')
)

# County boundary shapefile (Flint Hills counties)
COUNTY_SHP  = os.path.abspath(os.path.join(
    HERE, '..', '..', '..', '_static',
    'FH_Counties_Updated.shp', 'FH_Counties_Updated.shp'
))
COUNTY_NAME = 'Chase County'   # must match the NAME field in the shapefile

# ── Settings ──────────────────────────────────────────────────────────────────
DATE        = '2026-04-08'
BBOX        = [-96.84, 38.09, -96.35, 38.52]   # Chase County, KS (WGS84)
DST_CRS     = 'EPSG:32615'                      # UTM zone 15N
DST_RES     = 10                                # native S2 resolution (m)
CLOUD_MAX   = 80                                # max cloud % for STAC search
PC_STAC_URL = 'https://planetarycomputer.microsoft.com/api/stac/v1'

# Sentinel-2 L2A DN → % reflectance
# ESA processing baseline >= 04.00 (this scene is 05.12) adds a +1000 DN
# quantification offset to every band.  The correct formula is:
#   reflectance (%) = (DN - OFFSET) / 10 000 × 100
# Without this correction every pixel reads ~10 % too bright, which pushes
# burned areas (true ~2–5 %) above the vis_mean < 6 % burn threshold.
OFFSET = 1000          # BOA quantification offset (DN units, baseline >= 04.00)
SCALE  = 1.0 / 10_000.0 * 100.0   # DN → percent reflectance multiplier

# Burn detection thresholds (from plot_sentinel2_30m_pdf.py)
BURN_VIS_THRESH  = 6.0
BURN_NBR_THRESH  = -0.1
BURN_SWIR2_MIN   = 3.0

# ── Feature switches ─────────────────────────────────────────────────────────
CLOUD_MASK_ENABLED = False   # set True to re-enable spectral cloud masking
WATER_MASK_ENABLED = True    # exclude NDWI water pixels from burns

# Spectral cloud detection thresholds (only used when CLOUD_MASK_ENABLED=True)
VIS_BRIGHT_THRESH = 18.0
NIR_RED_THRESH    =  1.5
WHITENESS_THRESH  =  0.35
SWIR_RED_THRESH   =  1.0
CLOUD_BUFFER_M    = 300     # EDT dilation radius (metres)

# Water detection – McFeeters NDWI = (B3 - B8A) / (B3 + B8A)
# Values > threshold → water.  0.0 catches open water; raise to 0.1–0.2
# to be more conservative (only flag clearly wet pixels).
WATER_NDWI_THRESH = 0.0

PIXEL_ACRES = (DST_RES ** 2) / 4046.856   # 10 m pixel → acres

# ── Panel display settings ────────────────────────────────────────────────────
PLOT_W, PLOT_H = 740, 740
TOOLS          = ['wheel_zoom', 'pan', 'box_zoom', 'reset', 'save']


# ══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ══════════════════════════════════════════════════════════════════════════════

def pct_stretch(arr, lo=2, hi=98):
    """Linear percentile stretch; NaN → 0."""
    valid = arr[~np.isnan(arr)]
    if valid.size == 0:
        return np.zeros_like(arr)
    vmin = float(np.percentile(valid, lo))
    vmax = float(np.percentile(valid, hi))
    out  = np.clip((arr - vmin) / max(vmax - vmin, 1e-6), 0.0, 1.0)
    out[np.isnan(arr)] = 0.0
    return out


def dilate_edt(mask_bad, buffer_m, pixel_m=10):
    """Dilate a boolean mask by buffer_m metres using Euclidean distance transform."""
    if buffer_m <= 0:
        return mask_bad
    dist = distance_transform_edt(~mask_bad) * pixel_m
    return dist <= buffer_m


def ensure_cached(items, bands, cache_dir):
    """
    Download missing band GeoTIFFs from Planetary Computer to *cache_dir*
    and return a new list of STAC items whose asset hrefs point to the local
    files.  Tiles that are already on disk are reused without any network
    request.

    File naming: ``<item.id>.<band>.tif``
    e.g. ``S2B_MSIL2A_20260408T170849_R112_T15STD_20260408T210053.B04.tif``
    """
    os.makedirs(cache_dir, exist_ok=True)
    local_items = []

    for item in items:
        local_item = item.clone()

        for band in bands:
            if band not in item.assets:
                continue

            fname      = f"{item.id}.{band}.tif"
            local_path = os.path.join(cache_dir, fname)

            if os.path.exists(local_path):
                print(f"    [cache] {fname}")
            else:
                url = item.assets[band].href   # already signed by PC modifier
                print(f"    [download] {fname} ...", end='', flush=True)
                resp = requests.get(url, stream=True, timeout=300)
                resp.raise_for_status()
                total = int(resp.headers.get('content-length', 0))
                downloaded = 0
                with open(local_path, 'wb') as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            print(f"\r    [download] {fname}  "
                                  f"{downloaded / 1e6:.0f} / {total / 1e6:.0f} MB",
                                  end='', flush=True)
                print(f"\r    [download] {fname}  "
                      f"{os.path.getsize(local_path) / 1e6:.0f} MB  done     ")

            # odc.stac.load() passes hrefs directly to rasterio.open().
            # A bare Windows path (C:\...) gets misread as a relative URL and
            # the PC base URL is prepended to it.  Converting to a file:// URI
            # tells rasterio unambiguously that this is a local file.
            from pathlib import Path
            local_item.assets[band].href = Path(local_path).as_uri()

        local_items.append(local_item)

    return local_items


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 – Search Planetary Computer
# ══════════════════════════════════════════════════════════════════════════════
print("Step 1 – Searching Planetary Computer for Sentinel-2 L2A ...")

import pystac_client
import planetary_computer

catalog = pystac_client.Client.open(
    PC_STAC_URL,
    modifier=planetary_computer.sign_inplace,
)
items = catalog.search(
    collections=['sentinel-2-l2a'],
    bbox=BBOX,
    datetime=f'{DATE}/{DATE}',
    query={'eo:cloud_cover': {'lt': CLOUD_MAX}},
).item_collection()

if len(items) == 0:
    raise RuntimeError(
        f"No Sentinel-2 L2A items found for {DATE} over Chase County "
        f"(cloud < {CLOUD_MAX}%).  Try widening the date range.")

print(f"  Found {len(items)} granule(s):")
for it in items:
    cc  = it.properties.get('eo:cloud_cover', '?')
    tid = it.properties.get('s2:mgrs_tile', it.id)
    print(f"    {it.datetime.date()}  tile={tid}  cloud={cc:.1f}%")


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 – Cache tiles locally, then load all 6 bands at 10 m
# ══════════════════════════════════════════════════════════════════════════════
BANDS = ['B04', 'B03', 'B02', 'B8A', 'B11', 'B12']

print(f"\nStep 2a – Caching tiles to {CACHE_DIR} ...")
local_items = ensure_cached(items, BANDS, CACHE_DIR)

print(f"\nStep 2b – Loading {', '.join(BANDS)} at {DST_RES} m from local cache ...")

import odc.stac

ds_raw = odc.stac.load(
    local_items,
    bands=BANDS,
    bbox=BBOX,
    crs=DST_CRS,
    resolution=DST_RES,
    resampling='bilinear',
    groupby='solar_day',
)

n_days = ds_raw.dims['time']
print(f"  Solar days found: {n_days} – using first ({ds_raw.time.values[0]})")
ds_raw = ds_raw.isel(time=0)

x_coords = ds_raw['x'].values   # UTM easting, ascending
y_coords = ds_raw['y'].values   # UTM northing, descending (north to south)
height, width = ds_raw.dims['y'], ds_raw.dims['x']
print(f"  Grid: {width} x {height} px  ({DST_RES} m/px)")

# Extract raw uint16 DN arrays from odc-stac (no automatic offset applied)
raw = {b: ds_raw[b].values.astype(np.float32) for b in BANDS}

# Nodata sentinel is DN == 0 — must be identified on raw values BEFORE the
# offset subtraction (0 - 1000 = -1000 would look like a valid dark pixel).
nodata_dn = {b: raw[b] == 0 for b in BANDS}

# Apply offset + scale: (DN - OFFSET) × SCALE → percent reflectance
B4  = (raw['B04'] - OFFSET) * SCALE
B3  = (raw['B03'] - OFFSET) * SCALE
B2  = (raw['B02'] - OFFSET) * SCALE
B8A = (raw['B8A'] - OFFSET) * SCALE
B11 = (raw['B11'] - OFFSET) * SCALE
B12 = (raw['B12'] - OFFSET) * SCALE

# Restore nodata as NaN using the pre-offset masks
for arr, band in zip([B4, B3, B2, B8A, B11, B12], BANDS):
    arr[nodata_dn[band]] = np.nan
del raw, nodata_dn

nodata = np.isnan(B4) | np.isnan(B8A) | np.isnan(B12)
coverage = (~nodata).mean() * 100
print(f"  Valid pixel coverage: {coverage:.1f}%")

# Sanity-check: print band medians so offset issues are visible in logs
for name, arr in [('B04', B4), ('B8A', B8A), ('B12', B12)]:
    valid = arr[~np.isnan(arr)]
    if valid.size:
        print(f"  {name} median reflectance: {np.median(valid):.1f}%  "
              f"(p5={np.percentile(valid,5):.1f}%  p95={np.percentile(valid,95):.1f}%)")


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 – Rasterio transform
# ══════════════════════════════════════════════════════════════════════════════
res_x = float(x_coords[1] - x_coords[0])
res_y = float(y_coords[1] - y_coords[0])   # negative (north-up)
transform = rasterio.transform.from_origin(
    west  = float(x_coords[0]) - res_x / 2,
    north = float(y_coords[0]) - res_y / 2,
    xsize = abs(res_x),
    ysize = abs(res_y),
)


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 – Write true-colour GeoTIFF
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 4 – Writing true-colour GeoTIFF ...")

r_u8 = (pct_stretch(B4) * 255).astype(np.uint8)
g_u8 = (pct_stretch(B3) * 255).astype(np.uint8)
b_u8 = (pct_stretch(B2) * 255).astype(np.uint8)

with rasterio.open(
    OUT_RGB_TIF, 'w',
    driver='GTiff', height=height, width=width,
    count=3, dtype=np.uint8,
    crs=CRS.from_string(DST_CRS),
    transform=transform,
    compress='lzw', photometric='RGB',
) as dst:
    dst.write(r_u8, 1)
    dst.write(g_u8, 2)
    dst.write(b_u8, 3)
    dst.update_tags(
        description='True colour (B04=R, B03=G, B02=B) – Chase County KS',
        date=DATE,
        source=f'Sentinel-2 L2A via Planetary Computer ({len(items)} granule(s))',
        resolution='10 m (native)',
        stretch='2%/98% percentile per band',
    )
print(f"  Saved -> {OUT_RGB_TIF}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 5 – Spectral indices, optional cloud mask, water mask
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 5 – Spectral indices + masks ...")

vis_mean  = (B4 + B3 + B2) / 3.0

# ── Cloud mask (optional) ─────────────────────────────────────────────────────
if CLOUD_MASK_ENABLED:
    nir_red   = B8A / (B4  + 1e-6)
    swir_red  = B12 / (B4  + 1e-6)
    whiteness = (np.abs(B4 - vis_mean) + np.abs(B3 - vis_mean)
                 + np.abs(B2 - vis_mean)) / (vis_mean + 1e-6)
    with np.errstate(invalid='ignore'):
        cloud_spectral = (
            ~nodata
            & (vis_mean  > VIS_BRIGHT_THRESH)
            & (nir_red   < NIR_RED_THRESH)
            & ((swir_red < SWIR_RED_THRESH) | (whiteness < WHITENESS_THRESH))
        )
    contaminated = dilate_edt(cloud_spectral, CLOUD_BUFFER_M, pixel_m=DST_RES)
    print(f"  Cloud + {CLOUD_BUFFER_M} m buffer: {contaminated.mean()*100:.1f}% masked")
else:
    contaminated = np.zeros(B4.shape, dtype=bool)
    print("  Cloud mask DISABLED")

# ── Water mask – McFeeters NDWI = (B3 - B8A) / (B3 + B8A) ───────────────────
with np.errstate(invalid='ignore'):
    ndwi  = (B3 - B8A) / (B3 + B8A + 1e-6)
    water = ~nodata & (ndwi > WATER_NDWI_THRESH)

if WATER_MASK_ENABLED:
    print(f"  Water pixels (NDWI > {WATER_NDWI_THRESH}): {water.sum():,}  "
          f"({water.mean()*100:.1f}% of scene)")
else:
    water = np.zeros(B4.shape, dtype=bool)
    print("  Water mask DISABLED")


# ══════════════════════════════════════════════════════════════════════════════
# Step 6 – Burn classification
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 6 – Burn classification ...")

nbr = (B8A - B12) / (B8A + B12 + 1e-6)

with np.errstate(invalid='ignore'):
    burn = (
        ~nodata
        & (vis_mean < BURN_VIS_THRESH)
        & (nbr      < BURN_NBR_THRESH)
        & (B12      > BURN_SWIR2_MIN)
    )

burn_valid   = burn & ~contaminated & ~water
print(f"  Raw burn pixels (before water/county): {burn.sum():,}")
print(f"  After water exclusion                : {burn_valid.sum():,}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 6b – Apply county boundary mask
# ══════════════════════════════════════════════════════════════════════════════
print(f"\nStep 6b – Applying {COUNTY_NAME} boundary mask ...")

county_gdf  = gpd.read_file(COUNTY_SHP)
county_row  = county_gdf[county_gdf['NAME'] == COUNTY_NAME]
if county_row.empty:
    raise ValueError(
        f"'{COUNTY_NAME}' not found in shapefile.  "
        f"Available names: {sorted(county_gdf['NAME'].tolist())}")

county_utm  = county_row.to_crs(DST_CRS)
county_geom = county_utm.geometry.iloc[0]   # single Polygon or MultiPolygon

# Rasterize the county polygon onto the exact output grid
county_mask = rio_rasterize(
    [(county_geom, 1)],
    out_shape=(height, width),
    transform=transform,
    fill=0,
    dtype=np.uint8,
).astype(bool)

burn_valid   = burn_valid & county_mask
acres_burned = float(burn_valid.sum()) * PIXEL_ACRES
print(f"  Pixels inside county                 : {county_mask.sum():,}")
print(f"  Burn pixels inside county            : {burn_valid.sum():,}")
print(f"  Acres burned: {acres_burned:,.0f}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 7 – Write burn mask GeoTIFF
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 7 – Writing burn mask GeoTIFF ...")

with rasterio.open(
    OUT_BURN_TIF, 'w',
    driver='GTiff', height=height, width=width,
    count=1, dtype=np.uint8,
    crs=CRS.from_string(DST_CRS),
    transform=transform,
    compress='lzw',
) as dst:
    dst.write(burn_valid.astype(np.uint8), 1)
    dst.update_tags(
        description='Burn mask: 1=burned, 0=not burned – Chase County KS',
        date=DATE,
        acres_burned=f'{acres_burned:.0f}',
        algorithm='NBR + water mask' + (' + spectral cloud mask' if CLOUD_MASK_ENABLED else ''),
        thresholds=f'vis<{BURN_VIS_THRESH}, NBR<{BURN_NBR_THRESH}, B12>{BURN_SWIR2_MIN}',
        water_mask=f'NDWI>{WATER_NDWI_THRESH}' if WATER_MASK_ENABLED else 'disabled',
        cloud_mask='enabled' if CLOUD_MASK_ENABLED else 'disabled',
        source=f'Sentinel-2 L2A 10m via Planetary Computer',
    )
print(f"  Saved -> {OUT_BURN_TIF}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 8 – Build Panel / HoloViews / datashader interactive app
#
# odc-stac returns y decreasing (north→south, standard GIS raster).
# HoloViews / datashader require y increasing for correct north-up display,
# so arrays and y coordinates are flipped once here.
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 8 – Building interactive Panel app ...")

y_hv = y_coords[::-1]   # ascending northing for HoloViews

def flip(arr):
    """Flip array vertically to match ascending y_hv coordinate order."""
    return arr[::-1, :]

# ── True-colour xarray Dataset ────────────────────────────────────────────────
rgb_hv = xr.Dataset(
    {
        'R': xr.DataArray(flip(r_u8), dims=['y', 'x'], coords={'y': y_hv, 'x': x_coords}),
        'G': xr.DataArray(flip(g_u8), dims=['y', 'x'], coords={'y': y_hv, 'x': x_coords}),
        'B': xr.DataArray(flip(b_u8), dims=['y', 'x'], coords={'y': y_hv, 'x': x_coords}),
    }
)

# ── Burn overlay: NaN = transparent, 1.0 = burned ────────────────────────────
# NaN pixels are skipped by datashader's mean aggregation and rendered as
# transparent by Bokeh, giving a clean overlay on the true-colour base.
burn_display = np.where(flip(burn_valid), 1.0, np.nan).astype(np.float32)
burn_hv = xr.DataArray(
    burn_display,
    dims=['y', 'x'],
    coords={'y': y_hv, 'x': x_coords},
)

# ── Water overlay: NaN = transparent, 1.0 = water ────────────────────────────
water_display = np.where(flip(water), 1.0, np.nan).astype(np.float32)
water_hv = xr.DataArray(
    water_display,
    dims=['y', 'x'],
    coords={'y': y_hv, 'x': x_coords},
)

# Custom 2-stop colormaps: transparent → solid colour
burn_cmap = mcolors.LinearSegmentedColormap.from_list(
    'burn_overlay',
    [(0.0, (0.0, 0.0, 0.0, 0.0)),         # transparent
     (1.0, (0.863, 0.118, 0.118, 1.0))],   # solid red
)
water_cmap = mcolors.LinearSegmentedColormap.from_list(
    'water_overlay',
    [(0.0, (0.0, 0.0, 0.0, 0.0)),         # transparent
     (1.0, (0.118, 0.510, 0.863, 1.0))],   # solid blue
)

# ── County boundary as HoloViews Path ────────────────────────────────────────
# Extract exterior ring coords (UTM metres) from the county polygon so the
# boundary can be drawn as a line overlay on top of both panels.
def _geom_to_path_coords(geom):
    """Return a list of coordinate arrays suitable for hv.Path from a
    Shapely Polygon or MultiPolygon."""
    if geom.geom_type == 'MultiPolygon':
        return [list(g.exterior.coords) for g in geom.geoms]
    return [list(geom.exterior.coords)]

county_path_hv = hv.Path(
    _geom_to_path_coords(county_geom),
    kdims=['x', 'y'],
).opts(
    opts.Path(color='yellow', line_width=2.0, line_dash='solid')
)

# ── HoloViews elements ────────────────────────────────────────────────────────
tc_element    = hv.RGB(rgb_hv, kdims=['x', 'y'], vdims=['R', 'G', 'B'])
burn_element  = hv.Image(burn_hv,  kdims=['x', 'y'], vdims=['burn'])
water_element = hv.Image(water_hv, kdims=['x', 'y'], vdims=['water'])

# ── Rasterize with datashader ─────────────────────────────────────────────────
# rasterize() creates a DynamicMap that re-renders at screen resolution on
# every zoom/pan event – no fixed downsampling, full 10 m detail when zoomed in.

# Left panel: true colour + county boundary
tc_raster = (rasterize(tc_element, expand=False) * county_path_hv).opts(
    opts.RGB(
        title=f'True Colour  –  Sentinel-2 L2A 10 m  |  2026-04-08  |  {COUNTY_NAME} boundary (yellow)',
        width=PLOT_W, height=PLOT_H,
        tools=TOOLS, active_tools=['wheel_zoom'],
        xlabel='UTM Easting (m, EPSG:32615)',
        ylabel='UTM Northing (m)',
        fontsize={'title': 9},
    )
)

# Burn overlay (red) and water overlay (blue)
burn_raster = rasterize(burn_element, expand=False).opts(
    opts.Image(cmap=burn_cmap,  clim=(0, 1), colorbar=False, alpha=0.75)
)
water_raster = rasterize(water_element, expand=False).opts(
    opts.Image(cmap=water_cmap, clim=(0, 1), colorbar=False, alpha=0.65)
)

cloud_str = 'cloud mask OFF' if not CLOUD_MASK_ENABLED else f'cloud mask ON ({CLOUD_BUFFER_M} m buffer)'
water_str = f'water masked (NDWI>{WATER_NDWI_THRESH})' if WATER_MASK_ENABLED else 'water mask OFF'

# Right panel: true colour base + burn (red) + water (blue)
tc_base_right = rasterize(
    hv.RGB(rgb_hv, kdims=['x', 'y'], vdims=['R', 'G', 'B']),
    expand=False,
).opts(
    opts.RGB(
        title=f'Burns (red) {acres_burned:,.0f} ac  |  Water (blue)  |  {cloud_str}',
        width=PLOT_W, height=PLOT_H,
        tools=TOOLS, active_tools=['wheel_zoom'],
        xlabel='UTM Easting (m, EPSG:32615)',
        ylabel='',
        fontsize={'title': 9},
    )
)

right_panel = tc_base_right * burn_raster * water_raster * county_path_hv

# ── Layout with shared axes (synchronized zoom / pan) ─────────────────────────
# shared_axes=True tells HoloViews to pass the same Bokeh x_range / y_range
# objects to both figures so any zoom or pan in one panel mirrors the other.
layout = (tc_raster + right_panel).opts(
    opts.Layout(shared_axes=True, merge_tools=False)
)

# ── Header banner ─────────────────────────────────────────────────────────────
header = pn.pane.HTML(f"""
<div style="
    background: #1a1a2e;
    color: white;
    padding: 12px 18px;
    font-family: sans-serif;
    font-size: 14px;
    border-radius: 4px 4px 0 0;
    line-height: 1.6;
">
  <b>Chase County, KS &nbsp;·&nbsp; Sentinel-2 L2A 10 m &nbsp;·&nbsp; {DATE}</b>
  &emsp;|&emsp;
  Burned area: <b style="color: #ff6b6b">{acres_burned:,.0f} acres</b>
  &emsp;|&emsp;
  {cloud_str} &nbsp;·&nbsp; {water_str} &nbsp;·&nbsp; no NLCD filter
  &emsp;|&emsp;
  <span style="color: #aaa">
    Scroll to zoom &nbsp;·&nbsp; drag to pan &nbsp;·&nbsp;
    panels are synchronized &nbsp;·&nbsp; full 10 m detail when zoomed in
  </span>
</div>
""", sizing_mode='stretch_width')

# ── Assemble Panel app ────────────────────────────────────────────────────────
app = pn.Column(
    header,
    pn.pane.HoloViews(layout, sizing_mode='fixed'),
    sizing_mode='stretch_width',
)

app.servable(title='Chase County Burn Analysis – 2026-04-08')

print("\nReady.  Open your browser at http://localhost:5006/chase_county_burn_analysis_10m")
print(f"  True-colour GeoTIFF : {os.path.basename(OUT_RGB_TIF)}")
print(f"  Burn mask GeoTIFF   : {os.path.basename(OUT_BURN_TIF)}")
print(f"  Acres burned        : {acres_burned:,.0f}")
