#!/usr/bin/env python3

"""
Script: unify_burnmasks.py
Purpose: Unify daily burnmasks into a single annual burnmask using burnmasks
from instruments onboard one or more satellites

Usage examples:
    # One required satellite
    python unify_burnmasks.py 2024 --satellite Suomi-NPP

    # Multiple satellites
    python unify_burnmasks.py 2024 \
        --satellite Suomi-NPP --satellite NOAA-20

    # Multiple satellites and custom paths
    python unify_burnmasks.py 2024 \
        --satellite Suomi-NPP --satellite NOAA-20 \
        --path /data/2024/Suomi-NPP --path /data/2024/NOAA-20
"""

import argparse
import os
import re
import sys

from dataclasses import dataclass
from importlib import resources
from typing import List, Optional, Tuple

import geopandas as gpd
import pandas as pd
import rasterio
import rioxarray as rxr
import xarray as xr


@dataclass
class Burnmask:
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

    def _unify_daily_burnmasks(self,date,method='majority'):
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

    def combine_burnmasks(
            self,method='majority',min_date=None,max_date=None,return_unified=False,
            verbose=False
            ):
        
        burnmasks_by_date = {}

        iter_dates = self.burnmask_dates
        
        if min_date is not None:
            iter_dates = [x for x in iter_dates if pd.to_datetime(x) >= pd.to_datetime(min_date)]
        if max_date is not None:
            iter_dates = [x for x in iter_dates if pd.to_datetime(x) <= pd.to_datetime(max_date)]
        
        for date in iter_dates:
            if verbose:
                print(f" - {date}")
            burnmasks_by_date[date] = self._unify_daily_burnmasks(date,method=method)

        self.burnmasks_by_date = burnmasks_by_date

        if return_unified:

            da = xr.concat(burnmasks_by_date.values(),dim='date').sum(dim='date')

            return da
    
def _get_filedate(file):
    return re.findall(r'\d{4}-\d{2}-\d{2}',file)[0]

def get_burn_area_by_county(burnmask_file,county_shp):
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

def build_parser() -> argparse.ArgumentParser:
    """
    Configure and return the argument parser.
    """
    import textwrap


    parser = argparse.ArgumentParser(
        prog="Unify Burnmasks",
        description=textwrap.dedent('''\
                                    
        Unify Satellite Burnmasks
        --------------------------------------------------------
                                    
            Unify daily burnmasks into a single annual burnmask 
            using burnmasks from instruments onboard one or more 
            satellites
                                    

        '''),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    
    # Required positional: year
    parser.add_argument(
        "year",
        type=int,
        help="Target year (e.g., 2026)",
    )

    # Repeatable option: --satellite (at least one required)
    parser.add_argument(
        "--satellite",
        dest="satellites",
        action="append",
        required=True,  # ensures satellite_1 is required
        help="Satellite name (repeat this flag for satellite_2 ... satellite_N)",
    )

    # Repeatable option: --path (optional, can be zero or more)
    parser.add_argument(
        "--path",
        dest="paths",
        action="append",
        default=None,
        help="Filesystem or cloud path (repeat for path_2 ... path_N)",
    )

    parser.add_argument(
        "--classification_methods",
        nargs="+",
        default=['eucl','rf','svm'],
        help="Strings representing different classification methods used to generate daily burnmasks.",
    )

    parser.add_argument(
        "--unify_method",
        type=str,
        default='majority',
        choices=['any','majority','unanimous']
    )

    parser.add_argument(
        "--county_shp",
        type=str,
    )
    
    parser.add_argument(
        "--output_path",
        default=str(os.getcwd()),
        type=str,
    )

    return parser

def validate_args(year: int, satellites: List[str], paths: Optional[List[str]]) -> None:
    """
    Perform basic validation.
    Raise ValueError or SystemExit on irrecoverable issues.
    """
    # Year sanity check (adjust to your domain constraints)
    if year < 1900 or year > 2100:
        raise ValueError(f"Invalid year: {year}. Expected range is 1900–2100.")

    # Satellite list should not be empty (enforced by argparse required=True)
    if not satellites:
        raise ValueError("At least one --satellite must be provided.")

    # Optional paths: allow none; if provided, basic checks can go here
    if paths:
        for p in paths:
            if not isinstance(p, str) or not p.strip():
                raise ValueError(f"Invalid path value: {p!r}")

    # Optional: warn if number of paths doesn't match satellites
    # (You may choose to enforce one-to-one mapping instead.)
    if paths and len(paths) != len(satellites):
        # This is just a warning in the skeleton; adjust behavior as needed.
        print(
            f"[warn] Number of paths ({len(paths)}) does not match number of satellites "
            f"({len(satellites)}). Paths will be paired where possible and others set to None.",
            file=sys.stderr,
        )

def pair_satellites_paths(
    satellites: List[str],
    paths: Optional[List[str]],
) -> List[Tuple[str, Optional[str]]]:
    """
    Pair satellites to paths by index; pad with None if paths are fewer.
    If no paths provided, all path entries will be None.
    """
    if not paths:
        return [(sat, None) for sat in satellites]

    pairs: List[Tuple[str, Optional[str]]] = []
    for i, sat in enumerate(satellites):
        path_i = paths[i] if i < len(paths) else None
        pairs.append((sat, path_i))
    return pairs


def main(argv: Optional[List[str]] = None):
    
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = args.paths if args.paths is not None else None

    print(f"{args.year = }")
    print(f"{args.classification_methods = }")
    print(f"{args.unify_method = }")
    print(f"{args.county_shp = }")

    try:
        validate_args(args.year, args.satellites, paths)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    # Read through each satellite and merge the burnmasks into a satellite-specific
    # annual burnmask
    da_objs = []
    for satellite in args.satellites:
        print(f"Analyzing daily burnmasks from Satellite: {satellite}")
        bm = Burnmask(
            year=args.year,
            satellite=satellite,
            classification_methods=args.classification_methods
            )
        
        bm.combine_burnmasks(method=args.unify_method)

        da = xr.concat(bm.burnmasks_by_date.values(),dim='date')
        da = da.assign_coords(date=('date',list(bm.burnmasks_by_date.keys())))
        da_objs.append(da)

    # Align using outer join so that the date coordinate for each dataarray is
    # expanded to be the union of dates from all dataarrays, with zero-filling.
    # this makes combining much simpler
    da_objs = xr.align(*da_objs,join='outer',fill_value=0)
    combined = xr.zeros_like(da_objs[0])

    for da in da_objs:
        combined = combined + da

    combined = combined.cumsum(dim='date')
    combined = (combined > 0).astype(int)

    # Write output burnmask to geotiff, copying geolocating etc. from 
    # one of the input burnmasks
    output_dir = os.path.join(args.output_path,f"{args.year}")
    os.makedirs(output_dir,exist_ok=True)
    reference_file = list(bm.burnmasks[bm.classification_methods[0]].values())[0]

    with rasterio.open(reference_file,"r") as src:
        profile = src.profile

        for ix,date in enumerate(combined.date.data):
            date = pd.to_datetime(date)

            output_file = os.path.join(
                output_dir,
                f"viirs_combined_{args.year}_YTD{date.strftime("%j")}.tif")
            
            output_array = combined.isel(date=ix).fillna(0)

            print(f"\n=== {date} ===")

            with rasterio.open(output_file,"w",**profile) as dst:
                dst.write(output_array.data,1)

            by_county = get_burn_area_by_county(output_file,args.county_shp)

            by_county = by_county[[x not in ['Shawnee County','Montgomery County','Total'] for x in by_county['county_name']]]
            by_county = by_county.sort_values(by=['state_name','county_name'])

            by_county.to_csv(output_file.replace(".tif",".csv"))

            # print(by_county[['county_name','state_name','burned_area_acres']])

            print(f"Total Burned Acres: {by_county['burned_area_acres'].sum()}")

    return

if __name__ == '__main__':
    main()




