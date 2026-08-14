import textwrap
from datetime import datetime
from pathlib import Path
from typing import List
import pandas as pd

def bbox_is_valid(bbox):
    try:
        min_lon, min_lat, max_lon, max_lat = bbox
    except ValueError:
        msg = "Bounding Box must have four values [min_lon, min_lat, max_lon, max_lat]"
        return False, msg

    if not all(isinstance(x, (int,float)) for x in bbox):
        msg = "Bounding Box must consist of integer or float arguments"
        return False, msg

    if (max_lon > 180) or (min_lon <-180) or (max_lat > 90) or (min_lat < -90):
        msg = "Bounding Box be within -180<=LON<=180; -90<=LAT<=90"
        return False, msg

    return True, None

def validate_directory(project_dir):
    project_path = Path(project_dir)

    if not project_path.parent.exists():
        msg = "\n".join(textwrap.wrap(textwrap.dedent(
            f"""Parent directory `{project_path.parent}` does not exist. Ensure parent directory has been created.
        """)))
        return False, msg

    if project_path.exists():
        msg = "\n".join(textwrap.wrap(textwrap.dedent(
            f"""Project directory `{project_path}` already exists. Duplicates are not allowed.
        """)))
        return False, msg

    return True, ""

def get_valid_dates(year: str | int ) -> List[str]: 

    valid_min_date = pd.to_datetime(f"{year}-01-01")
    valid_max_date = pd.to_datetime(f"{year}-12-31")
    today = pd.to_datetime(datetime.today())

    if valid_max_date > today:
        valid_max_date = today

    return [x.strftime("%Y-%m-%d") for x in [valid_min_date,valid_max_date]]
        