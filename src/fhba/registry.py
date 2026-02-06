"""Registry classes to manage satellite granule metadata and download/processing status."""

import importlib
import inspect
import json
import os
import tempfile
import yaml

import earthaccess
import pandas as pd

class GranuleManager:
    """Maintain status of granule downloads, file QC, and processing."""
    def __init__(self,satellite_name=None,short_name_list=None,start_date=None,end_date=None,raw_data_dir=None,processed_data_dir=None,
                 min_lat=None,min_lon=None,max_lat=None,max_lon=None,spatial_name=None):
        
        self.satellite_name = satellite_name
        self.short_name_list = short_name_list if short_name_list is not None else []
        self.start_date = start_date
        self.end_date = end_date
        self.raw_data_dir = raw_data_dir
        self.processed_data_dir = processed_data_dir
        self.min_lat = min_lat
        self.min_lon = min_lon
        self.max_lat = max_lat
        self.max_lon = max_lon
        self.spatial_name = spatial_name

        # Define dictionaries to maintain status of satellite granules by date at 
        # various workflow stages.
        if self.start_date is not None and self.end_date is not None:
            date_range = pd.date_range(start=self.start_date,end=self.end_date,freq='D').strftime("%Y-%m-%d").tolist()
            self.download_status = {d:False for d in date_range}
            self.qc_status = {d:-1 for d in date_range}
            self.processing_status = {d:False for d in date_range}

        self.raw_granules_by_date = {}
        self.processed_granules_by_date = {}


    def update_download_status(self,date,status):
        """Update the download status for a given date."""
        self.download_status[date] = status

    def update_qc_status(self,date,status):
        """Update the quality control status for a given date."""
        self.qc_status[date] = status

    def update_processing_status(self,date,status):
        """Update the processing status for a given date."""
        self.processing_status[date] = status

    def download_granules(self,date,day_night_flag='day',outdir=None,clobber=False):
        """Download granules for the satellite within the specified temporal and spatial bounds."""
        
        earthaccess.login()

        spatial = (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

        if date not in self.download_status:
            raise ValueError(f"Date {date} is outside the defined date range for this GranuleManager of {self.start_date} to {self.end_date}.")

        if date in self.raw_granules_by_date and not clobber:
            print(f"Granules for date {date} already downloaded. Use clobber=True to re-download.")
            return self.raw_granules_by_date[date]
        
        granule_search_results = []
        for short_name in self.short_name_list:
            granule_search_results.extend(
                earthaccess.search_data(
                    short_name=short_name,
                    bounding_box=spatial,
                    temporal=(date,date),
                    day_night_flag=day_night_flag
                )
            )

        print(f"Found {len(granule_search_results)} granules for {self.satellite_name} on {date}.")

        granule_files = earthaccess.download(
            granule_search_results, 
            local_path=self.raw_data_dir if outdir is None else outdir
            )
        
        # Convert from Path objects to strings for JSON serialization
        self.raw_granules_by_date[date] = [str(f) for f in granule_files]
        self.download_status[date] = True

    def to_dict(self):
        """Convert the granule manager to a dictionary representation."""
        return {k:v for k,v in inspect.getmembers(self) if not k.startswith('_') and not inspect.ismethod(v)}
    
    def from_dict(self, data):
        for k in data:
            setattr(self, k, data[k])
        return self
    
    def __str__(self):
        class_str = f"GranuleManager for {self.satellite_name}\n > Product short names: {self.short_name_list}"
        return class_str

class GranuleRegistry:
    """"""
    def __init__(self,data_year=None,start_month=None,start_day=None,end_month=None,end_day=None,raw_data_dir=None,processed_data_dir=None,min_lat=None,min_lon=None,max_lat=None,max_lon=None,spatial_name=None):

        self.data_year = data_year
        self.start_month = start_month
        self.start_day = start_day
        self.end_month = end_month
        self.end_day = end_day
        self.raw_data_dir = raw_data_dir
        self.processed_data_dir = processed_data_dir
        self.min_lat = min_lat
        self.min_lon = min_lon
        self.max_lat = max_lat
        self.max_lon = max_lon
        self.spatial_name = spatial_name

        self.satellites = {}

    def add_satellite(self, satellite_name, short_name_list=None):

        """Add a satellite to the registry."""
        if satellite_name not in self.satellites:
            self.satellites[satellite_name] = GranuleManager(
                satellite_name, 
                short_name_list,
                start_date=f"{self.data_year}-{self.start_month:02d}-{self.start_day:02d}",
                end_date=f"{self.data_year}-{self.end_month:02d}-{self.end_day:02d}",
                raw_data_dir=self.raw_data_dir + "/"+satellite_name,
                processed_data_dir=self.processed_data_dir + "/"+satellite_name,
                min_lat=self.min_lat,
                min_lon=self.min_lon,
                max_lat=self.max_lat,
                max_lon=self.max_lon,
                spatial_name=self.spatial_name
                )
        else:
            print(f"Satellite {satellite_name} already exists in the registry.")

    def to_dict(self):
        """Convert the granule registry to a dictionary representation."""
        dict_repr = {k:v for k,v in inspect.getmembers(self) if not k.startswith('_') and not inspect.ismethod(v) and not k=='satellites'}
        dict_repr['satellites'] = {sat: self.satellites[sat].to_dict() for sat in self.satellites}
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
        class_str = f"GranuleRegistry for {self.data_year}"
        class_str += f"\n > Date Bounds: {self.start_month}/{self.start_day} to {self.end_month}/{self.end_day}"
        class_str += f"\n > Satellites: {list(self.satellites.keys())}"
        return class_str
    
        
class Registry:
    def __init__(self):

        self.granule_registry = {}

        self.read_config()
    
    def add_granule_registry(self, data_year):
        """Add a granule registry for a specific data year."""
        if data_year not in self.granule_registry:
            self.__setitem__(data_year, GranuleRegistry(
                data_year=data_year,
                start_month=self.start_month,
                start_day=self.start_day,
                end_month=self.end_month,
                end_day=self.end_day,
                raw_data_dir=self.raw_data_dir+"/"+str(data_year),
                processed_data_dir=self.processed_data_dir+"/"+str(data_year),
                min_lat=self.min_lat,
                min_lon=self.min_lon,
                max_lat=self.max_lat,
                max_lon=self.max_lon,
                spatial_name=self.spatial_name
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
    
    def read_config(self):
        """Reads the asset registry configuration from a file."""
        config_file = importlib.resources.files('fhba') / 'config.yaml'

        try:
            with open(config_file) as f:
                config = yaml.safe_load(f)
                self.raw_data_dir = config.get('raw_data_dir', None)
                self.processed_data_dir = config.get('processed_data_dir', None)
                self.start_month = config.get('start_month', None)
                self.start_day = config.get('start_day', None)
                self.end_month = config.get('end_month', None)
                self.end_day = config.get('end_day', None)
                self.min_lat = config.get('min_lat', None)
                self.min_lon = config.get('min_lon', None)
                self.max_lat = config.get('max_lat', None)
                self.max_lon = config.get('max_lon', None)
                self.spatial_name = config.get('spatial_name', None)

        except FileNotFoundError as e:
            msg = f"Configuration file {config_file} not found. Please ensure that 'config.yaml' is located at the root of the 'fhba' package and contains the necessary directory paths."
            raise FileNotFoundError(msg) from e
        except yaml.YAMLError as e:
            msg = f"Error parsing the configuration file {config_file}. Please ensure that 'config.yaml' is properly formatted and contains valid YAML syntax."
            raise yaml.YAMLError(msg) from e
        
    def to_dict(self):
        """Convert the registry to a dictionary representation."""
        dict_repr = {k:v for k,v in inspect.getmembers(self) if not k.startswith('_') and not inspect.ismethod(v) and not k=='granule_registry'}
        dict_repr['granule_registry'] = {year: self.granule_registry[year].to_dict() for year in self.granule_registry}
        return dict_repr
    
    def from_dict(self, data):
        for k in data:
            if k != 'granule_registry':
                setattr(self, k, data[k])
        
        self.granule_registry = {year: GranuleRegistry().from_dict(data['granule_registry'][year]) for year in data['granule_registry']}
        return self
    
    def save_json(self,json_file=importlib.resources.files('fhba') / 'app' / 'state' / 'registry.json'):
        """Save the registry to a JSON file."""
        data = self.to_dict()
        
        json_file.parent.mkdir(parents=True,exist_ok=True)

        with tempfile.NamedTemporaryFile('w', dir=json_file.parent, delete=False, suffix='PENDING_.json', encoding='utf-8') as tmpfile:
            with open(tmpfile.name, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        os.replace(tmpfile.name, json_file)

    def load_json(self,json_file=importlib.resources.files('fhba') / 'app' / 'state' / 'registry.json'):
        """Load the registry from a JSON file."""
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
                class_str += f"\n - {data_year}: satellites: {list(granule_registry.satellites.keys())}"
        else:
            class_str += "\n - Add granule registries using add_granule_registry(data_year) method."
        return class_str
        