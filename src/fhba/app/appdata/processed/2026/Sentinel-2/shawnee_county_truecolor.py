"""
shawnee_county_truecolor.py

Produces a true-colour (R=B04, G=B03, B=B02) GeoTIFF of Shawnee County, KS
from HLS S30 raw granules for 2026-04-08 (day-of-year 068).

Resolution note
---------------
Although native Sentinel-2 bands B02/B03/B04 are 10 m, the HLS S30 product
harmonises everything to 30 m for consistency with Landsat (L30).  The output
GeoTIFF is therefore 30 m/pixel, which is the true data resolution.

Output
------
  shawnee_county_truecolor_2026-04-08.tif
  – 3-band uint8 RGB, EPSG:32615 (UTM zone 15N), 30 m, LZW-compressed
  – 2 % / 98 % percentile stretch applied per band for display
"""

import os
import glob
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds, reproject as rio_reproject
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
import rasterio.windows

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE    = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.abspath(os.path.join(HERE, '..', '..', '..', 'raw', '2026', 'Sentinel-2'))
OUT_TIF = os.path.join(HERE, 'shawnee_county_truecolor_2026-04-08.tif')

DATE_TAG = '2026068'   # DOY 068 = 2026-04-08

# ── Shawnee County, KS bounding box (WGS84) ───────────────────────────────────
# Topeka, KS is the county seat.  These bounds add a small margin so the full
# county outline is visible in the output.
MINLON, MAXLON = -96.10, -95.44
MINLAT, MAXLAT =  38.85,  39.20

# ── Output grid ───────────────────────────────────────────────────────────────
DST_CRS = CRS.from_epsg(32615)   # WGS 84 / UTM zone 15N
DST_RES = 30.0                   # HLS S30 native 30 m


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def make_dst_grid(minlon, minlat, maxlon, maxlat, dst_crs, res):
    """Project bbox to *dst_crs* and build a grid snapped to *res* boundaries."""
    x0, y0, x1, y1 = transform_bounds('EPSG:4326', dst_crs,
                                       minlon, minlat, maxlon, maxlat)
    x0 = np.floor(x0 / res) * res
    y0 = np.floor(y0 / res) * res
    x1 = np.ceil(x1  / res) * res
    y1 = np.ceil(y1  / res) * res
    w  = int(round((x1 - x0) / res))
    h  = int(round((y1 - y0) / res))
    transform = from_bounds(x0, y0, x1, y1, w, h)
    return transform, w, h


def mosaic_band(tile_files, dst_transform, dst_w, dst_h, dst_crs, band_label):
    """
    Incrementally mosaic a list of HLS single-band GeoTIFFs into one float32
    array (percent reflectance, 0-100 scale).

    Uses windowed reads so only the Shawnee County footprint is loaded from
    each tile — keeps per-tile RAM to a fraction of a full 3660×3660 read.
    First-valid-pixel strategy fills NaN gaps from subsequent tiles.
    """
    result = np.full((dst_h, dst_w), np.nan, dtype=np.float32)

    # Destination extent in dst_crs (for intersection test)
    dst_x0 = dst_transform.c
    dst_y1 = dst_transform.f
    dst_x1 = dst_x0 + dst_w * dst_transform.a
    dst_y0 = dst_y1 + dst_h * dst_transform.e   # e is negative

    n_used = 0
    for fp in tile_files:
        try:
            with rasterio.open(fp) as src:
                # ── 1. Quick intersection test ─────────────────────────────
                tile_in_dst = transform_bounds(
                    src.crs, dst_crs,
                    src.bounds.left, src.bounds.bottom,
                    src.bounds.right, src.bounds.top)
                if (tile_in_dst[2] < dst_x0 or tile_in_dst[0] > dst_x1 or
                        tile_in_dst[3] < dst_y0 or tile_in_dst[1] > dst_y1):
                    continue   # tile doesn't touch Shawnee County at all

                # ── 2. Compute read window in source tile coords ───────────
                # Project dst bbox back to source CRS for windowed read.
                county_in_src = transform_bounds(
                    dst_crs, src.crs,
                    dst_x0, dst_y0, dst_x1, dst_y1)
                win = src.window(*county_in_src).intersection(
                    rasterio.windows.Window(0, 0, src.width, src.height))
                win = win.round_lengths().round_offsets()
                if win.width <= 0 or win.height <= 0:
                    continue

                win_transform = src.window_transform(win)

                # ── 3. Read metadata ───────────────────────────────────────
                sf_raw   = (src.tags(1).get('scale_factor')
                            or src.tags().get('scale_factor'))
                sf       = float(sf_raw) if sf_raw is not None else 0.0001
                fv_raw   = (src.tags().get('_FillValue')
                            or src.tags(1).get('_FillValue'))
                fill_val = float(fv_raw) if fv_raw is not None else -9999.0

                # ── 4. Windowed read ───────────────────────────────────────
                raw = src.read(1, window=win).astype(np.float32)
                raw[raw == fill_val] = np.nan

                # ── 5. Reproject window → destination grid ─────────────────
                layer = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
                rio_reproject(
                    source=raw,
                    destination=layer,
                    src_transform=win_transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                    src_nodata=np.nan,
                    dst_nodata=np.nan,
                )
                del raw

                # ── 6. Scale to percent reflectance ────────────────────────
                valid = ~np.isnan(layer)
                layer[valid] = layer[valid] * sf * 100.0

                # ── 7. First-valid mosaic merge ────────────────────────────
                fill_mask = np.isnan(result) & ~np.isnan(layer)
                result[fill_mask] = layer[fill_mask]
                del layer

                n_used += 1
                tile_name = os.path.basename(fp)
                tile_id   = tile_name.split('.')[2]
                print(f"    [{band_label}] merged tile {tile_id}")

        except Exception as exc:
            print(f"    WARNING [{band_label}] {os.path.basename(fp)}: {exc}")

    if n_used == 0:
        print(f"    WARNING [{band_label}] no tiles intersected the AOI.")
    return result


def pct_stretch(arr, lo=2, hi=98):
    """Linear percentile stretch; NaN pixels become 0 in uint8 output."""
    valid = arr[~np.isnan(arr)]
    if valid.size == 0:
        return np.zeros_like(arr)
    vmin = float(np.percentile(valid, lo))
    vmax = float(np.percentile(valid, hi))
    stretched = np.clip((arr - vmin) / max(vmax - vmin, 1e-6), 0.0, 1.0)
    stretched[np.isnan(arr)] = 0.0
    return stretched


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

print("Step 1 – Computing output grid ...")
dst_transform, dst_w, dst_h = make_dst_grid(
    MINLON, MINLAT, MAXLON, MAXLAT, DST_CRS, DST_RES)
x0 = dst_transform.c
y1 = dst_transform.f
x1 = x0 + dst_w * dst_transform.a
y0 = y1 + dst_h * dst_transform.e
print(f"  Grid : {dst_w} x {dst_h} px  ({DST_RES:.0f} m/px, EPSG:{DST_CRS.to_epsg()})")
print(f"  Extent (UTM 15N) : x=[{x0:.0f}, {x1:.0f}]  y=[{y0:.0f}, {y1:.0f}]")

print(f"\nStep 2 – Locating HLS S30 tiles for {DATE_TAG} ...")
b04_files = sorted(glob.glob(os.path.join(RAW_DIR, f'HLS.S30.*.{DATE_TAG}*.B04.tif')))
b03_files = sorted(glob.glob(os.path.join(RAW_DIR, f'HLS.S30.*.{DATE_TAG}*.B03.tif')))
b02_files = sorted(glob.glob(os.path.join(RAW_DIR, f'HLS.S30.*.{DATE_TAG}*.B02.tif')))
print(f"  B04 (Red)  : {len(b04_files)} tile(s)")
print(f"  B03 (Green): {len(b03_files)} tile(s)")
print(f"  B02 (Blue) : {len(b02_files)} tile(s)")

if not b04_files:
    raise FileNotFoundError(
        f"No B04 tiles found for {DATE_TAG} under {RAW_DIR}")

print("\nStep 3 – Mosaicking Red (B04) ...")
red = mosaic_band(b04_files, dst_transform, dst_w, dst_h, DST_CRS, 'B04')

print("\nStep 4 – Mosaicking Green (B03) ...")
grn = mosaic_band(b03_files, dst_transform, dst_w, dst_h, DST_CRS, 'B03')

print("\nStep 5 – Mosaicking Blue (B02) ...")
blu = mosaic_band(b02_files, dst_transform, dst_w, dst_h, DST_CRS, 'B02')

print("\nStep 6 – Percentile stretch → uint8 ...")
r_u8 = (pct_stretch(red) * 255).astype(np.uint8)
g_u8 = (pct_stretch(grn) * 255).astype(np.uint8)
b_u8 = (pct_stretch(blu) * 255).astype(np.uint8)

nodata_mask = np.isnan(red) | np.isnan(grn) | np.isnan(blu)
pct_covered = (~nodata_mask).mean() * 100
print(f"  Coverage: {pct_covered:.1f}% valid pixels")

print(f"\nStep 7 – Writing {os.path.basename(OUT_TIF)} ...")
with rasterio.open(
    OUT_TIF, 'w',
    driver='GTiff',
    height=dst_h,
    width=dst_w,
    count=3,
    dtype=np.uint8,
    crs=DST_CRS,
    transform=dst_transform,
    compress='lzw',
    photometric='RGB',
) as dst:
    dst.write(r_u8, 1)
    dst.write(g_u8, 2)
    dst.write(b_u8, 3)
    dst.update_tags(
        description='True colour (B04=R, B03=G, B02=B) – Shawnee County KS',
        date='2026-04-08',
        source=f'HLS S30 ({len(b04_files)} tiles, {DATE_TAG})',
        note='HLS S30 is 30 m resolution (native S2 10 m resampled by HLS)',
        stretch='2%/98% percentile per band',
    )

print(f"\nDone.  Saved -> {OUT_TIF}")
print(f"  Size: {dst_w} x {dst_h} px  (~{dst_w * dst_h * 3 / 1e6:.1f} MB uncompressed)")
