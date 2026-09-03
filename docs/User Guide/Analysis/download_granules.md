# Downloading Granules

The user first selects a `Year` and `Satellite` from the dropdowns before clicking the `Next` button.

## Download Preview Images

On the next page, a date range slider allows the user to select the starting and ending dates from which to download preview images. Clicking the `Download Preview Images` button queries NASA worldview for each date within the date range. 

Once all images have been downloaded, click the `Next` button.

## Classify Preview Images

The next page shows a slider which allows the user to quickly flip through the images downloaded on the previous page. For each image the user should categorize the image as `Fully Clear`, `Mostly Clear`, `Mostly Cloudy`, or `Fully Cloudy`.

Once all images have been categorized, click the `Save User Categorization` button and proceed to the next page by clicking the `Next` button.

!!! note
    The __`Save User Categorization`__ button does not need to be clicked for each individual date, but can be clicked after all images have been categorized.

## Download Granules

The final page of this section provides the user with a location to enter their username and password for the NASA Earthdata service (Register for free __[here](https://urs.earthdata.nasa.gov/users/new)__), and to login by clicking the __`Authenticate Earthdata`__ button.

Once authenticated, select which of the categories (`Fully Clear`, `Mostly Clear`, `Mostly Cloudy`, `Fully Cloudy` or `Uncategorized`) should be selected to download raw satellite granules. The user can optionally specify a daterange for downloading the granules by use of the __Download Range__ dropdown calendar.

Click the __`Download Granules`__ button to begin the data download, and follow-along in the __Download Log__.

!!! note
    Downloading granules across many dates can take some time, perhaps 1-2 minutes per date.

Once the download is complete, proceed to __[Processing Granules](./process_granules.md)__ by clicking the `2. Process Granules` tab near the top of the screen.