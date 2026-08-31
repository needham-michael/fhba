import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from typing import Tuple

from sklearn.metrics import euclidean_distances

from fhba.classification.utils import prep_inputs

def eucl(
    ds : xr.Dataset,
    userpts : gpd.GeoDataFrame,
) -> Tuple[xr.DataArray,xr.DataArray]:

    inp = prep_inputs(ds=ds,userpts=userpts)

    dist2brn = np.mean(euclidean_distances(X=inp.Xfull, Y=inp.Xbrn), axis=1)
    dist2unb = np.mean(euclidean_distances(X=inp.Xfull, Y=inp.Xunb), axis=1)

    inp.Xfull['isBurned'] = dist2brn < dist2unb
    inp.Xfull['confidence'] = dist2unb - dist2brn

    is_burned = xr.DataArray(inp.Xfull['isBurned'], coords={'z': inp.pixel_vector_1d.z}).unstack()
    confidence = xr.DataArray(inp.Xfull['confidence'], coords={'z': inp.pixel_vector_1d.z}).unstack()

    return (is_burned, confidence)