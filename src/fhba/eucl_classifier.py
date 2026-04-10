import logging

import pandas as pd
import numpy as np
import xarray as xr

from scipy.ndimage import binary_dilation
from sklearn.metrics import euclidean_distances

from fhba.image import nonlinear_enhancement

# Number of pixels processed per chunk when computing pairwise distances.
# Keeps peak memory for the distance matrix at ~chunk * n_train_pts * 4 bytes
# (e.g. 250 000 × 37 × 4 ≈ 37 MB) instead of the full scene at once.
_DIST_CHUNK = 250_000


def _mean_eucl_dist_chunked(X_arr, Y_arr):
    """Mean Euclidean distance from each row of X to every row of Y, in chunks."""
    n = len(X_arr)
    out = np.empty(n, dtype=np.float32)
    for start in range(0, n, _DIST_CHUNK):
        end = min(start + _DIST_CHUNK, n)
        out[start:end] = np.mean(
            euclidean_distances(X_arr[start:end], Y_arr), axis=1
        ).astype(np.float32)
    return out

logger = logging.getLogger(__name__)


def stack_bands(ds_processed, band_list, nbr_bands=None, ndvi_bands=None):
    """Stack satellite bands into a (z, band) pixel matrix.

    Parameters
    ----------
    ds_processed : xr.Dataset
        Preprocessed satellite dataset.
    band_list : list of str
        Band variable names to include.
    nbr_bands : list of str, optional
        Two-element list [nir_band, swir_band] used to compute NBR and append
        it as an extra feature.
    ndvi_bands : list of str, optional
        Two-element list [nir_band, red_band] used to compute NDVI and append
        it as an extra feature.

    Returns
    -------
    pixel_vector_1d : xr.DataArray
        Stacked pixel matrix with dimensions (z, band).
    pixel_vector : xr.DataArray
        2D pixel matrix with dimensions (x, y, band).
    """
    bands = [nonlinear_enhancement(255 * ds_processed[x].values / 100) / 255 for x in band_list]

    if nbr_bands is not None:
        nir_band, swir_band = nbr_bands
        nir = ds_processed[nir_band]
        swir = ds_processed[swir_band]
        nbr = (nir - swir) / (nir + swir)
        bands.append(nbr)

    if ndvi_bands is not None:
        nir_band, red_band = ndvi_bands
        nir = ds_processed[nir_band]
        red = ds_processed[red_band]
        ndvi = (nir - red) / (nir + red)
        bands.append(ndvi)

    # Use first available I-band or M-band as coordinate reference
    ref_band = None
    for b in band_list:
        if b in ds_processed:
            ref_band = b
            break
    if ref_band is None:
        ref_band = list(ds_processed.data_vars)[0]

    pixel_vector = xr.concat(
        [xr.DataArray(
            band,
            dims=ds_processed[ref_band].dims,
            coords=ds_processed[ref_band].coords
        ) for band in bands],
        dim="band"
    ).transpose(..., "band")

    pixel_vector_1d = pixel_vector.stack(z=['x', 'y'])

    return pixel_vector_1d, pixel_vector


def get_cloudmask(cldmask_nc, threshold=0.80):
    """Load cloud mask and apply binary dilation to account for cloud edges/shadows.

    Supports two cloud mask formats:
    * ``Clear_Sky_Confidence`` (VIIRS / MODIS L2): continuous score in [0, 1];
      pixels with score >= *threshold* are treated as clear.
    * ``cloud_mask`` + optional ``cloud_shadow`` (MODIS MOD35/MYD35 direct HDF):
      cloud and shadow pixels are dilated separately; shadow gets a wider buffer
      because MODIS shadows are spatially offset from the parent cloud.
    * ``Fmask`` (Landsat HLS): uint8 bit-field; bits 1 (cloud), 2 (adjacent
      cloud) and 3 (cloud shadow) indicate contaminated pixels — clear when
      all three bits are zero (``Fmask & 0x0E == 0``).
    """
    with xr.open_dataset(cldmask_nc) as ds_cldmask:
        if 'Clear_Sky_Confidence' in ds_cldmask:
            cldmask = ds_cldmask['Clear_Sky_Confidence'] >= threshold
        elif 'cloud_mask' in ds_cldmask:
            # MODIS MOD35/MYD35: 2-bit values 0=Cloudy, 1=Uncertain,
            # 2=Probably Clear, 3=Confident Clear.
            # NaN = outside swath coverage; treat as clear.
            cloud_arr = (ds_cldmask['cloud_mask'].fillna(3) >= 2).values
            ref_da = ds_cldmask['cloud_mask']
            if 'cloud_shadow' in ds_cldmask:
                # Dilate cloud and shadow pixels separately.
                # Shadow gets 10 iterations (~5 km at 500 m/px) because MODIS
                # cloud shadows are often offset far from the cloud itself and
                # are frequently misclassified as burns without a wider buffer.
                # Clouds get 4 iterations (~2 km) for semi-transparent edges.
                shadow_arr = (ds_cldmask['cloud_shadow'].fillna(0) == 0).values
                cloud_dilated  = binary_dilation(~cloud_arr,  iterations=6)
                shadow_dilated = binary_dilation(~shadow_arr, iterations=20)
                clear_arr = ~cloud_dilated & ~shadow_dilated
                cldmask = xr.DataArray(clear_arr, coords=ref_da.coords, dims=ref_da.dims)
            else:
                cloud_dilated = binary_dilation(~cloud_arr, iterations=4)
                cldmask = xr.DataArray(~cloud_dilated, coords=ref_da.coords, dims=ref_da.dims)
            return cldmask
        elif 'Fmask' in ds_cldmask:
            # Fill NaN (no-data) with 0xFF so all cloud bits are set → cloudy
            fmask = ds_cldmask['Fmask'].fillna(255).astype(int)
            cldmask = (fmask & 0x0E) == 0   # bits 1/2/3 = cloud/adj-cloud/shadow
        else:
            raise KeyError(
                f"Cloud mask file '{cldmask_nc}' contains neither "
                "'Clear_Sky_Confidence', 'cloud_mask', nor 'Fmask'.")
        # General dilation for VIIRS / Landsat: 4 pixels to catch cloud edges
        inverted = 1 - cldmask
        for _ in range(4):
            inverted = binary_dilation(inverted)
        cldmask = xr.DataArray(
            data=1 - inverted,
            coords=cldmask.coords,
            dims=cldmask.dims
        )
    return cldmask


def compute_dnbr(pre_nc, post_nc, nir_band, swir_band):
    """Compute differenced Normalized Burn Ratio (dNBR = NBR_pre - NBR_post).

    Higher positive values indicate more severe burning.

    Parameters
    ----------
    pre_nc : str
        Path to pre-fire processed NetCDF file.
    post_nc : str
        Path to post-fire processed NetCDF file.
    nir_band : str
        Band name for Near-Infrared.
    swir_band : str
        Band name for Short-Wave Infrared.

    Returns
    -------
    dnbr : xr.DataArray
        dNBR DataArray on the post-fire grid.
    """
    def _nbr(ds, nir, swir):
        n = ds[nir].astype(float)
        s = ds[swir].astype(float)
        return (n - s) / (n + s)

    with xr.open_dataset(pre_nc) as ds_pre:
        nbr_pre = _nbr(ds_pre, nir_band, swir_band).load()

    with xr.open_dataset(post_nc) as ds_post:
        nbr_post = _nbr(ds_post, nir_band, swir_band).load()

    dnbr = nbr_pre - nbr_post
    return dnbr


def classify_pixels_eucl(
        userpts_csv, processed_nc, landmask_nc, cldmask_nc=None,
        band_list=None, nbr_bands=None, ndvi_bands=None,
        dnbr_array=None, area_def=None, lonlat_to_xy=False,
        min_area_pixels=5):
    """Classify pixels as burned or unburned using Euclidean distance to training points.

    Parameters
    ----------
    userpts_csv : str
        Path to CSV file with user-selected training points (columns: x, y, isBurned).
    processed_nc : str
        Path to preprocessed satellite NetCDF file.
    landmask_nc : str
        Path to resampled NLCD land-cover mask GeoTIFF.
    cldmask_nc : str, optional
        Path to processed cloud mask NetCDF file.
    band_list : list of str, optional
        Bands to use as features. Defaults to all I/M bands in the dataset.
    nbr_bands : list of str, optional
        [nir_band, swir_band] for computing NBR feature.
    ndvi_bands : list of str, optional
        [nir_band, red_band] for computing NDVI feature.
    dnbr_array : xr.DataArray, optional
        Pre-computed dNBR array to append as an additional feature.
    area_def : pyresample.AreaDefinition, optional
        Required when lonlat_to_xy=True to convert lon/lat to projected x/y.
    lonlat_to_xy : bool, default False
        If True, convert lon/lat columns in userpts_csv to projected coordinates.
    min_area_pixels : int, default 5
        Minimum connected-component size to retain in the burn mask. Components
        smaller than this are removed as noise.

    Returns
    -------
    burnmask : xr.Dataset
        Dataset with a 'burnmask' variable (1 = burned, 0 = not burned/masked).
    confidence_ds : xr.Dataset
        Dataset with a 'confidence' variable = (dist_to_unburned - dist_to_burned).
        Positive values indicate confident burned classification.
    """
    from fhba.process_landcover_mask import flip_singletons

    with xr.open_dataset(landmask_nc).isel(band=0) as lcmask:
        with xr.open_dataset(processed_nc) as ds_processed:

            ds_processed = xr.open_dataset(processed_nc)
            df_userpts = pd.read_csv(userpts_csv)

            # If the land mask was built at a different resolution than the
            # processed NC (e.g. land mask at shared VIIRS grid, NC at native
            # 30 m Landsat resolution), reproject it onto the NC grid so all
            # arrays share the same x/y coordinates before any arithmetic.
            _ref_var = list(ds_processed.data_vars)[0]
            _ref_da  = ds_processed[_ref_var]
            if lcmask.dims.get('x') != ds_processed.dims.get('x') or \
               lcmask.dims.get('y') != ds_processed.dims.get('y'):
                import rioxarray as _rxr
                lc_da = lcmask['band_data'].rio.write_crs(3857)
                lc_reproj = lc_da.rio.reproject_match(_ref_da.rio.write_crs(3857))
                lcmask = xr.Dataset({'band_data': lc_reproj})

            if lonlat_to_xy:
                if area_def is None:
                    raise ValueError(
                        "area_def must be provided for coordinate transformation in classify_pixels_eucl.")
                x, y = area_def.get_projection_coordinates_from_lonlat(
                    df_userpts['longitude'], df_userpts['latitude'])
                df_userpts['x'] = x
                df_userpts['y'] = y

            if cldmask_nc is not None:
                cldmask = get_cloudmask(cldmask_nc)
            else:
                cldmask = xr.ones_like(lcmask)

            try:
                daily_mask = (lcmask * cldmask).band_data
            except ValueError:
                logger.error("daily_mask computation failed. lcmask=%s  cldmask=%s", lcmask, cldmask)
                raise

            if band_list is None:
                band_list = [var for var in ds_processed.data_vars if var.startswith(('I', 'M'))]

            pixel_vector_1d, pixel_vector = stack_bands(
                ds_processed, band_list, nbr_bands=nbr_bands, ndvi_bands=ndvi_bands)

            # Optionally append dNBR as an extra feature column
            if dnbr_array is not None:
                dnbr_stacked = dnbr_array.stack(z=['x', 'y'])
                Xfull = pd.DataFrame(pixel_vector_1d.values).T
                Xfull['dnbr'] = dnbr_stacked.values
            else:
                Xfull = pd.DataFrame(pixel_vector_1d.values).T

            df_isburned = df_userpts[df_userpts['isBurned'] == 1]
            df_unburned = df_userpts[df_userpts['isBurned'] == 0]

            x_burned = df_isburned['x'].values
            y_burned = df_isburned['y'].values
            x_unburned = df_unburned['x'].values
            y_unburned = df_unburned['y'].values

            Xbrn = pd.DataFrame([pixel_vector.sel(x=x, y=y, method='nearest').data
                                  for x, y in zip(x_burned, y_burned)])
            Xunb = pd.DataFrame([pixel_vector.sel(x=x, y=y, method='nearest').data
                                  for x, y in zip(x_unburned, y_unburned)])

            # Append dNBR to training vectors too
            if dnbr_array is not None:
                brn_dnbr = [float(dnbr_array.sel(x=x, y=y, method='nearest'))
                            for x, y in zip(x_burned, y_burned)]
                unb_dnbr = [float(dnbr_array.sel(x=x, y=y, method='nearest'))
                            for x, y in zip(x_unburned, y_unburned)]
                Xbrn['dnbr'] = brn_dnbr
                Xunb['dnbr'] = unb_dnbr

            # NaN and inf pixels (swath edges, no-data areas, zero-denominator
            # index values) must be excluded — sklearn raises ValueError on both.
            # Track them so they can be forced to unburned in the output.
            nodata_mask = (Xfull.isnull() | np.isinf(Xfull)).any(axis=1)
            Xfull_clean = Xfull.fillna(0).replace([np.inf, -np.inf], 0)
            Xbrn = Xbrn.fillna(0).replace([np.inf, -np.inf], 0)
            Xunb = Xunb.fillna(0).replace([np.inf, -np.inf], 0)

            dist2brn = _mean_eucl_dist_chunked(Xfull_clean.values, Xbrn.values)
            dist2unb = _mean_eucl_dist_chunked(Xfull_clean.values, Xunb.values)

            # Force no-data pixels to unburned regardless of distance result
            Xfull['isBurned'] = (dist2brn < dist2unb) & ~nodata_mask.values

            # Confidence = margin between unburned distance and burned distance.
            # Positive → confidently burned; negative → confidently unburned.
            confidence_1d = dist2unb - dist2brn

            is_burned = xr.DataArray(
                Xfull['isBurned'], coords={'z': pixel_vector_1d.z}).unstack()

            # Apply minimum area filter to remove small spurious patches
            if min_area_pixels > 1:
                cleaned = flip_singletons(
                    is_burned.values, diagonals=False, which='true',
                    min_size=min_area_pixels)
                is_burned = xr.DataArray(cleaned, coords=is_burned.coords, dims=is_burned.dims)

            burnmask = (is_burned * daily_mask).T.to_dataset(name='burnmask')

            # Build confidence dataset on the same grid
            confidence_da = xr.DataArray(
                confidence_1d, coords={'z': pixel_vector_1d.z}).unstack()
            confidence_ds = (confidence_da * daily_mask).T.to_dataset(name='confidence')

    return burnmask, confidence_ds
