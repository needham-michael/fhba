import geopandas as gpd
import xarray as xr

from fhba.classification.eucl import eucl
from fhba.classification.ml_classifier import rforest, svm

def classify_pixels(
    ds : xr.Dataset,
    userpts : gpd.GeoDataFrame,
    method : str,
    mask : xr.DataArray | None = None,
    **kwargs
    ) -> xr.DataArray:

    if method == 'eucl':
        is_burned, confidence = eucl(ds,userpts)
    elif method == 'rforest':
        is_burned, confidence = rforest(ds,userpts,**kwargs)
    elif method == 'svm':
        is_burned, confidence = svm(ds,userpts,**kwargs)

    else:
        raise NotImplementedError()

    if mask is not None:
        is_burned = is_burned * mask
        confidence = confidence * mask

    return (is_burned, confidence)
