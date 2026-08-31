
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import xarray as xr

def stack_bands(ds):
    pixel_vector = xr.concat([ds[b] for b in ds.data_vars],dim="band")
    pixel_vector_1d = pixel_vector.stack(z=['x', 'y'])

    return pixel_vector_1d, pixel_vector


def prep_inputs(
    ds : xr.Dataset,
    userpts : gpd.GeoDataFrame,
) -> SimpleNamespace:
    
    df_isburned = userpts[userpts['isBurned'] == 1]
    df_unburned = userpts[userpts['isBurned'] == 0]

    pixel_vector_1d, pixel_vector = stack_bands(ds)
    
    Xfull = pd.DataFrame(pixel_vector_1d.values).T
    Xbrn = pd.DataFrame([pixel_vector.sel(x=p.x,y=p.y,method='nearest').data for p in df_isburned.geometry] )
    Xunb = pd.DataFrame([pixel_vector.sel(x=p.x,y=p.y,method='nearest').data for p in df_unburned.geometry] )

    return SimpleNamespace(
        df_isburned = df_isburned,
        df_unburned = df_unburned,
        pixel_vector_1d = pixel_vector_1d,
        pixel_vector = pixel_vector,
        Xfull = Xfull,
        Xbrn = Xbrn,
        Xunb = Xunb,
        )

    