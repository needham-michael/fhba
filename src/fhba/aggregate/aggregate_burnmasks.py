from dataclasses import dataclass
from importlib import resources

import os
import re

import geopandas as gpd
import pandas as pd
import rasterio
import rioxarray as rxr
import xarray as xr

@dataclass
class UnifiedBurnmask:
    burnmasks: dict
    year: str
    classification_methods: list

    def __post_init__(self):
        self.satellites = list(self.burnmasks.keys())
        self.satellite_burnmasks = list(self.burnmasks.values())

        self._get_geotiff_reference_file()
        self._get_burnmask_dates()


    def join_burnmasks(self, method='any'):
        unified_burnmask = xr.zeros_like(self.satellite_burnmasks[0].binary_burnmask)

        for satellite_burnmasks in self.satellite_burnmasks:
            unified_burnmask += satellite_burnmasks.binary_burnmask

        if method == 'any':
            self.burnmask = (unified_burnmask > 0).astype(int)

        elif method == 'majority':
            self.burnmask = (unified_burnmask == (len(self.burnmasks)//2+1)).astype(int)

        elif method == 'unanimous':
            self.burnmask = (unified_burnmask == len(self.burnmasks)).astype(int)

        else:
            raise NotImplementedError

    def _get_burnmask_dates(self):
        # Get date range from disparate burnmasks
        burnmask_dates = []
        for bm in self.burnmasks.values():
            burnmask_dates += bm.burnmask_dates

        burnmask_dates = pd.to_datetime(burnmask_dates).unique().sort_values()
        self.burnmask_dates = list(burnmask_dates.strftime("%Y-%m-%d"))

    def _get_geotiff_reference_file(self):
        bm = self.satellite_burnmasks[0]
        ref_file = next(iter(next(iter(bm.burnmasks.values())).values()))
        self.geotiff_reference_file = ref_file

    def _generate_filename(self):
        raise NotImplementedError

    def write_burnmask(self,filename=None,overwrite=False):
        if filename is None:
            filename = self._generate_filename()

        exists=False
        if os.path.exists(filename):
            exists=True
            if not overwrite:
                raise FileExistsError
        
        temporary_file = filename.replace(".tif","_TMP.tif")

        with rasterio.open(self.geotiff_reference_file) as src:
            profile = src.profile

            with rasterio.open(temporary_file,"w",**profile) as dst:
                dst.write(self.burnmask.data,1)

        if exists:
            os.remove(filename)
        os.rename(temporary_file,filename)

@dataclass
class SatelliteBurnmask:
    satellite: str
    year: str
    classification_methods : list

    def __post_init__(self):
        self._get_burnmasks()
        self._get_spatial_ref()

    def _get_spatial_ref(self):
        _tmp_file = list(self.burnmasks[self.classification_methods[0]].values())[0]
        self.spatial_ref = rxr.open_rasterio(_tmp_file).spatial_ref

    def _get_burnmasks(self):
        self.burnmasks = {method:{} for method in self.classification_methods}
        file_dir = resources.files(f"fhba.app.appdata.burnmask.{self.year}.{self.satellite}")

        for file in file_dir.iterdir():
            file = str(file)
            for method in self.classification_methods:
                if method in file:
                    if "DOY" not in file and "YTD" not in file:
                        file_date = _get_filedate(file)
                        self.burnmasks[method][file_date] = file
                        continue

        self.burnmask_dates_by_method = {method:list(self.burnmasks[method].keys()) for method in self.classification_methods}
        self.burnmask_dates = sorted(list(set.union(*[set(x) for x in self.burnmask_dates_by_method.values()])))

    def _get_binary_burnmask(self, return_bm=False):
        """Get the binary burn mask from pixels id'd as burned at least once"""
        binary_burnmask = (self.merged_burnmask > 0).astype(int)

        if return_bm:
            return binary_burnmask

        self.binary_burnmask = binary_burnmask

    def _unite_burnmasks_diff_methods(self,date,method='majority'):
        try:
            burnmasks = [self.burnmasks[classification_method][date] for classification_method in self.classification_methods]
        except Exception as e:
            print(f"{classification_method = } | {date = }")
            raise e

        n_burnmasks = len(burnmasks)

        cmb = rxr.open_rasterio(burnmasks[0]).squeeze()
        
        for ix in range(1,n_burnmasks):
            cmb += rxr.open_rasterio(burnmasks[ix]).squeeze()

        if method == 'any':
            cmb = cmb > 0
        
        if method == 'majority':
            cmb = cmb >= ((n_burnmasks // 2) + 1)

        if method == 'unanimous':
            cmb = (cmb == n_burnmasks)

        return cmb.astype(int)

    def merge_burnmasks(self,method='majority',min_date=None,max_date=None,verbose=False,return_bm=False):
        """Merge burnmasks from separate dates into a single data array

        1.  For a given date, resolve differences in the burn mask 
            categorization between eucl, ef, etc. algs using either any, all, 
            or majority methods (see _unite_burnmasks_diff_methods for more 
            information)
        2.  Concatenate burnmasks from different days along the date dim
            and sum the result, effectively counting the number of days in
            which a pixel is identified as burned throughout the season
        3.  Assign the output data array as a class instance attribute, or
            return to the user. 
        """
        
        burnmasks_by_date = {}

        iter_dates = self.burnmask_dates
        
        if min_date is not None:
            iter_dates = [x for x in iter_dates if pd.to_datetime(x) >= pd.to_datetime(min_date)]
        if max_date is not None:
            iter_dates = [x for x in iter_dates if pd.to_datetime(x) <= pd.to_datetime(max_date)]

        if verbose:
            print(f"Combining Burnmasks for Dates using {method = }:")

        # Combine burnmasks derived from different methods (e.g., eucl, rf, etc.) into a single
        # burnmask for a specific date. The choice of method (any, all, majority) defines how
        # how disagreement among methods should be resolved.
        for date in iter_dates:
            if verbose:
                print(date)
            burnmasks_by_date[date] = self._unite_burnmasks_diff_methods(date,method=method)

        da = xr.concat(burnmasks_by_date.values(),dim='date').sum(dim='date')

        if return_bm:
            return da

        # Otherwise, assign the new da as an instance attribute 
        self.burnmasks_by_date = burnmasks_by_date
        self.merged_burnmask = da
        self._get_binary_burnmask()
        
def _get_filedate(file):
    return re.findall(r'\d{4}-\d{2}-\d{2}',file)[0]

def get_burn_area_by_county(burnmask_file,county_shp,include_total=False):
    """Compute burned area statistics per county from a burn mask GeoTIFF.
    
    Calculates area burned using Albers Equal-Area (EPSG:5070) projection. Includes 
    a total row summing all counties.
    
    Parameters
    ----------
    burnmask_file : str
        Path to a binary burn mask GeoTIFF (1 = burned, 0 = not burned).
    county_shp : shapefile representing county boundaries

    Returns
    -------
    gdf : geopandas.GeoDataFrame
        County-level statistics with columns:
        county_name, burned_area_km2_utm, burned_area_acres_utm,
        burned_area_km2_albers, burned_area_acres_albers.
        Includes a 'Total' row at the end.
    """
    
    with rxr.open_rasterio(burnmask_file) as ds:

        # Reproject to Albers Equal-Area (EPSG:5070) for comparison
        ds_albers = ds.rio.reproject("EPSG:5070")
        res_x_albers, res_y_albers = ds_albers.rio.resolution()
        pixel_area_m2_albers = abs(res_x_albers * res_y_albers)
        pixel_area_km2_albers = pixel_area_m2_albers / 1_000_000
        pixel_area_acres_albers = pixel_area_m2_albers / 4046.856

        records = []
        total_km2_albers = 0.0
        total_acres_albers = 0.0
        
        name_col = 'NAME'
        state_col = 'STATE_ABBR'

        counties = gpd.read_file(county_shp)
        counties_albers = counties.to_crs("EPSG:5070")

        for idx in range(len(counties)):
            county_albers = counties_albers.iloc[idx]
            county_name = counties.iloc[idx][name_col]
            state_name = counties.iloc[idx][state_col]

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
                'state_name': state_name,
                'burned_area_km2': km2_albers,
                'burned_area_acres': acres_albers,
            })
            
            total_km2_albers += km2_albers
            total_acres_albers += acres_albers

        # Create GeoDataFrame with geometries for counties and None for total
        geometries = list(counties['geometry'].values)

        if include_total:
            records.append({
                'county_name': 'Total',
                'burned_area_km2': round(total_km2_albers, 4),
                'burned_area_acres': round(total_acres_albers, 1),
            })

            geometries += [None]
            
        return gpd.GeoDataFrame(records, geometry=geometries, crs=counties.crs)