"""
shawnee_county_truecolor_10m.py

Downloads and produces a true-colour (R=B04, G=B03, B=B02) GeoTIFF of
Shawnee County, KS at native Sentinel-2 10 m resolution using the
Microsoft Planetary Computer STAC catalog (Sentinel-2 L2A collection).

Unlike the HLS S30 product (which resamples to 30 m), the L2A product
keeps the native 10 m resolution for B02, B03, and B04.

Requirements (installed automatically if missing):
    pystac-client  planetary-computer  odc-stac

Output
------
  shawnee_county_truecolor_10m_2026-04-08.tif
  – 3-band uint8 RGB, EPSG:32615 (UTM zone 15N), 10 m, LZW-compressed
  – 2 % / 98 % percentile stretch per band
"""

import os
import numpy as np
import rasterio
from rasterio.crs import CRS

# ── Paths ─────────────────────────────────────────────────────────────────────
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
#OUT_TIF = os.path.join(OUT_DIR, 'shawnee_county_truecolor_10m_2026-04-08.tif')
OUT_TIF = os.path.join(OUT_DIR, 'chase_county_truecolor_10m_2026-04-08.tif')

# ── AOI: Shawnee County, KS (WGS84) ──────────────────────────────────────────
#BBOX      = [-96.10, 38.85, -95.44, 39.20]   # [minlon, minlat, maxlon, maxlat]
BBOX = [-96.84, 38.09, -96.35, 38.52] #chase county
DATE      = '2026-04-08'
DATE_RANGE = f'{DATE}/{DATE}'

# ── Output grid ───────────────────────────────────────────────────────────────
DST_CRS = 'EPSG:32615'   # WGS 84 / UTM zone 15N – covers Topeka area
DST_RES = 10             # native Sentinel-2 resolution for B02/B03/B04

CLOUD_MAX = 80           # skip granules with > 80 % cloud cover

PC_STAC_URL = 'https://planetarycomputer.microsoft.com/api/stac/v1'


# ══════════════════════════════════════════════════════════════════════════════
# 1  Search Planetary Computer
# ══════════════════════════════════════════════════════════════════════════════
print("Step 1 – Searching Planetary Computer for Sentinel-2 L2A ...")
print(f"  AOI  : {BBOX}")
print(f"  Date : {DATE}")

import pystac_client
import planetary_computer

catalog = pystac_client.Client.open(
    PC_STAC_URL,
    modifier=planetary_computer.sign_inplace,
)

search = catalog.search(
    collections=['sentinel-2-l2a'],
    bbox=BBOX,
    datetime=DATE_RANGE,
    query={'eo:cloud_cover': {'lt': CLOUD_MAX}},
)
items = search.item_collection()

if len(items) == 0:
    raise RuntimeError(
        f"No Sentinel-2 L2A items found for {DATE} over Shawnee County "
        f"with cloud cover < {CLOUD_MAX}%.  Try widening the date range "
        f"or increasing CLOUD_MAX.")

print(f"  Found {len(items)} granule(s):")
for it in items:
    cc  = it.properties.get('eo:cloud_cover', '?')
    tid = it.properties.get('s2:mgrs_tile', it.id)
    print(f"    {it.datetime.date()}  tile={tid}  cloud={cc:.1f}%")


# ══════════════════════════════════════════════════════════════════════════════
# 2  Load bands at 10 m via odc-stac (streams windowed COGs – no full download)
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 2 – Loading B04 / B03 / B02 at 10 m (streaming COGs) ...")

import odc.stac

ds = odc.stac.load(
    items,
    bands=['B04', 'B03', 'B02'],
    bbox=BBOX,
    crs=DST_CRS,
    resolution=DST_RES,
    resampling='bilinear',
    groupby='solar_day',       # mosaic multiple tiles from the same overpass
)

# odc-stac returns an xarray Dataset with a 'time' dimension; squeeze to 2D.
# If multiple days came back, take the first (closest to DATE).
ds = ds.isel(time=0)

print(f"  Grid : {ds.dims['x']} x {ds.dims['y']} px  ({DST_RES} m/px)")

# Sentinel-2 L2A surface reflectance is stored as uint16.
# Processing baseline >= 04.00 (this scene is 05.12) adds a +1000 DN offset.
# Correct formula: reflectance (%) = (DN - 1000) / 10 000 × 100
# Without the offset every pixel reads ~10 % too bright.
OFFSET = 1000
SCALE  = 1.0 / 10000.0 * 100.0

# Identify nodata (DN == 0) on raw values BEFORE subtracting the offset,
# because 0 - 1000 = -1000 would look like a valid very dark pixel.
raw_B4 = ds['B04'].values.astype(np.float32)
raw_B3 = ds['B03'].values.astype(np.float32)
raw_B2 = ds['B02'].values.astype(np.float32)

nd_B4, nd_B3, nd_B2 = raw_B4 == 0, raw_B3 == 0, raw_B2 == 0

B4 = (raw_B4 - OFFSET) * SCALE
B3 = (raw_B3 - OFFSET) * SCALE
B2 = (raw_B2 - OFFSET) * SCALE

B4[nd_B4] = np.nan
B3[nd_B3] = np.nan
B2[nd_B2] = np.nan

coverage = (~np.isnan(B4)).mean() * 100
print(f"  Coverage: {coverage:.1f}% valid pixels")


# ══════════════════════════════════════════════════════════════════════════════
# 3  Percentile stretch → uint8
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 3 – Percentile stretch → uint8 ...")

def pct_stretch(arr, lo=2, hi=98):
    valid = arr[~np.isnan(arr)]
    if valid.size == 0:
        return np.zeros_like(arr)
    vmin = float(np.percentile(valid, lo))
    vmax = float(np.percentile(valid, hi))
    out  = np.clip((arr - vmin) / max(vmax - vmin, 1e-6), 0.0, 1.0)
    out[np.isnan(arr)] = 0.0
    return out

r_u8 = (pct_stretch(B4) * 255).astype(np.uint8)
g_u8 = (pct_stretch(B3) * 255).astype(np.uint8)
b_u8 = (pct_stretch(B2) * 255).astype(np.uint8)


# ══════════════════════════════════════════════════════════════════════════════
# 4  Write GeoTIFF
# ══════════════════════════════════════════════════════════════════════════════
print(f"\nStep 4 – Writing {os.path.basename(OUT_TIF)} ...")

# Reconstruct the rasterio transform from the odc-stac dataset coordinates.
x_coords = ds['x'].values
y_coords = ds['y'].values
res_x    = float(x_coords[1] - x_coords[0])
res_y    = float(y_coords[1] - y_coords[0])   # negative (north-up)
transform = rasterio.transform.from_origin(
    west  = float(x_coords[0]) - res_x / 2,
    north = float(y_coords[0]) - res_y / 2,
    xsize = abs(res_x),
    ysize = abs(res_y),
)
height, width = r_u8.shape

with rasterio.open(
    OUT_TIF, 'w',
    driver='GTiff',
    height=height,
    width=width,
    count=3,
    dtype=np.uint8,
    crs=CRS.from_string(DST_CRS),
    transform=transform,
    compress='lzw',
    photometric='RGB',
) as dst:
    dst.write(r_u8, 1)
    dst.write(g_u8, 2)
    dst.write(b_u8, 3)
    granule_ids = ', '.join(it.id for it in items)
    dst.update_tags(
        description='True colour (B04=R, B03=G, B02=B) – Shawnee County KS',
        date=DATE,
        source=f'Sentinel-2 L2A via Planetary Computer ({len(items)} granule(s))',
        granules=granule_ids,
        resolution='10 m (native)',
        stretch='2%/98% percentile per band',
    )

size_mb = height * width * 3 / 1e6
print(f"\nDone.  Saved -> {OUT_TIF}")
print(f"  Size : {width} x {height} px  (~{size_mb:.0f} MB uncompressed)")
