import pandas as pd
import numpy as np
import xarray as xr

from scipy.ndimage import binary_dilation
from sklearn.metrics import euclidean_distances

from fhba.image import nonlinear_enhancement

def stack_bands(ds_processed,band_list,nbr_bands=None):

    bands = [nonlinear_enhancement(255 * ds_processed[x].values / 100) / 255 for x in band_list]
 
    if nbr_bands is not None:
        nir_band, swir_band = nbr_bands
        nir = ds_processed[nir_band]
        swir = ds_processed[swir_band]

        nbr = (nir - swir) / (nir + swir)

        bands.append(nbr)

    pixel_vector = xr.concat(
        [xr.DataArray(
            band,
            dims=ds_processed['I01'].dims,
            coords=ds_processed['I01'].coords
            ) for band in bands],
        dim="band"
    ).transpose(...,"band")

    pixel_vector_1d = pixel_vector.stack(z=['x','y'])

    return pixel_vector_1d, pixel_vector

def get_cloudmask(cldmask_nc,threshold=0.80):
    with xr.open_dataset(cldmask_nc) as ds_cldmask:

        cldmask = ds_cldmask['Clear_Sky_Confidence'] >= threshold

        # Apply binary dilation to expand areas around cloudy pixels to account for 
        # cloud shadow and other cloud edge effects
        cldmask = xr.DataArray(
            data=1-binary_dilation(binary_dilation(1-cldmask)),
            coords=cldmask.coords,
            dims=cldmask.dims
        )

    return cldmask

def classify_pixels_eucl(
        userpts_csv,processed_nc,landmask_nc,cldmask_nc=None,band_list=None,nbr_bands=None,area_def=None,lonlat_to_xy=False):

    with xr.open_dataset(landmask_nc).isel(band=0) as lcmask:
        with xr.open_dataset(processed_nc) as ds_processed:

            ds_processed = xr.open_dataset(processed_nc)
            df_userpts = pd.read_csv(userpts_csv)

            if lonlat_to_xy:
                if area_def is None:
                    raise ValueError("area_def must be provided for coordinate transformation in classify_pixels_eucl.")

                x,y = area_def.get_projection_coordinates_from_lonlat(df_userpts['longitude'],df_userpts['latitude'])

                df_userpts['x'] = x
                df_userpts['y'] = y

            x = df_userpts['x']
            y = df_userpts['y']

            if cldmask_nc is not None:
                cldmask = get_cloudmask(cldmask_nc)
            else:
                cldmask = xr.ones_like(lcmask)

            daily_mask = lcmask * cldmask

            if band_list is None:
                band_list = [var for var in ds_processed.data_vars if var.startswith(('I','M'))]

            pixel_vector_1d, pixel_vector = stack_bands(ds_processed,band_list,nbr_bands=nbr_bands)

            Xfull = pd.DataFrame(pixel_vector_1d.values).T

            df_isburned = df_userpts[df_userpts['isBurned']==1]
            df_unburned = df_userpts[df_userpts['isBurned']==0]

            x_burned = df_isburned['x'].values
            y_burned = df_isburned['y'].values
            x_unburned = df_unburned['x'].values
            y_unburned = df_unburned['y'].values

            Xbrn = pd.DataFrame([pixel_vector.sel(x=x,y=y,method='nearest').data for x,y in zip(x_burned,y_burned)])
            Xunb = pd.DataFrame([pixel_vector.sel(x=x,y=y,method='nearest').data for x,y in zip(x_unburned,y_unburned)])

            # Calculate the distance between each pixel and each point within the burned and
            # unburned sets, then calculate the mean from this distance
            dist2brn = np.mean(euclidean_distances(X=Xfull,Y=Xbrn),axis=1)
            dist2unb = np.mean(euclidean_distances(X=Xfull,Y=Xunb),axis=1)

            # Identify whether a given pixel is closer to the burned or unburned set   
            Xfull['isBurned'] = dist2brn < dist2unb 

            is_burned = xr.DataArray(Xfull['isBurned'],coords={'z':pixel_vector_1d.z}).unstack()

            burnmask = (is_burned * daily_mask.band_data).T.to_dataset(name='burnmask')
            

            # burnmask = (daily_mask.band_data * is_burned.T).to_dataset(name='burnmask')

            # burnmask['lons'] = xr.DataArray(
            #     ds_processed.longitude.values,
            #     coords=burnmask.burnmask.coords,
            #     dims=burnmask.burnmask.dims
            # )

            # burnmask['lats'] = xr.DataArray(
            #     ds_processed.latitude.values,
            #     coords=burnmask.burnmask.coords,
            #     dims=burnmask.burnmask.dims
            # )

    return burnmask