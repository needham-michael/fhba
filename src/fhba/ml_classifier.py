"""Machine-learning burn-scar classifiers (Random Forest and SVM).

The public interface mirrors ``eucl_classifier.classify_pixels_eucl`` so that
``GranuleManager.classify_pixels`` can swap between methods transparently.
"""
import logging

import numpy as np
import pandas as pd
import xarray as xr

from scipy.ndimage import binary_dilation
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler

from fhba.eucl_classifier import stack_bands, get_cloudmask

logger = logging.getLogger(__name__)

_SUPPORTED_METHODS = {"rf", "svm"}


def classify_pixels_ml(
        userpts_csv, processed_nc, landmask_nc, cldmask_nc=None,
        band_list=None, nbr_bands=None, ndvi_bands=None,
        dnbr_array=None, area_def=None, lonlat_to_xy=False,
        min_area_pixels=5, method="rf", **clf_kwargs):
    """Classify pixels as burned or unburned using a supervised ML classifier.

    Parameters
    ----------
    userpts_csv : str
        Path to CSV with user-selected training points (columns: x, y, isBurned).
    processed_nc : str
        Path to preprocessed satellite NetCDF file.
    landmask_nc : str
        Path to resampled NLCD land-cover mask GeoTIFF.
    cldmask_nc : str, optional
        Path to processed cloud mask NetCDF file.
    band_list : list of str, optional
        Bands to use as features. Defaults to all I/M bands in the dataset.
    nbr_bands : list of str, optional
        [nir_band, swir_band] for NBR feature computation.
    ndvi_bands : list of str, optional
        [nir_band, red_band] for NDVI feature computation.
    dnbr_array : xr.DataArray, optional
        Pre-computed dNBR array to append as an additional feature.
    area_def : pyresample.AreaDefinition, optional
        Required when lonlat_to_xy=True.
    lonlat_to_xy : bool, default False
        Convert lon/lat training-point coordinates to projected x/y.
    min_area_pixels : int, default 5
        Minimum connected-component size to retain in the burn mask.
    method : {"rf", "svm"}, default "rf"
        Classifier to use.  "rf" = Random Forest; "svm" = Support Vector Machine.
    **clf_kwargs
        Extra keyword arguments forwarded to the scikit-learn classifier constructor.

    Returns
    -------
    burnmask : xr.Dataset
        Dataset with a 'burnmask' variable (1 = burned, 0 = not burned/masked).
    confidence_ds : xr.Dataset
        Dataset with a 'confidence' variable.
        For RF: predicted probability of the burned class.
        For SVM: signed decision-function distance from the hyperplane.
    """
    from fhba.process_landcover_mask import flip_singletons

    if method not in _SUPPORTED_METHODS:
        raise ValueError(f"method must be one of {_SUPPORTED_METHODS}, got '{method}'.")

    with xr.open_dataset(landmask_nc).isel(band=0) as lcmask:
        with xr.open_dataset(processed_nc) as ds_processed:

            df_userpts = pd.read_csv(userpts_csv)

            if lonlat_to_xy:
                if area_def is None:
                    raise ValueError(
                        "area_def must be provided for coordinate transformation.")
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
                daily_mask = xr.DataArray(
                    data=daily_mask, coords=cldmask.coords, dims=cldmask.dims)
            except ValueError:
                logger.error(
                    "daily_mask computation failed. lcmask=%s  cldmask=%s", lcmask, cldmask)
                raise

            if band_list is None:
                band_list = [v for v in ds_processed.data_vars if v.startswith(('I', 'M'))]

            pixel_vector_1d, pixel_vector = stack_bands(
                ds_processed, band_list, nbr_bands=nbr_bands, ndvi_bands=ndvi_bands)

            Xfull = pd.DataFrame(pixel_vector_1d.values).T
            Xfull.columns = Xfull.columns.astype(str)

            if dnbr_array is not None:
                dnbr_stacked = dnbr_array.stack(z=['x', 'y'])
                Xfull['dnbr'] = dnbr_stacked.values

            # -- Build training matrices ----------------------------------------
            df_isburned = df_userpts[df_userpts['isBurned'] == 1]
            df_unburned = df_userpts[df_userpts['isBurned'] == 0]

            x_burned   = df_isburned['x'].values
            y_burned   = df_isburned['y'].values
            x_unburned = df_unburned['x'].values
            y_unburned = df_unburned['y'].values

            Xbrn = pd.DataFrame([
                pixel_vector.sel(x=x, y=y, method='nearest').data
                for x, y in zip(x_burned, y_burned)])
            Xbrn.columns = Xbrn.columns.astype(str)
            Xunb = pd.DataFrame([
                pixel_vector.sel(x=x, y=y, method='nearest').data
                for x, y in zip(x_unburned, y_unburned)])
            Xunb.columns = Xunb.columns.astype(str)

            if dnbr_array is not None:
                Xbrn['dnbr'] = [float(dnbr_array.sel(x=x, y=y, method='nearest'))
                                for x, y in zip(x_burned, y_burned)]
                Xunb['dnbr'] = [float(dnbr_array.sel(x=x, y=y, method='nearest'))
                                for x, y in zip(x_unburned, y_unburned)]

            X_train = pd.concat([Xbrn, Xunb], ignore_index=True)
            y_train = np.array([1] * len(Xbrn) + [0] * len(Xunb))

            # -- Scale features -------------------------------------------------
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            Xfull_scaled   = scaler.transform(Xfull)

            # -- Train & predict ------------------------------------------------
            if method == "rf":
                defaults = dict(n_estimators=200, n_jobs=-1, random_state=42)
                defaults.update(clf_kwargs)
                clf = RandomForestClassifier(**defaults)
                clf.fit(X_train_scaled, y_train)
                y_pred = clf.predict(Xfull_scaled)
                # Probability of the burned class (index 1)
                class_idx = list(clf.classes_).index(1)
                confidence_vals = clf.predict_proba(Xfull_scaled)[:, class_idx]

            else:  # svm
                defaults = dict(kernel='rbf', C=1.0, probability=False)
                defaults.update(clf_kwargs)
                clf = SVC(**defaults)
                clf.fit(X_train_scaled, y_train)
                y_pred = clf.predict(Xfull_scaled)
                # Signed distance from the decision boundary
                confidence_vals = clf.decision_function(Xfull_scaled)

            logger.info(
                "ML classifier (%s): %d burned / %d unburned pixels predicted.",
                method, int(y_pred.sum()), int((y_pred == 0).sum()))

            # -- Reshape to 2D and apply masks ----------------------------------
            is_burned = xr.DataArray(
                y_pred.astype(bool),
                coords={'z': pixel_vector_1d.z}).unstack()

            if min_area_pixels > 1:
                cleaned = flip_singletons(
                    is_burned.values, diagonals=False, which='true',
                    min_size=min_area_pixels)
                is_burned = xr.DataArray(
                    cleaned, coords=is_burned.coords, dims=is_burned.dims)

            burnmask = (is_burned * daily_mask).T.to_dataset(name='burnmask')

            confidence_da = xr.DataArray(
                confidence_vals, coords={'z': pixel_vector_1d.z}).unstack()
            confidence_ds = (confidence_da * daily_mask).T.to_dataset(name='confidence')

    return burnmask, confidence_ds