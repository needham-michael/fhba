"""Registry classes to manage satellite granule metadata and download/processing status."""

from functools import lru_cache
import importlib
import inspect
import logging
import os
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
from scipy import ndimage as ndi

from fhba.eucl_classifier import classify_pixels_eucl
from fhba.image import nonlinear_enhancement

logger = logging.getLogger(__name__)

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
                start=self.start_date,end=self.end_date,freq='D'
            ).strftime("%Y-%m-%d").tolist()
            self.download_status = {d:False for d in date_range}
            self.cloud_mask_download_status = {d:False for d in date_range}
            self.qc_status = {d:-1 for d in date_range}
            self.processing_status = {d:False for d in date_range}
            self.user_categorization_by_date = {d:"Uncategorized" for d in date_range}
            self.analysis_status = {d:"Unanalyzed" for d in date_range}
            self.categorization_status = {d:"Uncategorized" for d in date_range}

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
            # NOTE 4/8/2026 build this as a dict of dicts where the top level key is 
            # the method (e.g., SVM / RF / EUCL) and the sub-key is the date. This will
            # allow for tracking the different methods

    def classify_pixels(self,date,method='eucl',landcover_mask_file=None,
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
        cldmask_file = self.processed_cloud_masks_by_date[date]

        if landcover_mask_file is None:
            landcover_mask_file = importlib.resources.files("fhba.app.appdata.annual_nlcd") / f"NLCD_LandMask_{self.spatial_name}.tif"
            
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
    
    def aggregate_burnmasks(self, out_file=None, method='eucl',ytd_cutoff=None, as_doy=False):
        """Union all finalized burn masks into a single seasonal burn map.
        Parameters
        ----------
        out_file : str, optional
            Output GeoTIFF path. Defaults to
            ``<burnmask_dir>/<satellite>_<spatial>_seasonal_burnmask.tif``.
        method : str, default 'eucl'
            Specify which classification method's burnmasks to aggregate (e.g., 'eucl', 
            'rf', 'svm'). Must match the key used in `self.gm.burnmask_by_date`. 
        ytd_cutoff : str, optional
            If provided, only burn masks from dates up to and including the cutoff date
            will be included in the YTD aggregation.
        as_doy : bool, default False
            If True, the output file will record burned area by the date of year(doy) in
            which the burn was first detected at a pixel.

        Returns
        -------
        str
            Path to the written seasonal burn map GeoTIFF.
        """
        import rioxarray as rxr
        # burnmask_files = getattr(self, 'burnmask_by_date', {})
        print(f"__ AGGREGATE_BURNMASKS DEBUG__ ")
        print(f"{self.burnmask_by_date = }")
        print(f"{ytd_cutoff = }")
        burnmask_files = self.burnmask_by_date.get(method, {})
        if not burnmask_files:
            raise ValueError("No finalized burn masks found. Run export_burnmask for at least one date first.")
        
        print("== Burn mask files to aggregate ==")
        print(burnmask_files)

        arrays = []
        dates = []

        if ytd_cutoff == 'None' or ytd_cutoff is None:
            print("NO YTD CUTOFF SPECIFIED: INCLUDING ALL DATES IN AGGREGATION")
            ytd_cutoff = pd.Timestamp(self.end_date)  # effectively include all dates up to end_date

        for date, path in sorted(burnmask_files.items()):
            print(f"{date} | {path}")
            if pd.to_datetime(date) <= pd.to_datetime(ytd_cutoff):
                if path and os.path.exists(path):
                    da = rxr.open_rasterio(path).squeeze()
                    da = da.expand_dims(dim={'time': [date]})
                    arrays.append(da)
                    dates.append(pd.to_datetime(date))
            else:
                print(f"Skipping {date} (YTD cutoff: {ytd_cutoff})")
        if not arrays:
            raise ValueError("Burn mask files listed in registry do not exist on disk.")

        stacked = xr.concat(arrays, dim='time')

        if as_doy:
            
            doy = xr.DataArray(
                data = [int(d.strftime("%j")) for d in dates],
                coords={'time':stacked.time}
                )
            
            seasonal = (stacked * doy)
            seasonal = seasonal.where(seasonal != 0).min(dim='time').fillna(0).astype(int)
            
        else:
            seasonal = stacked.max(dim='time')

        if out_file is None:
            os.makedirs(self.burnmask_dir, exist_ok=True)
            final_date = max(dates).strftime("%Y-%m-%d")
            out_file = os.path.join(
                self.burnmask_dir,
                f"{self.satellite_name}_{self.spatial_name}_{method}_{final_date}_YTD.tif")
            
        if as_doy:
            out_file = out_file.replace("_YTD.tif", "_DOY.tif")

        seasonal.rio.to_raster(out_file)
        logger.info("Seasonal burn map written to %s", out_file)
        return out_file
    
    def compute_burn_area_by_county(self, burnmask_file):
        """Compute burned area statistics per county from a burn mask GeoTIFF.
        
        Calculates area burned using Albers Equal-Area (EPSG:5070) projection. Includes 
        a total row summing all counties.
        
        Parameters
        ----------
        burnmask_file : str
            Path to a binary burn mask GeoTIFF (1 = burned, 0 = not burned).
        Returns
        -------
        gdf : geopandas.GeoDataFrame
            County-level statistics with columns:
            county_name, burned_area_km2_utm, burned_area_acres_utm,
            burned_area_km2_albers, burned_area_acres_albers.
            Includes a 'Total' row at the end.
        """
        import rioxarray as rxr
        counties = gpd.read_file(self.county_shp + ".shp")
        with rxr.open_rasterio(burnmask_file) as ds:
            
            # Reproject to Albers Equal-Area (EPSG:5070) for comparison
            ds_albers = ds.rio.reproject("EPSG:5070")
            res_x_albers, res_y_albers = ds_albers.rio.resolution()
            pixel_area_m2_albers = abs(res_x_albers * res_y_albers)
            pixel_area_km2_albers = pixel_area_m2_albers / 1_000_000
            pixel_area_acres_albers = pixel_area_m2_albers / 4046.856
            counties_albers = counties.to_crs("EPSG:5070")

            records = []
            total_km2_albers = 0.0
            total_acres_albers = 0.0
            
            name_col = next((c for c in counties.columns if 'name' in c.lower()), counties.columns[0])
            
            for idx in range(len(counties)):
                county_albers = counties_albers.iloc[idx]
                county_name = counties.iloc[idx][name_col]

                # Calculate for EPSG:5070
                try:
                    clipped_albers = ds_albers.rio.clip([county_albers.geometry], drop=True, all_touched=False)
                    n_burned_albers = int((clipped_albers == 1).sum())
                except Exception:
                    n_burned_albers = 0
                km2_albers = round(n_burned_albers * pixel_area_km2_albers, 4)
                acres_albers = round(n_burned_albers * pixel_area_acres_albers, 1)
                
                records.append({
                    'county_name': county_name,
                    'burned_area_km2': km2_albers,
                    'burned_area_acres': acres_albers,
                })
                
                total_km2_albers += km2_albers
                total_acres_albers += acres_albers
            
            # Add total row
            records.append({
                'county_name': 'Total',
                'burned_area_km2': round(total_km2_albers, 4),
                'burned_area_acres': round(total_acres_albers, 1),
            })
        
        # Create GeoDataFrame with geometries for counties and None for total
        geometries = list(counties['geometry'].values) + [None]
        gdf = gpd.GeoDataFrame(records, geometry=geometries, crs=counties.crs)
        return gdf

    def prepare_data(self,date):
        """Prepare data for a given date by downloading and preprocessing granules."""

        reflectance_granules = self.search_granules(date=date)
        cloud_mask_granules = self.search_cloud_mask_granules(date=date)

        self.download_granules(date=date,granule_search_results=reflectance_granules)
        self.download_cloud_mask_granules(date=date,cloud_mask_granule_search_results=cloud_mask_granules)
        self.preprocess_granules(date=date)

    def preprocess_granules(self,date,truecolor_from_worldview=False):
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
            print(f"Preprocessing already completed for this date. Skipping preprocessing step.")
            return

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
        
        nc_file_exists = os.path.exists(nc_file)
        cloud_mask_nc_file_exists = os.path.exists(cloud_mask_nc_file)
        truecolor_file_exists = os.path.exists(truecolor_file)

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
                band_list = self.full_band_list

                if truecolor_file_exists:
                    if "true_color" not in band_list:
                        band_list += ["true_color"]

                print(f"Loading bands: {band_list}")
                scene_full = Scene(filenames=granule_files, reader=f"{self.instrument}_l1b")
                scene_full.load(band_list)
                scene_regional = scene_full.resample(self.satpy_area_def,resampler='ewa')
                scene_regional.load(band_list)
        
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

                self.retrieve_truecolor_image(date,out_path=truecolor_file,overwrite=False)

            else:

                # Generate true color image with county overlay and save to disk
                print(f"Generating true color image preview")
                self.generate_truecolor_image(date,scene_regional,out_path=truecolor_file,overwrite=False)

        if cloud_mask_nc_file_exists:
            print(f"Processed cloud mask netcdf file already exists for {date}. Skipping save step.")
            self.processed_cloud_masks_by_date[date] = cloud_mask_nc_file

        else:
            
            # Load bands from raw granules and reproject 
            print(f"Loading and reprojecting cloud mask to defined {self.spatial_name} region...")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

                print(f"Loading cloud mask granules for {date}")
                cloud_mask_scene_full = Scene(filenames=cloud_mask_files, reader=f"{self.instrument}_l2")
                cloud_mask_scene_full.load(['Clear_Sky_Confidence'])
                cloud_mask_scene_regional = cloud_mask_scene_full.resample(self.satpy_area_def,resampler='nearest')
                cloud_mask_scene_regional.load(['Clear_Sky_Confidence'])

                cloud_mask_scene_regional.save_datasets(
                    filename=cloud_mask_nc_file,writer='cf'
                )

            self.processed_cloud_masks_by_date[date] = cloud_mask_nc_file
            self.update_processing_status(date,True)

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
    
    def resample_openwatermask(
            self,openwater_mask_file_fullres,openwater_mask_file,flip_single_pixels=True,return_mask=False):
        """Resample NLCD Open Water Mask to the local spatial domain using nearest-neighbor"""
        import rasterio
        import rioxarray as rxr
        from pyresample import image, geometry
        from pyresample.kd_tree import XArrayResamplerNN
        from rasterio.transform import Affine
        
        from fhba.process_landcover_mask import get_nlcd_area_definition, flip_singletons

        nlcd_mask = rxr.open_rasterio(openwater_mask_file_fullres)

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

        # Apply binary dilation on the inverted resampled field (0-open water, 1-other) 
        # to remove small features, then apply binary dilation 4 times on the resultant 
        # field to make a large buffer around large features
        mask_small_features = ndi.binary_dilation(
            1-ndi.binary_dilation(1-nlcd_mask_resampled),
            iterations=4) 
        
        # Apply this interim mask to the original resampled field to retain the finer-
        # scale features of the large water bodies
        mask_interim = mask_small_features * nlcd_mask_resampled

        # Apply a final binary dilation to extend the feature edges around large features
        mask_final = ndi.binary_dilation(mask_interim,iterations=1)

        if return_mask:
            return mask_final

        extent = self.satpy_area_def.area_extent

        dx, dy = self.satpy_area_def.resolution

        # x and y coords of upper left corner
        x0 = extent[0]
        y0 = extent[3]

        geotransform = [dx,0.0,x0,0.0,-dy,y0]
        geotransform = [float(x) for x in geotransform]

        geotransform = Affine(*geotransform)

        with rasterio.open(
            openwater_mask_file,
            'w',
            driver='GTiff',
            height=1000,
            width=500,
            count=1,
            dtype='int8',
            crs=self.satpy_area_def.crs.to_proj4(),
            transform=geotransform
        ) as dst:

            dst.write(mask_final,1)

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

    def search_granules(self,date,day_night_flag='day'):
        """Search for granules for the satellite within the specified temporal and spatial bounds."""

        if date not in self.download_status:
            raise ValueError(f"Date {date} is outside the defined date range for this GranuleManager of {self.start_date} to {self.end_date}.")
        
        if self.download_status[date] == True:
            print(f"Granules for date {date} have already been downloaded. Skipping search step.")
            return None

        print("Beginning Search for granules...")
        bounding_box = self.spatial

        print(f"{bounding_box = }")
        granule_search_results = earthaccess.search_data(
            short_name=self.short_name_list,
            bounding_box=tuple(bounding_box),
            temporal=(date,date),
            day_night_flag=day_night_flag,
            instrument=self.instrument.upper(),
            platform=self.satellite_name.upper()   
        )

        print(f"Found {len(granule_search_results)} granules for {self.satellite_name} {self.instrument.upper()} on {date}.")

        return granule_search_results
    
    def search_cloud_mask_granules(self,date,day_night_flag='day'):
        """Search for cloud mask granules for the satellite within the specified temporal and spatial bounds."""

        if date not in self.download_status:
            raise ValueError(f"Date {date} is outside the defined date range for this GranuleManager of {self.start_date} to {self.end_date}.")
        
        if self.cloud_mask_download_status[date] == True:
            print(f"Granules for date {date} have already been downloaded. Skipping search step.")
            return None
        
        print("Beginning Search for cloud mask granules...")
        granule_search_results = earthaccess.search_data(
            short_name=self.cloud_mask_short_name,
            bounding_box=tuple(self.spatial),
            temporal=(date,date),
            day_night_flag=day_night_flag,
            instrument=self.instrument.upper(),
            platform=self.satellite_name.upper()   
        )

        print(f"Found {len(granule_search_results)} cloud mask granules for {self.satellite_name} {self.instrument.upper()} on {date}.")

        return granule_search_results

    def download_granules(self,granule_search_results=None,date=None,day_night_flag='day',outdir=None,clobber=False,return_granules=False):
        """Download granules for the satellite within the specified temporal and spatial bounds."""

        if granule_search_results is None and date is None:
            raise ValueError("Either granule_search_results or date must be provided to download granules.")
        
        if granule_search_results is None:
            print("Searching for Granules")
            # Will return None if granules already downloaded to avoid redundant searches
            granule_search_results = self.search_granules(date,day_night_flag=day_night_flag)

        if granule_search_results is None:
            print("Granules already downloaded. Skipping download step.")
            self.download_status[date] = True
            return None


        print(f"Downloading {len(granule_search_results)} granules for {self.satellite_name} {self.instrument.upper()}")

        try: 
            granule_files = earthaccess.download(
                granule_search_results, 
                local_path=self.raw_data_dir if outdir is None else outdir
                )
            
            # Convert from Path objects to strings for JSON serialization
            if date is not None:
                self.raw_granules_by_date[date] = [str(f) for f in granule_files]
                self.download_status[date] = True
                print("Download complete. Filenames added to Registry")
                if return_granules:
                    return self.raw_granules_by_date[date]
                
            else:
                print("Download complete. Filenames not added to Registry since date was not provided.")
                if return_granules:
                    return [str(f) for f in granule_files]
        except ValueError as e:
            print(f"No granules found for {date}.")
        except earthaccess.exceptions.DownloadFailure as e:
            print("Download failed. Marking date as download error in Registry.")
            self.raw_granules_by_date[date] = ["DOWNLOAD ERROR"]

    def download_cloud_mask_granules(self,cloud_mask_granule_search_results=None,date=None,day_night_flag='day',outdir=None,clobber=False,return_granules=False):

        if cloud_mask_granule_search_results is None and date is None:
            raise ValueError("Either granule_search_results or date must be provided to download granules.")
        
        if cloud_mask_granule_search_results is None:

            # Will return None if granules already downloaded to avoid redundant searches
            cloud_mask_granule_search_results = self.search_cloud_mask_granules(date,day_night_flag=day_night_flag)

        if cloud_mask_granule_search_results is None:
            print("Cloud mask granules already downloaded. Skipping download step.")
            return None

        print(f"Downloading {len(cloud_mask_granule_search_results)} cloud mask granules for {self.satellite_name} {self.instrument.upper()}")

        try: 
            granule_files = earthaccess.download(
                cloud_mask_granule_search_results, 
                local_path=self.raw_data_dir if outdir is None else outdir
                )
            
            # Convert from Path objects to strings for JSON serialization
            if date is not None:
                self.raw_cloud_mask_granules_by_date[date] = [str(f) for f in granule_files]
                print("Download complete. Cloud mask filenames added to Registry")
                if return_granules:
                    return self.raw_cloud_mask_granules_by_date[date]
                
            else:
                print("Download complete. Filenames not added to Registry since date was not provided.")
                if return_granules:
                    return [str(f) for f in granule_files]
        except ValueError as e:
            print(f"No granules found for {date}.")
        except earthaccess.exceptions.DownloadFailure:
            print("Download failed. Marking date as download error in Registry.")
            self.raw_cloud_mask_granules_by_date[date] = ["DOWNLOAD ERROR"]
 
    def download_granules_date_range(self,day_night_flag='day',outdir=None,clobber=False):
        pass

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
    
    def retrieve_worldview_image(self,date,out_path,overwrite=False,truecolor=True):

        if os.path.exists(out_path) and not overwrite:
            print("True color image already exists. Skipping retrieval but adding file to Registry.")
            self.truecolor_images_by_date[date] = out_path

            return
        
        url_worldview = r"https://wvs.earthdata.nasa.gov/api/v1/snapshot?REQUEST=GetSnapshot&TIME=DATEPLACEHOLDERT00:00:00Z&BBOX=BBOXPLACEHOLDER&CRS=EPSG:4326&LAYERS=SATNAME_CorrectedReflectance_PRODUCT,Coastlines_15m&WRAP=day,x&FORMAT=image/jpeg&WIDTH=1138&HEIGHT=1820&colormaps=,&ts=1772050098509"

        if truecolor:
            url_worldview = url_worldview.replace("PRODUCT", "TrueColor")
        else:
            url_worldview = url_worldview.replace("PRODUCT", "BandsM11-I2-I1")

        url_worldview = url_worldview.replace("DATEPLACEHOLDER", date)

        bbox = f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"
        url_worldview = url_worldview.replace("BBOXPLACEHOLDER", bbox)

        sat_name = {
            'Suomi-NPP':'VIIRS_SNPP',
            'NOAA-20':'VIIRS_NOAA20',
            'NOAA-21':'VIIRS_NOAA21'
        }

        url_worldview = url_worldview.replace("SATNAME", sat_name[self.satellite_name])

        print(f"{url_worldview = }")

        response = requests.get(url_worldview)
        if response.status_code == 200:
            with open(out_path, 'wb') as f:
                f.write(response.content)
            self.truecolor_images_by_date[date] = out_path
            print(f"True color image retrieved and saved to {out_path}")
        else:
            print(f"Failed to retrieve true color image. HTTP status code: {response.status_code}")

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

        # TODO: NIR and RED are labeled backwards in this function; need to update the
        # labels, but waiting to do so until can confirm nothing breaks in the wider
        # application. Will also need to update the CONFIG file and test/confirm
        nir = ds[self.nir_red_band_list[0]].load()
        red = ds[self.nir_red_band_list[1]].load()

        # lons = ds.longitude.isel(y=0)
        # lats = ds.latitude.isel(x=0)

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
    
    @lru_cache(maxsize=10)
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
                # logger.warning("HMS fire points request failed for %s: %s", dt.date(), exc)
                continue

            if resp.status_code != 200:
                # logger.info("No HMS fire point file for %s (HTTP %s)", dt.date(), resp.status_code)
                continue

            try:
                lons, lats = _load_points_from_text(resp.text)
            except Exception as exc:
                # logger.warning("Could not parse HMS fire file for %s: %s", dt.date(), exc)
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
            # return ([],[])
        
        # return fire_by_age

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
        print("Starting to_df")
        df = pd.DataFrame({'download_status': self.download_status})
        df['processing_status'] = self.processing_status
        df['user_categorization'] = self.user_categorization_by_date
        df['analysis_status'] = self.analysis_status
        df['categorization_status'] = self.categorization_status
        df['truecolor_images_by_date'] = self.truecolor_images_by_date
        print("Ending to_df")
        return df


class GranuleRegistry:
    """"""
    def __init__(self,data_year=None,start_month=None,start_day=None,end_month=None,
                 end_day=None,raw_data_dir=None,processed_data_dir=None,
                 truecolor_img_dir=None,min_lat=None,min_lon=None,max_lat=None,
                 max_lon=None,spatial_name=None,viirs_short_names=None,
                 viirs_band_list=None,viirs_nir_red_band_list=None,modis_short_names=None,
                 modis_band_list=None,modis_nir_red_band_list=None,satpy_area_def=None,
                 county_shp=None,supported_instruments=None,userpts_dir=None,
                 viirs_cloud_mask_short_names=None,burnmask_dir=None):

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
        self.viirs_cloud_mask_short_names = viirs_cloud_mask_short_names
        self.modis_short_names = modis_short_names
        self.modis_band_list = modis_band_list
        self.modis_nir_red_band_list = modis_nir_red_band_list
        self.satpy_area_def = satpy_area_def
        self.supported_instruments = supported_instruments if supported_instruments is not None else []

        self.satellites = {}

    def add_satellite(self, satellite_name):

        """Add a satellite to the registry."""
        if satellite_name not in self.satellites:

            if satellite_name not in self.supported_instruments:
                raise ValueError(f"Satellite {satellite_name} not recognized. Valid options are {self.supported_instruments}.")
            
            
            if satellite_name in self.viirs_short_names.keys():
                instrument = 'viirs'
                short_name_list = self.viirs_short_names[satellite_name]
                full_band_list = self.viirs_band_list
                nir_red_band_list = self.viirs_nir_red_band_list
                cloud_mask_short_name = self.viirs_cloud_mask_short_names[satellite_name]
            else:
                instrument = 'modis'
                short_name_list = self.modis_short_names[satellite_name]
                full_band_list = self.modis_band_list
                nir_red_band_list = self.modis_nir_red_band_list


            self.satellites[satellite_name] = GranuleManager(
                satellite_name,
                short_name_list=short_name_list,
                instrument=instrument,
                start_date=f"{self.data_year}-{self.start_month:02d}-{self.start_day:02d}",
                end_date=f"{self.data_year}-{self.end_month:02d}-{self.end_day:02d}",
                raw_data_dir=self.raw_data_dir +"/" + satellite_name,
                processed_data_dir=self.processed_data_dir + "/" + satellite_name,
                truecolor_img_dir=self.truecolor_img_dir + "/" + satellite_name,
                userpts_dir=self.userpts_dir + "/" + satellite_name,
                burnmask_dir=self.burnmask_dir + "/" + satellite_name,
                full_band_list=full_band_list,
                cloud_mask_short_name=cloud_mask_short_name,
                nir_red_band_list=nir_red_band_list,
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
                county_shp = self.county_shp,
                min_lat=self.min_lat,
                min_lon=self.min_lon,
                max_lat=self.max_lat,
                max_lon=self.max_lon,
                spatial_name=self.spatial_name,
                viirs_band_list=self.viirs_full_band_list,
                viirs_nir_red_band_list=self.viirs_nir_red_band_list,
                viirs_short_names=self.viirs_short_names,
                viirs_cloud_mask_short_names=self.viirs_cloud_mask_short_names,
                modis_band_list=self.modis_full_band_list,
                modis_nir_red_band_list=self.modis_nir_red_band_list,
                modis_short_names=self.modis_short_names,
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

        config_file   = importlib.resources.files('fhba') / 'config.yaml'
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
        self.min_lat      = config['spatial'].get('min_lat', None)
        self.min_lon      = config['spatial'].get('min_lon', None)
        self.max_lat      = config['spatial'].get('max_lat', None)
        self.max_lon      = config['spatial'].get('max_lon', None)
        self.spatial_name = config['spatial'].get('spatial_name', None)
        self.spatial = (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

        # Temporal Config
        self.start_month = config['temporal'].get('start_month', None)
        self.start_day   = config['temporal'].get('start_day', None)
        self.end_month   = config['temporal'].get('end_month', None)
        self.end_day     = config['temporal'].get('end_day', None)

        # Filepath Config
        self.raw_data_dir       = str(proj_home_dir / config['paths'].get('raw_data_dir', None))
        self.processed_data_dir = str(proj_home_dir / config['paths'].get('processed_data_dir', None))
        self.truecolor_img_dir  = str(proj_home_dir / config['paths'].get('truecolor_img_dir', None))
        self.userpts_dir        = str(proj_home_dir / config['paths'].get('userpts_dir', None))
        self.burnmask_dir       = str(proj_home_dir / config['paths'].get('burnmask_dir', None))
        self.county_shp         = str(proj_home_dir / config['paths'].get('county_shp', None))

        # Satellite-specific Config
        self.viirs_short_names = {k:v['short_name_list'] for k,v in config['viirs'].items() if k != "full_band_list" and k != "nir_red_band_list" }
        self.modis_short_names = {k:v['short_name_list'] for k,v in config['modis'].items() if k != "full_band_list" and k != "nir_red_band_list" }

        self.viirs_cloud_mask_short_names = {k:v['cloud_mask_short_name'] for k,v in config['viirs'].items() if k != "full_band_list" and k != "nir_red_band_list" }
        
        
        self.viirs_full_band_list = config['viirs'].get('full_band_list', [])
        self.modis_full_band_list = config['modis'].get('full_band_list', [])
        self.viirs_nir_red_band_list = config['viirs'].get('nir_red_band_list', [])
        self.modis_nir_red_band_list = config['modis'].get('nir_red_band_list', [])

        self.supported_instruments = list(self.viirs_short_names.keys()) + list(self.modis_short_names.keys())


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
        
        self.granule_registry = {year: GranuleRegistry().from_dict(data['granule_registry'][year]) for year in data['granule_registry']}

        if 'satpy_area_def' in data:
            self.define_satpy_area_def()

            for gr in self.granule_registry:
                self.granule_registry[gr].satpy_area_def = self.satpy_area_def

                for gm in self.granule_registry[gr].satellites:
                    self.granule_registry[gr].satellites[gm].satpy_area_def = self.satpy_area_def
        return self
    
    def save_json(self,json_file=importlib.resources.files('fhba') / 'app' / 'state' / 'registry.json'):
        """Save the registry to a JSON file."""
        from fhba.registry import save_json_registry
        data = self.to_dict()
        save_json_registry(data=data,json_file=json_file)

    def load_json(self,json_file = None):
        """Load the registry from a JSON file."""
        from fhba.registry import load_json_registry 
        if json_file is None:
            json_file=importlib.resources.files('fhba') / 'app' / 'state' / 'registry.json'

        data = load_json_registry(json_file)
        self.from_dict(data)
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
        