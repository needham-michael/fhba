"""Registry classes to manage satellite granule metadata and download/processing status."""
import importlib
import inspect
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from unicodedata import category
import warnings
import yaml
import earthaccess
import holoviews as hv
import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import xarray as xr
from satpy.scene import Scene
from satpy.enhancements import overlays
from fhba.eucl_classifier import classify_pixels_eucl
from fhba.image import nonlinear_enhancement

logger = logging.getLogger(__name__)

# ── HLS (Harmonized Landsat Sentinel-2) helpers ───────────────────────────────
# Satpy has no reader for the individual-band GeoTIFF files produced by the
# HLSL30 product, so Landsat granules are loaded with rioxarray instead.
def _load_hls_bands(granule_files, band_list, area_def, resampler='nearest'):
    """Load, merge tiles and reproject HLS individual-band GeoTIFF files.
    HLS L30 filenames follow the pattern
    ``HLS.L30.<TILE>.<DATETIME>.v<VER>.<BAND>.tif``.
    Reflectance bands (everything except ``Fmask``) are scaled from raw int16
    to percent reflectance (0–100) by applying ``scale_factor * 100`` so that
    values are on the same scale as Satpy-processed VIIRS/MODIS files.
    Fmask is kept as-is (raw uint8 bit-flags, scale_factor=1).

    Parameters
    ----------
    granule_files : list of str
        All HLS .tif files for one date (all tiles and bands).
    band_list : list of str
        Band names to load (e.g. ['B4','B5','B6','B7','Fmask']).
        Both zero-padded ('B04') and unpadded ('B4') forms are accepted.
    area_def : pyresample.AreaDefinition
        Target grid.
    resampler : str, default 'nearest'
        Resampling algorithm ('nearest', 'bilinear', 'ewa'/'average').

    Returns
    -------
    xr.Dataset with one float32 variable per band on the *area_def* grid.
    """
    import rioxarray as rxr
    from rasterio.enums import Resampling as RioResampling
    from rasterio.transform import from_bounds
    _RESAMPLING = {
        'nearest': RioResampling.nearest,
        'bilinear': RioResampling.bilinear,
        'ewa': RioResampling.average,
        'average': RioResampling.average,
    }
    rio_resampling = _RESAMPLING.get(resampler, RioResampling.nearest)

    def _band_from_path(p):
        # 'HLS.L30.T14SNH.2026054T170656.v2.0.B04.tif' → 'B04'
        return os.path.splitext(os.path.basename(p))[0].rsplit('.', 1)[-1]

    # Build file index with both zero-padded ('B04') and unpadded ('B4') keys
    files_by_band: dict = {}
    for f in granule_files:
        b = _band_from_path(f)
        files_by_band.setdefault(b, []).append(f)
        # Strip leading zeros: B04 → B4, B11 stays B11 (no leading zero)
        b_short = re.sub(r'^([A-Za-z]+)0+(\d+)$',
                         lambda m: m.group(1) + str(int(m.group(2))), b)
        if b_short != b:
            files_by_band.setdefault(b_short, []).append(f)

    # Target grid parameters from pyresample AreaDefinition
    x_ll, y_ll, x_ur, y_ur = area_def.area_extent
    width, height = area_def.width, area_def.height
    dst_transform = from_bounds(x_ll, y_ll, x_ur, y_ur, width, height)
    dst_crs = area_def.crs

    # Pixel-centre coordinate arrays (y descends: north → south)
    pw = (x_ur - x_ll) / width
    ph = (y_ur - y_ll) / height
    xs = np.array([x_ll + pw * (i + 0.5) for i in range(width)])
    ys = np.array([y_ur - ph * (j + 0.5) for j in range(height)])

    das = {}
    for band in band_list:
        if band == 'true_color':
            continue  # generated separately
        if band not in files_by_band:
            logger.warning("HLS: band '%s' not found in downloaded files.", band)
            continue
        tile_das = []
        for fp in files_by_band[band]:
            try:
                da = rxr.open_rasterio(fp, masked=True).squeeze('band', drop=True)
                da = da.rio.reproject(
                    dst_crs=dst_crs,
                    shape=(height, width),
                    transform=dst_transform,
                    resampling=rio_resampling,
                    nodata=np.nan,
                )
                # Apply scale factor to convert to percent reflectance
                # (Fmask has scale_factor=1, reflectance bands use 0.0001)
                if band.lower() != 'fmask':
                    sf = float(da.attrs.get('scale_factor', 0.0001))
                    da = da * sf * 100.0  # → percent reflectance, same as VIIRS
                tile_das.append(da)
            except Exception as exc:
                logger.warning("Could not load HLS file '%s': %s", fp, exc)
        if not tile_das:
            continue
        # Mosaic: fill NaN gaps in tile[0] from subsequent tiles
        merged = tile_das[0].copy()
        for other in tile_das[1:]:
            merged = merged.where(merged.notnull(), other)
        # Assign canonical coordinate values and strip spatial_ref scalar
        merged = merged.assign_coords(x=('x', xs), y=('y', ys))
        merged = merged.drop_vars('spatial_ref', errors='ignore')
        # Remove CF encoding attributes (scaling already applied above).
        for cf_attr in ('scale_factor', 'add_offset', '_FillValue'):
            merged.attrs.pop(cf_attr, None)
        das[band] = merged.astype('float32')

    return xr.Dataset(das)


def _make_hls_truecolor_png(hls_ds, out_path, county_shp=None, target_crs=None):
    """Write a true-colour PNG from HLS bands B4 (Red), B3 (Green), B2 (Blue).
    Applies a percentile stretch to each band, stacks into an RGB image, and
    optionally overlays county boundaries from *county_shp*.
    Returns True on success, False if required bands are missing.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    rgb_bands = ['B4', 'B3', 'B2']
    missing = [b for b in rgb_bands if b not in hls_ds]
    if missing:
        logger.warning(
            "Cannot generate HLS true-colour PNG: bands %s missing.", missing)
        return False

    def _pct_stretch(arr, lo=2, hi=98):
        vmin = np.nanpercentile(arr, lo)
        vmax = np.nanpercentile(arr, hi)
        return np.clip((arr - vmin) / max(vmax - vmin, 1e-10), 0, 1)

    r = _pct_stretch(hls_ds['B4'].values)
    g = _pct_stretch(hls_ds['B3'].values)
    b = _pct_stretch(hls_ds['B2'].values)
    rgb = np.stack([r, g, b], axis=-1)

    xs = hls_ds['B4'].x.values
    ys = hls_ds['B4'].y.values
    extent = [xs[0], xs[-1], ys[-1], ys[0]]  # [left, right, bottom, top]

    fig, ax = plt.subplots(1, 1, figsize=(5, 10), dpi=150)
    ax.imshow(rgb, origin='upper', extent=extent, aspect='auto',
              interpolation='nearest')

    if county_shp and os.path.exists(str(county_shp)):
        try:
            counties = gpd.read_file(county_shp)
            if target_crs is not None:
                counties = counties.to_crs(target_crs)
            counties.boundary.plot(ax=ax, color='white', linewidth=0.75)
        except Exception as exc:
            logger.warning("County overlay failed for HLS truecolor: %s", exc)

    ax.set_axis_off()
    plt.tight_layout(pad=0)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, bbox_inches='tight', pad_inches=0, dpi=150)
    plt.close(fig)
    return True


class GranuleManager:
    """Maintain status of granule downloads, file QC, and processing."""
    def __init__(self, satellite_name=None, instrument=None, short_name_list=None,
                 start_date=None, end_date=None, raw_data_dir=None, processed_data_dir=None,
                 truecolor_img_dir=None, min_lat=None, min_lon=None, max_lat=None,
                 max_lon=None, spatial_name=None, satpy_area_def=None, county_shp=None,
                 raw_granules_by_date=None, processed_granules_by_date=None,
                 truecolor_images_by_date=None, full_band_list=None, nir_red_band_list=None,
                 userpts_dir=None, cloud_mask_short_name=None, userpts_by_date=None,
                 burnmasks_by_date=None, burnmask_dir=None,
                 nbr_bands=None, ndvi_bands=None):

        self.satellite_name = satellite_name
        self.instrument = instrument.lower() if instrument is not None else None
        self.short_name_list = short_name_list if short_name_list is not None else []
        self.cloud_mask_short_name = cloud_mask_short_name
        self.start_date = start_date
        self.end_date = end_date
        self.raw_data_dir = raw_data_dir
        self.processed_data_dir = processed_data_dir
        self.truecolor_img_dir = truecolor_img_dir
        self.userpts_dir = userpts_dir
        self.min_lat = min_lat
        self.min_lon = min_lon
        self.max_lat = max_lat
        self.max_lon = max_lon
        self.spatial_name = spatial_name
        self.spatial = (self.min_lon, self.min_lat, self.max_lon, self.max_lat)
        self.satpy_area_def = satpy_area_def
        self.county_shp = county_shp
        self.full_band_list = full_band_list if full_band_list is not None else []
        self.nir_red_band_list = nir_red_band_list if nir_red_band_list is not None else []
        self.nbr_bands = nbr_bands  # [nir_band, swir_band] or None
        self.ndvi_bands = ndvi_bands  # [nir_band, red_band] or None

        if self.instrument not in ['viirs', None]:
            if self.instrument in ['landsat', 'modis']:
                raise NotImplementedError(f"Instrument '{instrument}' is not yet supported. Only VIIRS imagery is currently implemented.")
            else:
                raise ValueError(f"Instrument {instrument} not recognized. Valid options are 'viirs'.")

        # Define dictionaries to maintain status of satellite granules by date at
        # various workflow stages.
        if self.start_date is not None and self.end_date is not None:
            date_range = pd.date_range(
                start=self.start_date, end=self.end_date, freq='D'
            ).strftime("%Y-%m-%d").tolist()
            self.download_status = {d: False for d in date_range}
            self.cloud_mask_download_status = {d: False for d in date_range}
            self.qc_status = {d: -1 for d in date_range}
            self.processing_status = {d: False for d in date_range}
            self.user_categorization_by_date = {d: "Uncategorized" for d in date_range}
            self.analysis_status = {d: "Unanalyzed" for d in date_range}
            self.categorization_status = {d: "Uncategorized" for d in date_range}

        if raw_granules_by_date is None:
            self.raw_granules_by_date = {}
            self.raw_cloud_mask_granules_by_date = {}
        if processed_granules_by_date is None:
            self.processed_granules_by_date = {}
            self.processed_cloud_masks_by_date = {}
        if truecolor_images_by_date is None:
            self.truecolor_images_by_date = {}
        if userpts_by_date is None:
            self.userpts_by_date = {}
        if burnmasks_by_date is None:
            self.burnmasks_by_date = {}

    def classify_pixels(self, date, method='eucl', landcover_mask_file=None,
                        min_area_pixels=5, pre_fire_date=None, **clf_kwargs):
        """Classify pixels as burned/unburned for a given date.
        Parameters
        ----------
        date : str
            Analysis date (YYYY-MM-DD).
        method : {"eucl", "rf", "svm"}, default "eucl"
            Classification method.
        landcover_mask_file : str, optional
            Path to resampled NLCD land-cover mask. Resolved automatically if None.
        min_area_pixels : int, default 5
            Minimum burn-patch size in pixels; smaller patches are removed.
        pre_fire_date : str, optional
            Pre-fire reference date for dNBR computation. When provided, dNBR
            is computed and appended as an additional classification feature.
        **clf_kwargs
            Extra kwargs forwarded to the ML classifier (method="rf"/"svm" only).
        Returns
        -------
        burnmask : xr.Dataset
        confidence_ds : xr.Dataset
        """
        nc_file = self.processed_granules_by_date[date]
        points_csv_file = self.userpts_by_date[date]
        cldmask_file = self.processed_cloud_masks_by_date.get(date)

        if landcover_mask_file is None:
            landcover_mask_file = importlib.resources.files("fhba.app.appdata.annual_nlcd") / \
                                  f"NLCD_LandMask_{self.spatial_name}.tif"

        # Optionally compute dNBR
        dnbr_array = None
        if pre_fire_date is not None and self.nbr_bands is not None:
            from fhba.eucl_classifier import compute_dnbr
            pre_nc = self.processed_granules_by_date.get(pre_fire_date)
            if pre_nc is not None:
                nir_band, swir_band = self.nbr_bands
                dnbr_array = compute_dnbr(pre_nc, nc_file, nir_band, swir_band)
            else:
                logger.warning("Pre-fire date %s has no processed granule; skipping dNBR.", pre_fire_date)

        common_kwargs = dict(
            userpts_csv=points_csv_file,
            processed_nc=nc_file,
            cldmask_nc=cldmask_file,
            landmask_nc=landcover_mask_file,
            band_list=[b for b in self.full_band_list if b != 'true_color'] or None,
            nbr_bands=self.nbr_bands,
            ndvi_bands=self.ndvi_bands,
            dnbr_array=dnbr_array,
            area_def=self.satpy_area_def,
            min_area_pixels=min_area_pixels,
        )

        if method == 'eucl':
            burnmask, confidence_ds = classify_pixels_eucl(**common_kwargs)
        elif method in ('rf', 'svm'):
            from fhba.ml_classifier import classify_pixels_ml
            burnmask, confidence_ds = classify_pixels_ml(
                method=method, **common_kwargs, **clf_kwargs)
        else:
            raise NotImplementedError(f"Classification method '{method}' not implemented. "
                                      f"Choose from 'eucl', 'rf', or 'svm'.")
        return burnmask, confidence_ds

    # ── Helper methods ──────────────────────────────────────────────────────
    def get_clear_processed_dates(self):
        """Return dates that have been processed and categorized as clear.
        Returns
        -------
        list of str
            Dates where processing_status is True and user_categorization is
            'Fully Clear' or 'Mostly Clear', in chronological order.
        """
        clear_cats = {"Fully Clear", "Mostly Clear"}
        return sorted([
            d for d in self.processing_status
            if self.processing_status.get(d) is True
            and d in self.processed_granules_by_date
            and self.user_categorization_by_date.get(d) in clear_cats
        ])

    def auto_categorize_cloud_cover(self, date, clear_threshold=0.80):
        """Automatically set user_categorization for *date* from the cloud mask.
        Uses the fraction of clear-sky pixels to assign one of the four standard
        categories. Requires a processed cloud mask for the date.
        Parameters
        ----------
        date : str
        clear_threshold : float, default 0.80
            Pixel-level confidence threshold used to determine clear sky.
        Returns
        -------
        str
            The assigned category string.
        """
        cldmask_file = self.processed_cloud_masks_by_date.get(date)
        if cldmask_file is None or not os.path.exists(str(cldmask_file)):
            logger.warning("No cloud mask available for date %s; skipping auto-categorization.", date)
            return "Uncategorized"

        with xr.open_dataset(cldmask_file) as ds:
            if 'Clear_Sky_Confidence' in ds:
                # VIIRS / MODIS: continuous confidence score in [0, 1]
                conf = ds['Clear_Sky_Confidence']
                clear_frac = float((conf >= clear_threshold).mean())
            elif 'Fmask' in ds:
                # Landsat HLS Fmask uint8 bit-field:
                # bit 1 = cloud, bit 2 = adjacent cloud, bit 3 = cloud shadow
                # NaN (no-data) pixels are treated as cloudy (fill with 0xFF).
                fmask = ds['Fmask'].fillna(255).astype(int)
                clear_frac = float(((fmask & 0x0E) == 0).mean())
            else:
                logger.warning(
                    "Cloud mask file for %s has neither 'Clear_Sky_Confidence' "
                    "nor 'Fmask'; skipping categorization.", date)
                return "Uncategorized"

        if clear_frac >= 0.90:
            category = "Fully Clear"
        elif clear_frac >= 0.70:
            category = "Mostly Clear"
        elif clear_frac >= 0.30:
            category = "Mostly Cloudy"
        else:
            category = "Fully Cloudy"
        self.update_user_categorization(date, category)
        return category

    def classify_pixels_date_range(self, method='eucl', min_area_pixels=5, **clf_kwargs):
        """Batch-classify all dates that have user training points.
        Parameters
        ----------
        method : {"eucl","rf","svm"}, default "eucl"
        min_area_pixels : int, default 5
        **clf_kwargs
            Forwarded to the ML classifier when method is "rf" or "svm".
        """
        if not self.userpts_by_date:
            logger.warning("No user points found; nothing to batch-classify.")
            return
        os.makedirs(self.burnmask_dir, exist_ok=True)

        for date in sorted(self.userpts_by_date.keys()):
            if date not in self.processed_granules_by_date:
                logger.warning("Skipping %s: no processed granule.", date)
                continue
            logger.info("Batch classifying %s with method=%s ...", date, method)
            try:
                burnmask, _ = self.classify_pixels(
                    date=date, method=method, min_area_pixels=min_area_pixels, **clf_kwargs)
                import rasterio
                burnmask = burnmask.rio.write_crs(
                    rasterio.crs.CRS.from_user_input(self.satpy_area_def.proj_str))
                out_file = os.path.join(
                    self.burnmask_dir,
                    f"{self.satellite_name}_{self.spatial_name}_{date}_burnmask.tif")
                burnmask.rio.to_raster(out_file)
                if not hasattr(self, 'burnmask_by_date') or self.burnmask_by_date is None:
                    self.burnmask_by_date = {}
                self.burnmask_by_date[date] = out_file
                self.update_categorization_status(date, "Categorized")
                logger.info("Saved burnmask for %s → %s", date, out_file)
            except Exception as exc:
                logger.error("Error classifying %s: %s", date, exc)

    def aggregate_burnmasks(self, out_file=None):
        """Union all finalized burn masks into a single seasonal burn map.
        Parameters
        ----------
        out_file : str, optional
            Output GeoTIFF path. Defaults to
            ``<burnmask_dir>/<satellite>_<spatial>_seasonal_burnmask.tif``.
        Returns
        -------
        str
            Path to the written seasonal burn map GeoTIFF.
        """
        import rioxarray as rxr
        burnmask_files = getattr(self, 'burnmask_by_date', {})
        if not burnmask_files:
            raise ValueError("No finalized burn masks found. Run export_burnmask for at least one date first.")

        arrays = []
        for date, path in sorted(burnmask_files.items()):
            if path and os.path.exists(path):
                da = rxr.open_rasterio(path).squeeze()
                da = da.expand_dims(dim={'time': [date]})
                arrays.append(da)
        if not arrays:
            raise ValueError("Burn mask files listed in registry do not exist on disk.")

        stacked = xr.concat(arrays, dim='time')
        seasonal = stacked.max(dim='time')

        if out_file is None:
            os.makedirs(self.burnmask_dir, exist_ok=True)
            year = self.start_date.split('-')[0]
            out_file = os.path.join(
                self.burnmask_dir,
                f"{self.satellite_name}_{self.spatial_name}_{year}_seasonal_burnmask.tif")
        seasonal.rio.to_raster(out_file)
        logger.info("Seasonal burn map written to %s", out_file)
        return out_file

    def compute_burn_area_by_county(self, burnmask_file):
        """Compute burned area statistics per county from a burn mask GeoTIFF.
        
        Calculates areas using both UTM Zone 14N (EPSG:32614) and Albers Equal-Area (EPSG:5070)
        projections to compare differences. Includes a total row summing all counties.
        
        Parameters
        ----------
        burnmask_file : str
            Path to a binary burn mask GeoTIFF (1 = burned, 0 = not burned).
        Returns
        -------
        gdf : geopandas.GeoDataFrame
            County-level statistics with columns:
            county_name, burned_area_km2_utm, burned_area_acres_utm,
            burned_area_km2_5070, burned_area_acres_5070.
            Includes a 'Total' row at the end.
        """
        import rioxarray as rxr
        counties = gpd.read_file(self.county_shp + ".shp")
        with rxr.open_rasterio(burnmask_file) as ds:
            # Reproject to UTM Zone 14N for area calculation
            ds_utm = ds.rio.reproject("EPSG:32614")
            res_x_utm, res_y_utm = ds_utm.rio.resolution()
            pixel_area_m2_utm = abs(res_x_utm * res_y_utm)
            pixel_area_km2_utm = pixel_area_m2_utm / 1_000_000
            pixel_area_acres_utm = pixel_area_m2_utm / 4046.856
            counties_utm = counties.to_crs("EPSG:32614")
            
            # Reproject to Albers Equal-Area (EPSG:5070) for comparison
            ds_5070 = ds.rio.reproject("EPSG:5070")
            res_x_5070, res_y_5070 = ds_5070.rio.resolution()
            pixel_area_m2_5070 = abs(res_x_5070 * res_y_5070)
            pixel_area_km2_5070 = pixel_area_m2_5070 / 1_000_000
            pixel_area_acres_5070 = pixel_area_m2_5070 / 4046.856
            counties_5070 = counties.to_crs("EPSG:5070")

            records = []
            total_km2_utm = 0.0
            total_acres_utm = 0.0
            total_km2_5070 = 0.0
            total_acres_5070 = 0.0
            
            name_col = next((c for c in counties.columns if 'name' in c.lower()), counties.columns[0])
            
            for idx in range(len(counties)):
                county_utm = counties_utm.iloc[idx]
                county_5070 = counties_5070.iloc[idx]
                county_name = counties.iloc[idx][name_col]
                
                # Calculate for UTM
                try:
                    clipped_utm = ds_utm.rio.clip([county_utm.geometry], drop=True, all_touched=False)
                    n_burned_utm = int((clipped_utm == 1).sum())
                except Exception:
                    n_burned_utm = 0
                km2_utm = round(n_burned_utm * pixel_area_km2_utm, 4)
                acres_utm = round(n_burned_utm * pixel_area_acres_utm, 1)
                
                # Calculate for EPSG:5070
                try:
                    clipped_5070 = ds_5070.rio.clip([county_5070.geometry], drop=True, all_touched=False)
                    n_burned_5070 = int((clipped_5070 == 1).sum())
                except Exception:
                    n_burned_5070 = 0
                km2_5070 = round(n_burned_5070 * pixel_area_km2_5070, 4)
                acres_5070 = round(n_burned_5070 * pixel_area_acres_5070, 1)
                
                records.append({
                    'county_name': county_name,
                    'burned_area_km2_utm': km2_utm,
                    'burned_area_acres_utm': acres_utm,
                    'burned_area_km2_5070': km2_5070,
                    'burned_area_acres_5070': acres_5070,
                })
                
                total_km2_utm += km2_utm
                total_acres_utm += acres_utm
                total_km2_5070 += km2_5070
                total_acres_5070 += acres_5070
            
            # Add total row
            records.append({
                'county_name': 'Total',
                'burned_area_km2_utm': round(total_km2_utm, 4),
                'burned_area_acres_utm': round(total_acres_utm, 1),
                'burned_area_km2_5070': round(total_km2_5070, 4),
                'burned_area_acres_5070': round(total_acres_5070, 1),
            })
        
        # Create GeoDataFrame with geometries for counties and None for total
        geometries = list(counties['geometry'].values) + [None]
        gdf = gpd.GeoDataFrame(records, geometry=geometries, crs=counties.crs)
        return gdf

    def get_hms_active_fire_overlay(self, date, lookback_days=3, buffer_days=0, alpha=1.0):
        """
        Fetch NOAA Hazard Mapping System fire points and return as hv.Points overlay.
        Stratifies fires by age and colors them using the YlOrBr colormap (yellow→orange→brown).
        
        Uses NOAA text point files (Lon, Lat, YearDay, Time, Satellite, Method, Ecosystem, FRP).
        
        Parameters
        ----------
        date : str
            Reference date (YYYY-MM-DD).
        lookback_days : int, default 3
            Number of days to look back from reference date (inclusive).
        buffer_days : int, default 0
            Additional offset (deprecated; kept for backward compatibility).        
        alpha : float, default 1.0
            Opacity of the fire point markers (0.0 = fully transparent, 1.0 = fully opaque).
        
        Returns
        -------
        hv.Overlay or None
            Combined HoloViews overlay with age-stratified fire points, or None if no fires found.
        """
        import io
        import requests
        import pandas as pd
        from matplotlib.cm import YlOrBr

        def _hms_url_for_date(dt):
            return (
                "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Text/"
                f"{dt.year}/{dt.month:02d}/hms_fire{dt.year}{dt.month:02d}{dt.day:02d}.txt"
            )

        def _load_points_from_text(text):
            # the first row is header names, comma-delimited with spaces
            df = pd.read_csv(io.StringIO(text), sep=",", skipinitialspace=True)
            if 'Lon' not in df.columns or 'Lat' not in df.columns:
                raise ValueError("HMS file missing expected Lon/Lat columns")
            # drop NA just in case
            df = df[['Lon', 'Lat']].dropna()
            if df.empty:
                return np.array([]), np.array([])
            return df['Lon'].to_numpy(dtype=float), df['Lat'].to_numpy(dtype=float)

        # Generate color palette from YlOrBr colormap
        # Normalize age to [0, 1] so most recent (age 1) is yellow, oldest is brown
        # Skip day 0 (today) and only show days 1-3
        cmap = YlOrBr
        age_color_map = {}
        for age_days in range(1, lookback_days + 1):
            # Inverse normalization: age 1 → 0.3 (yellow), age lookback_days → 1.0 (brown)
            norm_val = 0.3 + ((age_days - 1) / max(1, lookback_days - 1)) * 0.7
            rgba = cmap(norm_val)
            hex_color = '#{:02x}{:02x}{:02x}'.format(
                int(rgba[0] * 255), int(rgba[1] * 255), int(rgba[2] * 255)
            )
            age_color_map[age_days] = hex_color

        # Stratify fires by age (skip day 0 = today, only show 1-3 days ago)
        fire_by_age = {}
        for day_offset in range(1, lookback_days + 1):
            fire_by_age[day_offset] = []

        # Fetch HMS files going backward from reference date (skip today)
        for day_offset in range(1, lookback_days + 1):
            dt = pd.Timestamp(date) - pd.Timedelta(days=day_offset)
            url = _hms_url_for_date(dt)
            try:
                resp = requests.get(url, timeout=30)
            except requests.RequestException as exc:
                logger.warning("HMS fire points request failed for %s: %s", dt.date(), exc)
                continue

            if resp.status_code != 200:
                logger.info("No HMS fire point file for %s (HTTP %s)", dt.date(), resp.status_code)
                continue

            try:
                lons, lats = _load_points_from_text(resp.text)
            except Exception as exc:
                logger.warning("Could not parse HMS fire file for %s: %s", dt.date(), exc)
                continue

            if lons.size == 0:
                continue

            xs, ys = self.satpy_area_def.get_projection_coordinates_from_lonlat(lons, lats)
            x_min, y_min, x_max, y_max = self.satpy_area_def.area_extent
            in_aoi = (xs >= x_min) & (xs <= x_max) & (ys >= y_min) & (ys <= y_max)

            # Accumulate points for this age in the stratified dict
            fire_by_age[day_offset].extend(
                list(zip(xs[in_aoi].tolist(), ys[in_aoi].tolist()))
            )

        # Check if any fires were found
        total_fires = sum(len(pts) for pts in fire_by_age.values())
        if total_fires == 0:
            return None

        # Create per-age HoloViews Points objects
        x_min, y_min, x_max, y_max = self.satpy_area_def.area_extent
        overlays = []

        age_labels = {
            0: "Active Fire (Today)",
            1: "Active Fire (1 day ago)",
            2: "Active Fire (2 days ago)",
            3: "Active Fire (3 days ago)",
            4: "Active Fire (4+ days ago)",
        }

        for age_days in sorted(fire_by_age.keys()):
            points = fire_by_age[age_days]
            if not points:
                continue

            color = age_color_map.get(age_days, age_color_map[lookback_days - 1])
            label = age_labels.get(age_days, f"Active Fire ({age_days}d ago)")

            pts_obj = hv.Points(points, label=label).opts(
                color=color, marker='o', size=5, alpha=alpha,
                line_color='grey', line_width=0.5,
                xlim=(x_min, x_max), ylim=(y_min, y_max),
                
            )
            overlays.append(pts_obj)

        # Compose all age-stratified overlays into a single HoloViews Overlay
        if not overlays:
            return None

        combined = overlays[0]
        for overlay in overlays[1:]:
            combined = combined * overlay

        # Use legend click policy hide so deactivated lines disappear instead of becoming translucent)
        return combined.opts(click_policy='hide')

    def get_viirs_active_fire_overlay(self, date, lookback_days=3, buffer_days=0):
        """Backward-compatible alias to get_hms_active_fire_overlay."""
        return self.get_hms_active_fire_overlay(date, lookback_days=lookback_days, buffer_days=buffer_days)

    def prepare_data(self, date, resampler='nearest'):
        """Prepare data for a given date by downloading and preprocessing granules."""
        reflectance_granules = self.search_granules(date=date)
        cloud_mask_granules = self.search_cloud_mask_granules(date=date)
        self.download_granules(date=date,granule_search_results=reflectance_granules)
        self.download_cloud_mask_granules(date=date,cloud_mask_granule_search_results=cloud_mask_granules)
        self.preprocess_granules(date=date, resampler=resampler)

    def preprocess_granules(self, date, truecolor_from_worldview=False, resampler='nearest'):
        """Preprocess raw satellite granules for further analysis
        Performs the following operations:
        * Load specified bands from raw granules using Satpy
        * Load cloud mask granules using Satpy
        * Resample reflectance and cloud masks to the defined spatial area
        * Generate and save a true color preview image
        * Save loaded and reprojected bands to a new NetCDF file
          - Separate NetCDF file for cloud mask
        """
        if self.processing_status[date] == True:
            # Double-check that the processed NC actually exists and is non-empty
            # before trusting the status flag (a previous run may have crashed
            # after setting the flag but before writing valid data).
            _nc_check = os.path.join(
                self.processed_data_dir,
                f"{self.satellite_name}_{self.spatial_name}_{date}.nc"
            )
            _nc_ok = False
            if os.path.exists(_nc_check):
                try:
                    with xr.open_dataset(_nc_check) as _ds_chk:
                        _nc_ok = bool(_ds_chk.data_vars)
                except Exception:
                    pass
            if _nc_ok:
                print(f"Preprocessing already completed for this date. Skipping preprocessing step.")
                return
            else:
                logger.warning(
                    "processing_status is True for %s but NC is missing or "
                    "empty — re-running preprocessing.", date)
                self.processing_status[date] = False

        if self.download_status[date] != True:
            raise ValueError(f"Granules for date {date} have not been downloaded.")

        granule_files = self.raw_granules_by_date[date]
        cloud_mask_files = self.raw_cloud_mask_granules_by_date[date]

        nc_file = os.path.join(
            self.processed_data_dir,
            f"{self.satellite_name}_{self.spatial_name}_{date}.nc"
        )
        cloud_mask_nc_file = os.path.join(
            self.processed_data_dir,
            f"{self.satellite_name}_{self.spatial_name}_cloud_mask_{date}.nc"
        )
        png_name = f"{self.satellite_name.replace('-','')}_{self.instrument}_{self.spatial_name}_truecolor_{date}.png"
        truecolor_file = os.path.join(self.truecolor_img_dir, png_name)

        if granule_files == ["DOWNLOAD ERROR"]:
            print(f"Download error for date {date}. Cannot preprocess granules.")
            return
        if not granule_files:
            print(f"No granules available for date {date}. Skipping preprocessing.")
            return

        nc_file_exists = os.path.exists(nc_file)
        cloud_mask_nc_file_exists = os.path.exists(cloud_mask_nc_file)
        truecolor_file_exists = os.path.exists(truecolor_file)

        # Guard against empty/corrupt NC files left by a previous failed run.
        # If the file exists but has no data variables, treat it as absent so
        # it will be regenerated.
        if nc_file_exists:
            try:
                with xr.open_dataset(nc_file) as _ds_chk:
                    if not _ds_chk.data_vars:
                        logger.warning(
                            "Processed NC for %s has no data variables — "
                            "deleting and regenerating.", date)
                        os.remove(nc_file)
                        nc_file_exists = False
            except Exception:
                logger.warning(
                    "Could not open processed NC for %s — "
                    "deleting and regenerating.", date)
                os.remove(nc_file)
                nc_file_exists = False

        # ── Landsat HLS: use rioxarray — Satpy has no reader for HLSL30 GeoTIFFs ──
        if self.instrument == 'landsat':
            hls_load_bands = list(self.full_band_list)
            hls_ds = None
            if not nc_file_exists or not truecolor_file_exists:
                # Include B2 and B3 for truecolor generation if needed
                tc_extra = [b for b in ('B2', 'B3')
                            if not truecolor_file_exists and b not in hls_load_bands]
                all_bands = hls_load_bands + tc_extra
                print(f"Loading HLS bands {all_bands} using rioxarray "
                      f"(resampler='{resampler}')...")
                hls_ds = _load_hls_bands(granule_files, all_bands,
                                         self.satpy_area_def, resampler)

            if nc_file_exists:
                print(f"Processed NC already exists for {date}. Skipping save.")
                self.processed_granules_by_date[date] = nc_file
                self.update_processing_status(date, True)
            else:
                nc_vars = {b: hls_ds[b] for b in hls_load_bands if b in hls_ds}
                os.makedirs(self.processed_data_dir, exist_ok=True)
                xr.Dataset(nc_vars).to_netcdf(nc_file)
                print(f"Saved processed HLS bands to {nc_file}")
                self.processed_granules_by_date[date] = nc_file
                self.update_processing_status(date, True)

            if truecolor_file_exists:
                print(f"Truecolor image already exists for {date}. Skipping.")
                self.truecolor_images_by_date[date] = truecolor_file
            else:
                ok = _make_hls_truecolor_png(
                    hls_ds, truecolor_file,
                    county_shp=self.county_shp,
                    target_crs=self.satpy_area_def.crs)
                if ok:
                    self.truecolor_images_by_date[date] = truecolor_file
                else:
                    # Fallback: download a browse image from LPDAAC
                    self.retrieve_worldview_image(
                        date, out_path=truecolor_file, overwrite=False)

            if cloud_mask_nc_file_exists:
                print(f"Cloud mask NC already exists for {date}. Skipping.")
                self.processed_cloud_masks_by_date[date] = cloud_mask_nc_file
            else:
                if not cloud_mask_files:
                    print(f"No cloud mask files for {date}. Skipping.")
                else:
                    print(f"Loading HLS Fmask cloud mask for {date}...")
                    fmask_ds = _load_hls_bands(
                        cloud_mask_files, ['Fmask'],
                        self.satpy_area_def, resampler)
                    if 'Fmask' in fmask_ds:
                        fmask_ds.to_netcdf(cloud_mask_nc_file)
                        print(f"Saved HLS Fmask cloud mask to {cloud_mask_nc_file}")
                        self.processed_cloud_masks_by_date[date] = cloud_mask_nc_file
                    else:
                        print(f"Fmask band not found in HLS files for {date}.")
            self.update_processing_status(date, True)
            return  # skip the Satpy path below

        # If both the NC file and the truecolor preview already exist, then skip
        # loading the satpy scene. Otherwise need to load. Note that loading the
        # cloud mask granule occurs later
        if nc_file_exists & truecolor_file_exists:
            pass
        else:
            # Load bands from raw granules and reproject
            print(f"Loading and reprojecting granules to defined {self.spatial_name} region...")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # Make a copy so we never mutate self.full_band_list
                band_list = list(self.full_band_list)
                if truecolor_file_exists:
                    if "true_color" not in band_list:
                        band_list = band_list + ["true_color"]
                print(f"Loading bands: {band_list}")

                # Derive the correct Satpy reader name for this instrument.
                # VIIRS → viirs_l1b, MODIS → modis_l1b, Landsat HLS → hls_l30
                if self.instrument == 'landsat':
                    _reader = 'hls_l30'
                else:
                    _reader = f"{self.instrument}_l1b"

                scene_full = Scene(filenames=granule_files, reader=_reader)
                scene_full.load(band_list)
                print(f"Resampling to {self.spatial_name} region using '{resampler}' resampler (this may take a minute)...")
                scene_regional = scene_full.resample(self.satpy_area_def, resampler=resampler)
                scene_regional.load(band_list)
                print("Resampling complete.")

                if nc_file_exists:
                    print(f"Processed netcdf file already exists for {date}. Skipping save step.")
                    self.processed_granules_by_date[date] = nc_file
                    self.update_processing_status(date,True)
                else:
                    # Save loaded and reprojected bands to new NetCDF file
                    os.makedirs(self.processed_data_dir,exist_ok=True)
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore") # Ignore satpy warnings
                        scene_regional.save_datasets(
                            filename=nc_file,writer='cf'
                        )
                    self.processed_granules_by_date[date] = nc_file
                    self.update_processing_status(date,True)

                if truecolor_file_exists:
                    print(f"True color preview image already exists for {date}. Skipping generation step.")
                    self.truecolor_images_by_date[date] = truecolor_file
                else:
                    if truecolor_from_worldview:
                        self.retrieve_worldview_image(date,out_path=truecolor_file,overwrite=False)
                    else:
                        # Generate true color image with county overlay and save to disk
                        print(f"Generating true color image preview")
                        self.generate_truecolor_image(date,scene_regional,out_path=truecolor_file,overwrite=False)

        if cloud_mask_nc_file_exists:
            print(f"Processed cloud mask netcdf file already exists for {date}. Skipping save step.")
            self.processed_cloud_masks_by_date[date] = cloud_mask_nc_file
        else:
            if not cloud_mask_files:
                print(f"No cloud mask granules available for date {date}. Skipping cloud mask step.")
                self.update_processing_status(date, True)
            else:
                # Load bands from raw granules and reproject
                print(f"Loading and reprojecting cloud mask to defined {self.spatial_name} region...")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    print(f"Loading cloud mask granules for {date}")
                    # Landsat HLS: cloud mask is Fmask band in the same hls_l30
                    # product; VIIRS/MODIS use a separate L2 cloud mask product.
                    if self.instrument == 'landsat':
                        _cld_reader = 'hls_l30'
                        _cld_band = 'Fmask'
                    else:
                        _cld_reader = f"{self.instrument}_l2"
                        _cld_band = 'Clear_Sky_Confidence'
                    cloud_mask_scene_full = Scene(filenames=cloud_mask_files, reader=_cld_reader)
                    cloud_mask_scene_full.load([_cld_band])
                    print(f"Resampling cloud mask using '{resampler}' resampler...")
                    cloud_mask_scene_regional = cloud_mask_scene_full.resample(self.satpy_area_def, resampler=resampler)
                    cloud_mask_scene_regional.load([_cld_band])
                    cloud_mask_scene_regional.save_datasets(
                        filename=cloud_mask_nc_file, writer='cf'
                    )
                    self.processed_cloud_masks_by_date[date] = cloud_mask_nc_file
                    self.update_processing_status(date, True)
        return

    def resample_landmask(self,landcover_mask_file_fullres,landcover_mask_file,flip_single_pixels=True):
        """Resample NLCD Land Mask to the local spatial domain using nearest-neighbor"""
        import rasterio
        import rioxarray as rxr
        from pyresample import image, geometry
        from pyresample.kd_tree import XArrayResamplerNN
        from rasterio.transform import Affine
        from fhba.process_landcover_mask import get_nlcd_area_definition, flip_singletons

        nlcd_mask = rxr.open_rasterio(landcover_mask_file_fullres)
        area_nlcd = get_nlcd_area_definition(nlcd_mask)

        resampler = XArrayResamplerNN(
            source_geo_def=area_nlcd,
            target_geo_def=self.satpy_area_def,
            radius_of_influence=90
        )
        # The following line appears to fix a bug within pyresample. Otherwise the get_sample_from_neighbour_info
        # function call fails...
        resampler.index_array = resampler.get_neighbour_info()[2]
        nlcd_mask_resampled = resampler.get_sample_from_neighbour_info(
            data=nlcd_mask.isel(band=0),
            fill_value=nlcd_mask._FillValue
        )

        extent = self.satpy_area_def.area_extent
        dx, dy = self.satpy_area_def.resolution
        # x and y coords of upper left corner
        x0 = extent[0]
        y0 = extent[3]
        geotransform = [dx,0.0,x0,0.0,-dy,y0]
        geotransform = [float(x) for x in geotransform]
        geotransform = Affine(*geotransform)

        with rasterio.open(
            landcover_mask_file,
            'w',
            driver='GTiff',
            height=1000,
            width=500,
            count=1,
            dtype='int8',
            crs=self.satpy_area_def.crs.to_proj4(),
            transform=geotransform
        ) as dst:
            nlcd_mask_values = nlcd_mask_resampled.values
            if flip_single_pixels:
                nlcd_mask_values = flip_singletons(nlcd_mask_values,diagonals=False)
            dst.write(nlcd_mask_values,1)
        return

    def update_download_status(self,date,status):
        """Update the download status for a given date."""
        self.download_status[date] = status

    def update_qc_status(self,date,status):
        """Update the quality control status for a given date."""
        self.qc_status[date] = status

    def update_processing_status(self,date,status):
        """Update the processing status for a given date."""
        self.processing_status[date] = status

    def update_analysis_status(self,date,status):
        """Update the analysis status for a given date."""
        self.analysis_status[date] = status

    def update_categorization_status(self,date,status):
        """Update the categorization status for a given date."""
        self.categorization_status[date] = status

    def update_user_categorization(self,date,category):
        """Update the user categorization for a given date."""
        if category not in ["Fully Cloudy", "Mostly Cloudy", "Mostly Clear", "Fully Clear", "Uncategorized", "Unfilled"]:
            raise ValueError(f"Category {category} not recognized. Valid options are 'Fully Cloudy', 'Mostly Cloudy', 'Mostly Clear', 'Fully Clear', or 'Uncategorized'.")
        self.user_categorization_by_date[date] = category

    # Mapping from internal instrument name to the CMR instrument short name.
    # HLS (Landsat) data uses 'OLI' in CMR regardless of L8 vs L9; the generic
    # 'landsat' instrument name used internally is not a valid CMR value.
    _CMR_INSTRUMENT = {
        'viirs': 'VIIRS',
        'modis': 'MODIS',
        'landsat': 'OLI',
    }

    def _cmr_search_kwargs(self, day_night_flag='day'):
        """Return the CMR keyword arguments appropriate for this instrument.
        HLS (Landsat) granules are not tagged with a DayNightFlag in CMR, so
        filtering by that field returns no results. The CMR instrument name
        for HLS is 'OLI', not 'LANDSAT'.
        """
        kwargs = dict(
            platform=self.satellite_name.upper(),
            instrument=self._CMR_INSTRUMENT.get(self.instrument, self.instrument.upper()),
        )
        # Only VIIRS and MODIS granules carry a DayNightFlag in CMR.
        if self.instrument in ('viirs', 'modis'):
            kwargs['day_night_flag'] = day_night_flag
        return kwargs

    def search_granules(self, date, day_night_flag='day'):
        """Search for granules for the satellite within the specified temporal and spatial bounds."""
        if date not in self.download_status:
            raise ValueError(f"Date {date} is outside the defined date range for this GranuleManager of {self.start_date} to {self.end_date}.")
        if self.download_status[date] == True:
            print(f"Granules for date {date} have already been downloaded. Skipping search step.")
            return None

        logger.info("Beginning search for granules...")
        granule_search_results = earthaccess.search_data(
            short_name=self.short_name_list,
            bounding_box=tuple(self.spatial),
            temporal=(date, date),
            **self._cmr_search_kwargs(day_night_flag),
        )
        logger.info("Found %d granules for %s %s on %s.",
                    len(granule_search_results), self.satellite_name, self.instrument.upper(), date)
        return granule_search_results

    def search_cloud_mask_granules(self, date, day_night_flag='day'):
        """Search for cloud mask granules for the satellite within the specified temporal and spatial bounds."""
        if date not in self.download_status:
            raise ValueError(f"Date {date} is outside the defined date range for this GranuleManager of {self.start_date} to {self.end_date}.")
        if self.cloud_mask_download_status[date] == True:
            print(f"Granules for date {date} have already been downloaded. Skipping search step.")
            return None

        logger.info("Beginning search for cloud mask granules...")
        granule_search_results = earthaccess.search_data(
            short_name=self.cloud_mask_short_name,
            bounding_box=tuple(self.spatial),
            temporal=(date, date),
            **self._cmr_search_kwargs(day_night_flag),
        )
        logger.info("Found %d cloud mask granules for %s %s on %s.",
                    len(granule_search_results), self.satellite_name, self.instrument.upper(), date)
        return granule_search_results

    def download_granules(self,granule_search_results=None,date=None,day_night_flag='day',outdir=None,clobber=False,return_granules=False):
        """Download granules for the satellite within the specified temporal and spatial bounds."""
        if granule_search_results is None and date is None:
            raise ValueError("Either granule_search_results or date must be provided to download granules.")
        if granule_search_results is None:
            # Will return None if granules already downloaded to avoid redundant searches
            granule_search_results = self.search_granules(date,day_night_flag=day_night_flag)
            if granule_search_results is None:
                print("Granules already downloaded. Skipping download step.")
                self.download_status[date] = True
                return None

        if len(granule_search_results) == 0:
            logger.info("No granules found for %s %s on %s. Skipping download.",
                        self.satellite_name, self.instrument.upper(), date)
            if date is not None:
                self.raw_granules_by_date[date] = []
                self.download_status[date] = True
            return None

        logger.info("Downloading %d granules for %s %s.",
                    len(granule_search_results), self.satellite_name, self.instrument.upper())
        try:
            granule_files = self._download_with_retry(
                granule_search_results,
                local_path=self.raw_data_dir if outdir is None else outdir
            )
            # Convert from Path objects to strings for JSON serialization
            if date is not None:
                self.raw_granules_by_date[date] = [str(f) for f in granule_files]
                self.download_status[date] = True
                logger.info("Download complete. Filenames added to Registry.")
                if return_granules:
                    return self.raw_granules_by_date[date]
            else:
                logger.info("Download complete. Filenames not added to Registry since date was not provided.")
                if return_granules:
                    return [str(f) for f in granule_files]
        except earthaccess.exceptions.DownloadFailure:
            logger.error("Download failed after all retries. Marking date %s as download error.", date)
            self.raw_granules_by_date[date] = ["DOWNLOAD ERROR"]

    def download_cloud_mask_granules(self, cloud_mask_granule_search_results=None, date=None,
                                     day_night_flag='day', outdir=None, clobber=False,
                                     return_granules=False):
        if cloud_mask_granule_search_results is None and date is None:
            raise ValueError("Either granule_search_results or date must be provided to download granules.")
        if cloud_mask_granule_search_results is None:
            # Will return None if granules already downloaded to avoid redundant searches
            cloud_mask_granule_search_results = self.search_cloud_mask_granules(date,day_night_flag=day_night_flag)
            if cloud_mask_granule_search_results is None:
                print("Cloud mask granules already downloaded. Skipping download step.")
                return None

        if len(cloud_mask_granule_search_results) == 0:
            logger.info("No cloud mask granules found for %s %s on %s.",
                        self.satellite_name, self.instrument.upper(), date)
            if date is not None:
                self.raw_cloud_mask_granules_by_date[date] = []
                self.cloud_mask_download_status[date] = True
            return None

        logger.info("Downloading %d cloud mask granules for %s %s.",
                    len(cloud_mask_granule_search_results), self.satellite_name, self.instrument.upper())
        try:
            granule_files = self._download_with_retry(
                cloud_mask_granule_search_results,
                local_path=self.raw_data_dir if outdir is None else outdir
            )
            # Convert from Path objects to strings for JSON serialization
            if date is not None:
                self.raw_cloud_mask_granules_by_date[date] = [str(f) for f in granule_files]
                self.cloud_mask_download_status[date] = True
                logger.info("Download complete. Cloud mask filenames added to Registry.")
                if return_granules:
                    return self.raw_cloud_mask_granules_by_date[date]
            else:
                logger.info("Download complete. Filenames not added to Registry since date was not provided.")
                if return_granules:
                    return [str(f) for f in granule_files]
        except earthaccess.exceptions.DownloadFailure:
            logger.error("Cloud mask download failed after all retries. Marking date %s as error.", date)
            self.raw_cloud_mask_granules_by_date[date] = ["DOWNLOAD ERROR"]

    def _download_with_retry(self, granule_search_results, local_path,
                             max_retries=3, backoff_factor=2.0):
        """Download granules with exponential-backoff retry on failure.
        Parameters
        ----------
        granule_search_results : list
            earthaccess granule search result list.
        local_path : str
            Destination directory for downloaded files.
        max_retries : int, default 3
        backoff_factor : float, default 2.0
            Each retry waits ``backoff_factor ** attempt`` seconds.
        Returns
        -------
        list of pathlib.Path
            Downloaded file paths.
        Raises
        ------
        earthaccess.exceptions.DownloadFailure
            After all retries are exhausted.
        """
        import glob as _glob
        os.makedirs(local_path, exist_ok=True)
        if not granule_search_results:
            logger.info("No granules to download (empty search result). Returning empty list.")
            return []

        last_exc = None
        for attempt in range(max_retries):
            try:
                files = earthaccess.download(granule_search_results, local_path=local_path)
                return files
            except earthaccess.exceptions.DownloadFailure as exc:
                last_exc = exc
                wait = backoff_factor ** attempt
                logger.warning(
                    "Download attempt %d/%d failed: %s. Retrying in %.1fs ...",
                    attempt + 1, max_retries, exc, wait)
                # Remove any partial files before retrying
                for f in _glob.glob(os.path.join(local_path, "*.PARTIAL")):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                time.sleep(wait)
        raise last_exc

    def download_granules_date_range(self, day_night_flag='day', outdir=None, clobber=False):
        """Download and preprocess granules for every date in the defined date range.
        Parameters
        ----------
        day_night_flag : str, default 'day'
        outdir : str, optional
        Override destination directory.
        clobber : bool, default False
        If True, re-download dates that are already marked as downloaded.
        """
        date_range = pd.date_range(
            start=self.start_date, end=self.end_date, freq='D'
        ).strftime("%Y-%m-%d").tolist()
        for date in date_range:
            if not clobber and self.download_status.get(date) is True:
                logger.info("Skipping %s — already downloaded.", date)
                continue
            try:
                logger.info("Processing date %s ...", date)
                self.prepare_data(date)
            except Exception as exc:
                logger.error("Error processing date %s: %s", date, exc)

    def get_county_overlay(self,color='white',line_width=0.75):
        county_gdf = gpd.read_file(self.county_shp + ".shp")
        county_gdf = county_gdf.to_crs(self.satpy_area_def.to_cartopy_crs())
        county_overlay = hv.Path([sh.boundary.xy for sh in county_gdf.geometry]).opts(
            color=color, line_width=line_width
        )
        return county_overlay

    def generate_truecolor_image(self,date,scene_regional,out_path,overwrite=False):
        granules = self.raw_granules_by_date[date]
        if granules == ["DOWNLOAD ERROR"]:
            print(f"Download error for date {date}. Cannot generate true color image.")
            return
        if os.path.exists(out_path) and not overwrite:
            print("True color image already exists. Skipping generation but adding file to Registry.")
            self.truecolor_images_by_date[date] = out_path
        else:
            print(f"Generating true color image for {date}")
            os.makedirs(self.truecolor_img_dir, exist_ok=True)
            print("Generating true color image with county overlay...")
            img = scene_regional.show('true_color')
            img = overlays.add_overlay(
                img,
                area=scene_regional['true_color'].area,
                coast_dir=None,
                overlays={
                    'shapefiles': {
                        'filename' : self.county_shp,
                        "outline": (255, 255, 255, 255),
                        "fill": None,
                        "width": 0.75,
                    }}
            )
            img.save(out_path)
            self.truecolor_images_by_date[date] = out_path
        return

    def retrieve_worldview_image(self, date, out_path, overwrite=False, truecolor=True):
        """Download a true-color (or composite) preview image for *date*.
        * VIIRS (Suomi-NPP, NOAA-20, NOAA-21): fetched via the NASA Worldview
          Snapshot API (wvs.earthdata.nasa.gov).
        * MODIS (AQUA/TERRA): fetched via the same Worldview Snapshot API
          using CorrectedReflectance layers.
        * Landsat-8 / Landsat-9: Worldview Snapshot API does not carry daily
          Landsat imagery; browse thumbnails are retrieved directly from the
          publicly-accessible LPDAAC S3 bucket via a CMR granule search.
        """
        if os.path.exists(out_path) and not overwrite:
            logger.info("True color image already exists for %s. Skipping retrieval.", date)
            self.truecolor_images_by_date[date] = out_path
            return

        # ── VIIRS via NASA Worldview Snapshot API ──────────────────────────────
        _viirs_layer = {
            'Suomi-NPP': 'VIIRS_SNPP',
            'NOAA-20':  'VIIRS_NOAA20',
            'NOAA-21':  'VIIRS_NOAA21',
        }
        # NEW: MODIS layer prefixes (Fix A)
        _modis_layer = {
            'AQUA':  'MODIS_Aqua',
            'TERRA': 'MODIS_Terra',
        }

        # Use the AOI bounds defined in the registry (same pattern as original VIIRS)
        bbox = f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"

        if self.satellite_name in _viirs_layer:
            product = "TrueColor" if truecolor else "BandsM11-I2-I1"
            layer = f"{_viirs_layer[self.satellite_name]}_CorrectedReflectance_{product}"
            url = (
                "https://wvs.earthdata.nasa.gov/api/v1/snapshot"
                f"?REQUEST=GetSnapshot&TIME={date}T00:00:00Z&BBOX={bbox}&CRS=EPSG:4326"
                f"&LAYERS={layer},Coastlines_15m&WRAP=day,x&FORMAT=image/jpeg"
                "&WIDTH=1138&HEIGHT=1820&colormaps="
            )
            logger.debug("Worldview URL: %s", url)
            response = requests.get(url)

            if response.status_code == 200:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, 'wb') as f:
                    f.write(response.content)
                self.truecolor_images_by_date[date] = out_path
                logger.info("True color image retrieved and saved to %s", out_path)
            else:
                logger.warning(
                    "Failed to retrieve Worldview image for %s on %s. HTTP %d",
                    self.satellite_name, date, response.status_code)

        # ── MODIS (AQUA/TERRA) via NASA Worldview Snapshot API ────────────────
        elif self.satellite_name in _modis_layer:
            # TrueColor or 7-2-1 composite (common MODIS browse composite)
            product = "TrueColor" if truecolor else "Bands7-2-1"
            layer = f"{_modis_layer[self.satellite_name]}_CorrectedReflectance_{product}"
            url = (
                "https://wvs.earthdata.nasa.gov/api/v1/snapshot"
                f"?REQUEST=GetSnapshot&TIME={date}T00:00:00Z&BBOX={bbox}&CRS=EPSG:4326"
                f"&LAYERS={layer},Coastlines_15m&WRAP=day,x&FORMAT=image/jpeg"
                "&WIDTH=1138&HEIGHT=1820&colormaps="
            )
            logger.debug("Worldview MODIS URL: %s", url)
            response = requests.get(url)

            if response.status_code == 200:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, 'wb') as f:
                    f.write(response.content)
                self.truecolor_images_by_date[date] = out_path
                logger.info("MODIS preview image retrieved and saved to %s", out_path)
            else:
                logger.warning(
                    "Failed to retrieve MODIS Worldview image for %s on %s. HTTP %d",
                    self.satellite_name, date, response.status_code)

        # ── Landsat 8 / 9 via LPDAAC HLS browse images ────────────────────────
        elif self.satellite_name in ('Landsat-8', 'Landsat-9'):
            # The Worldview Snapshot API does not provide daily Landsat imagery.
            # Instead, retrieve the publicly-hosted browse JPEG for the HLS L30
            # (Harmonized Landsat Sentinel-2) granule that covers this area.
            short_name = 'HLSL30'
            # CMR requires an explicit time range — using just "date,date" misses
            # granules acquired later in the day. Use a 24-hour window instead.
            date_end = (datetime.strptime(date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            cmr_url = (
                f"https://cmr.earthdata.nasa.gov/search/granules.json"
                f"?short_name={short_name}"
                f"&temporal={date}T00:00:00Z,{date_end}T00:00:00Z"
                f"&bounding_box={self.min_lon},{self.min_lat},{self.max_lon},{self.max_lat}"
                f"&page_size=30"
            )
            logger.debug("CMR HLS search URL: %s", cmr_url)
            try:
                cmr_resp = requests.get(cmr_url, timeout=30)
            except Exception as exc:
                logger.warning("CMR search request failed for %s on %s: %s", short_name, date, exc)
                return

            if cmr_resp.status_code != 200:
                logger.warning(
                    "CMR search returned HTTP %d for %s on %s.",
                    cmr_resp.status_code, short_name, date)
                return

            entries = cmr_resp.json().get('feed', {}).get('entry', [])
            if not entries:
                logger.info("No HLS L30 granules found for %s on %s.", short_name, date)
                return

            # Select the tile that best covers the area of interest.
            # Score = polygon_area / (1 + distance²) so that full tiles near
            # the AOI centre are preferred over partial edge-of-swath tiles.
            center_lat = (self.min_lat + self.max_lat) / 2.0
            center_lon = (self.min_lon + self.max_lon) / 2.0

            def _tile_score(entry):
                """Higher is better: full tiles close to the AOI centre win."""
                polygons = entry.get('polygons', [])
                if not polygons or not polygons[0]:
                    return -1.0
                coords = list(map(float, polygons[0][0].split()))
                lats = coords[0::2]
                lons = coords[1::2]
                clat = sum(lats) / len(lats)
                clon = sum(lons) / len(lons)
                # Approximate bounding-box area of the tile polygon
                lat_span = max(lats) - min(lats)
                lon_span = max(lons) - min(lons)
                area = lat_span * lon_span
                dist_sq = (clat - center_lat) ** 2 + (clon - center_lon) ** 2
                return area / (1.0 + dist_sq)

            entries_sorted = sorted(entries, key=_tile_score, reverse=True)

            # Pick the public HTTPS browse link for the best-scoring tile
            browse_url = None
            for entry in entries_sorted:
                for link in entry.get('links', []):
                    href = link.get('href', '')
                    if (href.startswith('https://') and
                        href.endswith('.jpg') and
                        'lp-prod-public' in href):
                        browse_url = href
                        logger.debug(
                            "Selected HLS tile %s (best area/distance score)", entry['title'])
                        break
                if browse_url:
                    break

            if not browse_url:
                logger.warning("No public browse image found for HLS L30 granules on %s.", date)
                return

            logger.debug("HLS browse URL: %s", browse_url)
            try:
                img_resp = requests.get(browse_url, timeout=30)
            except Exception as exc:
                logger.warning("Browse image download failed for %s on %s: %s", short_name, date, exc)
                return

            if img_resp.status_code == 200:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, 'wb') as f:
                    f.write(img_resp.content)
                self.truecolor_images_by_date[date] = out_path
                logger.info("HLS browse image saved to %s", out_path)
            else:
                logger.warning(
                    "Failed to download HLS browse image for %s on %s. HTTP %d",
                    self.satellite_name, date, img_resp.status_code)
        else:
            logger.warning(
                "retrieve_worldview_image: satellite '%s' is not supported. "
                "Skipping preview download for %s.",
                self.satellite_name, date)
            return
        return

    def get_nir_red_mwir_hv_rgb(self,date,in_app=False,include_counties=True,no_title=False):
        """Generate false-color composite image from VIIRS bands M11-I02-I01"""
        if date is None:
            return None
        if date not in self.processed_granules_by_date:
            msg = f"No preprocessed granules found for date {date}."
            if in_app:
                return msg
            else:
                raise ValueError(msg)

        ds = xr.open_dataset(self.processed_granules_by_date[date])
        red = ds[self.nir_red_mwir_band_list[0]].load()
        nir = ds[self.nir_red_mwir_band_list[1]].load()
        mwir = ds[self.nir_red_mwir_band_list[2]].load()
        r = nonlinear_enhancement(255 * mwir.values / 100) / 255
        g = nonlinear_enhancement(255 * nir.values / 100) / 255
        b = nonlinear_enhancement(255 * red.values / 100) / 255
        img = xr.concat(
            [xr.DataArray(c,dims=red.dims,coords=red.coords)for c in [r,g,b]],
            dim="band"
        ).transpose(...,"band")
        rgb = hv.RGB((ds.x,ds.y,img.values)).opts(
            width=500,
            height=1000,
            title=f"{self.satellite_name} {self.instrument.upper()} NIR-Red Composite for {date}" if not no_title else "",
        )
        if include_counties:
            county_overlay = self.get_county_overlay()
            rgb = rgb * county_overlay
        return rgb

    def get_nir_red_hv_rgb(self,date,in_app=False,include_counties=True,no_title=False):
        if date is None:
            return None
        if date not in self.processed_granules_by_date:
            msg = f"No preprocessed granules found for date {date}."
            if in_app:
                return msg
            else:
                raise ValueError(msg)

        ds = xr.open_dataset(self.processed_granules_by_date[date])
        # nir_red_band_list[0] = NIR band, nir_red_band_list[1] = RED band (fixed in config)
        nir = ds[self.nir_red_band_list[0]].load()
        red = ds[self.nir_red_band_list[1]].load()
        r = nonlinear_enhancement(255 * nir.values / 100) / 255
        g = nonlinear_enhancement(255 * red.values / 100) / 255
        b = np.sqrt(r * g)
        img = xr.concat(
            [xr.DataArray(c,dims=red.dims,coords=red.coords)for c in [r,g,b]],
            dim="band"
        ).transpose(...,"band")
        rgb = hv.RGB((ds.x,ds.y,img.values)).opts(
            width=500,
            height=1000,
            title=f"{self.satellite_name} {self.instrument.upper()} NIR-Red Composite for {date}" if not no_title else "",
        )
        if include_counties:
            county_overlay = self.get_county_overlay()
            rgb = rgb * county_overlay
        return rgb

    def get_burnmask_hv_rgb(self,burnmask_array=None,burnmask_file=None,include_counties=True):
        if burnmask_array is None and burnmask_file is None:
            raise ValueError("Either burnmask_array or burnmask_file must be provided to get burnmask hv QuadMesh.")
        if burnmask_array is None:
            raise NotImplementedError("Loading burnmask from file not yet implemented.")

        # RGB appears to be much faster than QuadMesh, render burnmask as a white/orange image
        rgb = [213,94,0]
        da = 255 * (1-burnmask_array.values).astype(int)
        burnmask_qm = hv.RGB((
            burnmask_array.x,
            burnmask_array.y,
            np.clip(da+rgb[0],0,255),
            np.clip(da+rgb[1],0,255),
            np.clip(da+rgb[2],0,255),
        )).opts(
            width=500,
            height=1000,
        )
        if include_counties:
            county_overlay = self.get_county_overlay(color='k')
            burnmask_qm = burnmask_qm * county_overlay
        return burnmask_qm

    def review_file_status(self):
        raise NotImplementedError("Method review_file_status not yet implemented.")

    def to_dict(self):
        """Convert the granule manager to a dictionary representation."""
        dict_repr = {k:v for k,v in inspect.getmembers(self) if not k.startswith('_') and not inspect.ismethod(v)}
        if 'satpy_area_def' in dict_repr.keys():
            dict_repr['satpy_area_def'] = str(dict_repr['satpy_area_def'])
        return dict_repr

    def from_dict(self, data):
        for k in data:
            setattr(self, k, data[k])
        return self

    def __str__(self):
        class_str = f"GranuleManager for {self.satellite_name} {self.instrument.upper()}\n > Product short names: {self.short_name_list}"
        return class_str

    def to_df(self):
        df = pd.DataFrame({'download_status': self.download_status})
        df['processing_status'] = self.processing_status
        df['user_categorization'] = self.user_categorization_by_date
        df['analysis_status'] = self.analysis_status
        df['categorization_status'] = self.categorization_status
        df['truecolor_images_by_date'] = self.truecolor_images_by_date
        return df


class GranuleRegistry:
    """"""
    def __init__(self, data_year=None, start_month=None, start_day=None, end_month=None,
                 end_day=None, raw_data_dir=None, processed_data_dir=None,
                 truecolor_img_dir=None, min_lat=None, min_lon=None, max_lat=None,
                 max_lon=None, spatial_name=None, viirs_short_names=None,
                 viirs_band_list=None, viirs_nir_red_band_list=None,
                 viirs_nbr_bands=None, viirs_ndvi_bands=None,
                 modis_short_names=None, modis_band_list=None,
                 modis_nir_red_band_list=None, modis_nbr_bands=None, modis_ndvi_bands=None,
                 modis_cloud_mask_short_names=None,
                 landsat_short_names=None, landsat_band_list=None,
                 landsat_nir_red_band_list=None, landsat_nbr_bands=None,
                 landsat_ndvi_bands=None, landsat_cloud_mask_short_names=None,
                 satpy_area_def=None, county_shp=None, supported_instruments=None,
                 userpts_dir=None, viirs_cloud_mask_short_names=None, burnmask_dir=None):

        self.data_year = data_year
        self.start_month = start_month
        self.start_day = start_day
        self.end_month = end_month
        self.end_day = end_day

        self.raw_data_dir = raw_data_dir
        self.processed_data_dir = processed_data_dir
        self.truecolor_img_dir = truecolor_img_dir
        self.userpts_dir = userpts_dir
        self.burnmask_dir = burnmask_dir
        self.county_shp = county_shp

        self.min_lat = min_lat
        self.min_lon = min_lon
        self.max_lat = max_lat
        self.max_lon = max_lon
        self.spatial_name = spatial_name

        self.viirs_short_names = viirs_short_names
        self.viirs_band_list = viirs_band_list
        self.viirs_nir_red_band_list = viirs_nir_red_band_list
        self.viirs_nbr_bands = viirs_nbr_bands
        self.viirs_ndvi_bands = viirs_ndvi_bands
        self.viirs_cloud_mask_short_names = viirs_cloud_mask_short_names

        self.modis_short_names = modis_short_names
        self.modis_band_list = modis_band_list
        self.modis_nir_red_band_list = modis_nir_red_band_list
        self.modis_nbr_bands = modis_nbr_bands
        self.modis_ndvi_bands = modis_ndvi_bands
        self.modis_cloud_mask_short_names = modis_cloud_mask_short_names

        self.landsat_short_names = landsat_short_names
        self.landsat_band_list = landsat_band_list
        self.landsat_nir_red_band_list = landsat_nir_red_band_list
        self.landsat_nbr_bands = landsat_nbr_bands
        self.landsat_ndvi_bands = landsat_ndvi_bands
        self.landsat_cloud_mask_short_names = landsat_cloud_mask_short_names

        self.satpy_area_def = satpy_area_def
        self.supported_instruments = supported_instruments if supported_instruments is not None else []

        self.satellites = {}

    def add_satellite(self, satellite_name):
        """Add a satellite to the registry."""
        if satellite_name not in self.satellites:
            if satellite_name not in self.supported_instruments:
                raise ValueError(f"Satellite {satellite_name} not recognized. Valid options are {self.supported_instruments}.")

            if self.viirs_short_names and satellite_name in self.viirs_short_names:
                instrument = 'viirs'
                short_name_list = self.viirs_short_names[satellite_name]
                full_band_list = self.viirs_band_list
                nir_red_band_list = self.viirs_nir_red_band_list
                nbr_bands = self.viirs_nbr_bands
                ndvi_bands = self.viirs_ndvi_bands
                cloud_mask_short_name = self.viirs_cloud_mask_short_names[satellite_name]
            elif self.landsat_short_names and satellite_name in self.landsat_short_names:
                instrument = 'landsat'
                short_name_list = self.landsat_short_names[satellite_name]
                full_band_list = self.landsat_band_list
                nir_red_band_list = self.landsat_nir_red_band_list
                nbr_bands = self.landsat_nbr_bands
                ndvi_bands = self.landsat_ndvi_bands
                cloud_mask_short_name = (self.landsat_cloud_mask_short_names or {}).get(satellite_name)
            else:
                instrument = 'modis'
                short_name_list = self.modis_short_names[satellite_name]
                full_band_list = self.modis_band_list
                nir_red_band_list = self.modis_nir_red_band_list
                nbr_bands = self.modis_nbr_bands
                ndvi_bands = self.modis_ndvi_bands
                cloud_mask_short_name = (self.modis_cloud_mask_short_names or {}).get(satellite_name)

            self.satellites[satellite_name] = GranuleManager(
                satellite_name,
                short_name_list=short_name_list,
                instrument=instrument,
                start_date=f"{self.data_year}-{self.start_month:02d}-{self.start_day:02d}",
                end_date=f"{self.data_year}-{self.end_month:02d}-{self.end_day:02d}",
                raw_data_dir=self.raw_data_dir + "/" + satellite_name,
                processed_data_dir=self.processed_data_dir + "/" + satellite_name,
                truecolor_img_dir=self.truecolor_img_dir + "/" + satellite_name,
                userpts_dir=self.userpts_dir + "/" + satellite_name,
                burnmask_dir=self.burnmask_dir + "/" + satellite_name,
                full_band_list=full_band_list,
                cloud_mask_short_name=cloud_mask_short_name,
                nir_red_band_list=nir_red_band_list,
                nbr_bands=nbr_bands,
                ndvi_bands=ndvi_bands,
                min_lat=self.min_lat,
                min_lon=self.min_lon,
                max_lat=self.max_lat,
                max_lon=self.max_lon,
                spatial_name=self.spatial_name,
                satpy_area_def=self.satpy_area_def,
                county_shp=self.county_shp
            )
        else:
            print(f"Satellite {satellite_name} already exists in the registry.")

    def review_file_status(self):
        """"""
        for satellite in self.satellites:
            self.satellites[satellite].review_file_status()

    def to_dict(self):
        """Convert the granule registry to a dictionary representation."""
        dict_repr = {k:v for k,v in inspect.getmembers(self) if not k.startswith('_') and not inspect.ismethod(v) and not k=='satellites'}
        dict_repr['satellites'] = {sat: self.satellites[sat].to_dict() for sat in self.satellites}
        if 'satpy_area_def' in dict_repr.keys():
            dict_repr['satpy_area_def'] = str(dict_repr['satpy_area_def'])
        return dict_repr

    def from_dict(self, data):
        for k in data:
            if k != 'satellites':
                setattr(self, k, data[k])
        self.satellites = {sat: GranuleManager().from_dict(data['satellites'][sat]) for sat in data['satellites']}

        # Guard: recover empty full_band_list from current config
        _band_list_by_instrument = {
            'viirs': getattr(self, 'viirs_full_band_list', None) or getattr(self, 'viirs_band_list', None) or [],
            'modis': getattr(self, 'modis_full_band_list', None) or getattr(self, 'modis_band_list', None) or [],
            'landsat': getattr(self, 'landsat_full_band_list', None) or getattr(self, 'landsat_band_list', None) or [],
        }
        for gm in self.satellites.values():
            if not getattr(gm, 'full_band_list', None):
                instr = getattr(gm, 'instrument', '')
                recovered = _band_list_by_instrument.get(instr, [])
                if recovered:
                    logger.warning(
                        "GranuleManager for %s had empty full_band_list; "
                        "recovering from registry config: %s",
                        getattr(gm, 'satellite_name', '?'), recovered)
                    gm.full_band_list = recovered
        return self

    def __getitem__(self, satellite_name):
        """Allow access to satellite granule managers using indexing syntax."""
        return self.satellites.get(satellite_name, None)

    def __str__(self):
        disp = lambda x: f"{x} {self.satellites[x].instrument.upper()}"
        class_str = f"GranuleRegistry for {self.data_year}"
        class_str += f"\n > Date Bounds: {self.start_month}/{self.start_day} to {self.end_month}/{self.end_day}"
        class_str += f"\n > Satellites: {list(map(disp, self.satellites.keys()))}"
        return class_str


class Registry:
    def __init__(self,get_satpy_area_def=True,auth_earthaccess=True):
        self.granule_registry = {}
        self.read_config()
        if get_satpy_area_def:
            self.define_satpy_area_def()
        if auth_earthaccess:
            try:
                earthaccess.login(persist=True)
                print("Registry authenticated with Earthaccess...")
            except Exception as e:
                msg = "Error authenticating with Earthaccess."
                raise ValueError(msg) from e

    def add_granule_registry(self, data_year):
        """Add a granule registry for a specific data year."""
        if data_year not in self.granule_registry:
            self.__setitem__(data_year, GranuleRegistry(
                data_year=data_year,
                start_month=self.start_month,
                start_day=self.start_day,
                end_month=self.end_month,
                end_day=self.end_day,
                raw_data_dir=self.raw_data_dir + "/" + str(data_year),
                processed_data_dir=self.processed_data_dir + "/" + str(data_year),
                truecolor_img_dir=self.truecolor_img_dir + "/" + str(data_year),
                userpts_dir=self.userpts_dir + "/" + str(data_year),
                burnmask_dir=self.burnmask_dir + "/" + str(data_year),
                county_shp=self.county_shp,
                min_lat=self.min_lat,
                min_lon=self.min_lon,
                max_lat=self.max_lat,
                max_lon=self.max_lon,
                spatial_name=self.spatial_name,
                viirs_band_list=self.viirs_full_band_list,
                viirs_nir_red_band_list=self.viirs_nir_red_band_list,
                viirs_nbr_bands=self.viirs_nbr_bands,
                viirs_ndvi_bands=self.viirs_ndvi_bands,
                viirs_short_names=self.viirs_short_names,
                viirs_cloud_mask_short_names=self.viirs_cloud_mask_short_names,
                modis_band_list=self.modis_full_band_list,
                modis_nir_red_band_list=self.modis_nir_red_band_list,
                modis_nbr_bands=self.modis_nbr_bands,
                modis_ndvi_bands=self.modis_ndvi_bands,
                modis_short_names=self.modis_short_names,
                modis_cloud_mask_short_names=self.modis_cloud_mask_short_names,
                landsat_band_list=self.landsat_full_band_list,
                landsat_nir_red_band_list=self.landsat_nir_red_band_list,
                landsat_nbr_bands=self.landsat_nbr_bands,
                landsat_ndvi_bands=self.landsat_ndvi_bands,
                landsat_short_names=self.landsat_short_names,
                landsat_cloud_mask_short_names=self.landsat_cloud_mask_short_names,
                satpy_area_def=self.satpy_area_def,
                supported_instruments=self.supported_instruments
            ))
        else:
            print(f"Granule registry for data year {data_year} already exists.")

    def __getitem__(self, data_year):
        """Allow access to granule registries using indexing syntax."""
        data_year = str(data_year)
        return self.granule_registry.get(data_year, None)

    def __setitem__(self, data_year, granule_registry):
        """Allow access to granule registries using indexing syntax."""
        data_year = str(data_year)
        self.granule_registry[data_year] = granule_registry

    def define_satpy_area_def(self,width=500,height=1000):
        import warnings
        from pyresample import create_area_def
        warnings.warn("Using hardcoded parameters for registry.satpy_area_def")
        warnings.warn("Projection set to Web Mercator (EPSG:3857) for registry.satpy_area_def.")
        area_id = self.spatial_name
        # projection = {'proj': 'lcc', 'lon_0': -95, 'lat_0': 25, 'lat_1': 35}
        projection = 3857 # EPSG Code for Web Mercator
        area_extent = self.spatial
        units = 'degrees'
        satpy_area_def = create_area_def(
            area_id=area_id,
            projection=projection,
            width=width,
            height=height,
            area_extent=area_extent,
            units=units
        )
        self.satpy_area_def = satpy_area_def

    def read_config(self,return_raw_config=False):
        """Reads the asset registry configuration from a file."""
        config_file = importlib.resources.files('fhba') / 'config.yaml'
        proj_home_dir = importlib.resources.files('fhba') / "app"
        try:
            with open(config_file) as f:
                config = yaml.safe_load(f)
                config = {k:config[k] for k in config if not k.startswith('_')}
        except FileNotFoundError as e:
            msg = f"Configuration file {config_file} not found. Please ensure that 'config.yaml' is located at the root of the 'fhba' package and contains the necessary directory paths."
            raise FileNotFoundError(msg) from e
        except yaml.YAMLError as e:
            msg = f"Error parsing the configuration file {config_file}. Please ensure that 'config.yaml' is properly formatted and contains valid YAML syntax."
            raise yaml.YAMLError(msg) from e

        if return_raw_config:
            return config

        # Spatial Config
        self.min_lat = config['spatial'].get('min_lat', None)
        self.min_lon = config['spatial'].get('min_lon', None)
        self.max_lat = config['spatial'].get('max_lat', None)
        self.max_lon = config['spatial'].get('max_lon', None)
        self.spatial_name = config['spatial'].get('spatial_name', None)
        self.spatial = (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

        # Temporal Config
        self.start_month = config['temporal'].get('start_month', None)
        self.start_day = config['temporal'].get('start_day', None)
        self.end_month = config['temporal'].get('end_month', None)
        self.end_day = config['temporal'].get('end_day', None)

        # Filepath Config
        self.raw_data_dir = str(proj_home_dir / config['paths'].get('raw_data_dir', None))
        self.processed_data_dir = str(proj_home_dir / config['paths'].get('processed_data_dir', None))
        self.truecolor_img_dir = str(proj_home_dir / config['paths'].get('truecolor_img_dir', None))
        self.userpts_dir = str(proj_home_dir / config['paths'].get('userpts_dir', None))
        self.burnmask_dir = str(proj_home_dir / config['paths'].get('burnmask_dir', None))
        self.county_shp = str(proj_home_dir / config['paths'].get('county_shp', None))

        # ── Satellite-specific config ──────────────────────────────────────────
        _viirs_sat_keys = [k for k in config['viirs']
                           if k not in ('full_band_list', 'nir_red_band_list',
                                        'nbr_bands', 'ndvi_bands')]
        _modis_sat_keys = [k for k in config['modis']
                           if k not in ('full_band_list', 'nir_red_band_list',
                                        'nbr_bands', 'ndvi_bands')]

        self.viirs_short_names = {k: config['viirs'][k]['short_name_list'] for k in _viirs_sat_keys}
        self.viirs_cloud_mask_short_names = {k: config['viirs'][k]['cloud_mask_short_name'] for k in _viirs_sat_keys}
        self.viirs_full_band_list = config['viirs'].get('full_band_list', [])
        self.viirs_nir_red_band_list = config['viirs'].get('nir_red_band_list', [])
        self.viirs_nbr_bands = config['viirs'].get('nbr_bands', None)
        self.viirs_ndvi_bands = config['viirs'].get('ndvi_bands', None)

        self.modis_short_names = {k: config['modis'][k]['short_name_list'] for k in _modis_sat_keys}
        self.modis_cloud_mask_short_names = {k: config['modis'][k]['cloud_mask_short_name'] for k in _modis_sat_keys}
        self.modis_full_band_list = config['modis'].get('full_band_list', [])
        self.modis_nir_red_band_list = config['modis'].get('nir_red_band_list', [])
        self.modis_nbr_bands = config['modis'].get('nbr_bands', None)
        self.modis_ndvi_bands = config['modis'].get('ndvi_bands', None)

        # Landsat (optional — config block may not exist in older installs)
        _landsat_cfg = config.get('landsat', {})
        _landsat_sat_keys = [k for k in _landsat_cfg
                             if k not in ('full_band_list', 'nir_red_band_list',
                                          'nbr_bands', 'ndvi_bands')]
        self.landsat_short_names = {k: _landsat_cfg[k]['short_name_list'] for k in _landsat_sat_keys}
        self.landsat_cloud_mask_short_names = {k: _landsat_cfg[k]['cloud_mask_short_name'] for k in _landsat_sat_keys}
        self.landsat_full_band_list = _landsat_cfg.get('full_band_list', [])
        self.landsat_nir_red_band_list = _landsat_cfg.get('nir_red_band_list', [])
        self.landsat_nbr_bands = _landsat_cfg.get('nbr_bands', None)
        self.landsat_ndvi_bands = _landsat_cfg.get('ndvi_bands', None)

        self.supported_instruments = (
            list(self.viirs_short_names.keys()) +
            list(self.modis_short_names.keys()) +
            list(self.landsat_short_names.keys())
        )

    def review_file_status(self):
        """Review the status of all files in the registry."""
        for data_year in self.granule_registry:
            self.granule_registry[data_year].review_file_status()

    def to_dict(self):
        """Convert the registry to a dictionary representation."""
        dict_repr = {k:v for k,v in inspect.getmembers(self) if not k.startswith('_') and not inspect.ismethod(v) and not k=='granule_registry'}
        dict_repr['granule_registry'] = {year: self.granule_registry[year].to_dict() for year in self.granule_registry}
        if 'satpy_area_def' in dict_repr.keys():
            dict_repr['satpy_area_def'] = str(dict_repr['satpy_area_def'])
        return dict_repr

    def from_dict(self, data):
        for k in data:
            if k != 'granule_registry':
                setattr(self, k, data[k])
        self.granule_registry = {
            year: GranuleRegistry().from_dict(data['granule_registry'][year])
            for year in data['granule_registry']
        }

        if 'satpy_area_def' in data:
            self.define_satpy_area_def()
            for gr in self.granule_registry:
                self.granule_registry[gr].satpy_area_def = self.satpy_area_def
                for gm in self.granule_registry[gr].satellites:
                    self.granule_registry[gr].satellites[gm].satpy_area_def = self.satpy_area_def

        # Re-apply current config.yaml values to every loaded GranuleRegistry.
        self.read_config()
        _config_fields = {
            'supported_instruments',
            'viirs_short_names', 'viirs_cloud_mask_short_names',
            'viirs_full_band_list', 'viirs_nir_red_band_list',
            'viirs_nbr_bands', 'viirs_ndvi_bands',
            'modis_short_names', 'modis_cloud_mask_short_names',
            'modis_full_band_list', 'modis_nir_red_band_list',
            'modis_nbr_bands', 'modis_ndvi_bands',
            'landsat_short_names', 'landsat_cloud_mask_short_names',
            'landsat_full_band_list', 'landsat_nir_red_band_list',
            'landsat_nbr_bands', 'landsat_ndvi_bands',
        }
        for gr in self.granule_registry.values():
            for field in _config_fields:
                val = getattr(self, field, None)
                if val is not None:
                    setattr(gr, field, val)
        return self

    def save_json(self, json_file=importlib.resources.files('fhba') / 'app' / 'state' / 'registry.json'):
        """Save the registry to a JSON file (Windows/OneDrive-safe)."""
        data = self.to_dict()
        json_file.parent.mkdir(parents=True, exist_ok=True)

        import tempfile
        # Create a temp file using mkstemp so the FD can be properly closed before replace (avoids WinError 32)
        fd, temp_path = tempfile.mkstemp(dir=json_file.parent, suffix='.PENDING_.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            # FD closed now; safe to replace on Windows
            os.replace(temp_path, json_file)
        finally:
            # Clean up in case something failed before replace
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    def load_json(self,json_file=importlib.resources.files('fhba') / 'app' / 'state' / 'registry.json'):
        """Load the registry from a JSON file."""
        print(f"Loading registry from JSON file: {json_file}")
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.from_dict(data)
        except FileNotFoundError:
            print(f"JSON file {json_file} not found. Starting with an empty registry.")
        except json.JSONDecodeError as e:
            msg = f"Error decoding JSON from file {json_file}. Please ensure that the file contains valid JSON."
            raise json.JSONDecodeError(msg, e.doc, e.pos) from e
        return self

    def __str__(self):
        class_str = f"Registry for managing satellite granules."
        if self.granule_registry:
            for data_year, granule_registry in self.granule_registry.items():
                disp = lambda x: f"{x} {granule_registry.satellites[x].instrument.upper()}"
                class_str += f"\n - {data_year}: satellites: {list(map(disp, granule_registry.satellites.keys()))}"
        else:
            class_str += "\n - Add granule registries using add_granule_registry(data_year) method."
        return class_str
