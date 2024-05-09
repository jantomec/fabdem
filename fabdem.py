"""Download FABDEM data: a DEM with forests and buildings removed using ML."""

__version__ = "0.1.0"
__author__ = "Jan Tomec"


from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import requests
from geopandas import GeoDataFrame
import shapely
import shapely.geometry
from tqdm import tqdm
import rasterio, rasterio.merge
from pyproj import CRS


def __merge_rasters(output_path, tiles, bounds=None, crs=None):
    """Merge tiles.
    
    Parameters:
    - tiles (list): A list of pathlib.Path instances.
    - output_path (pathlib.Path): The path of the merged raster.
    - bounds (iterable or None): If not None, bounds must include 4 values which 
    are used to crop the merged raster. The order is (west, south, east, north).
    - crs (int, pyproj.CRS or None): Specify the input coordinate system or use 
    None to try and set it automatically based on the metadata of the tiles.
    """
    rasters = [rasterio.open(raster) for raster in tiles]
    merged_raster, merged_transform = rasterio.merge.merge(
        datasets=rasters,
        bounds=bounds
    )
    
    for raster in rasters:
        raster.close()

    with rasterio.open(tiles[0]) as raster:
        if raster.crs:
            source_crs = raster.crs
        else:
            source_crs = None

    if crs:
        if CRS(crs) != CRS(source_crs):
            raise ValueError("Input CRS does not match the one read from the rasters metadata.")
    else:
        if source_crs:
            crs = source_crs
        else:
            raise ValueError("No CRS is present in the rasters metadata. Specify one using crs parameter.")
        
    
    metadata = {
        'count': merged_raster.shape[0],
        'height': merged_raster.shape[1],
        'width': merged_raster.shape[2],
        'dtype': merged_raster.dtype,
        'crs': crs,
        'transform': merged_transform
    }
    
    with rasterio.open(output_path, mode='w', **metadata) as dest:
        dest.write(merged_raster)

def __download_file(url, destination_path, show_progress):
    # First, send a HEAD request to get the total size of the file
    response = requests.head(url)
    total_size = int(response.headers.get('content-length', 0))

    # Stream the download
    response = requests.get(url, stream=True)
    response.raise_for_status()

    # Open the file to write to
    with open(destination_path, "wb") as file:
        if show_progress:
            # Setup the progress bar if requested
            with tqdm(
                desc=destination_path.name,
                total=total_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
            ) as bar:
                for data in response.iter_content(chunk_size=1024):
                    size = file.write(data)
                    bar.update(size)
        else:
            # Download without progress bar
            for data in response.iter_content(chunk_size=1024):
                file.write(data)

def download(bounds, output_path, show_progress=True, cache=None):
    """Use this function to download FABDEM data as a raster image.

    Parameters:
    - bounds (tuple): coordinates (west, south, east, north) in EPSG:4326
    - output_path (str or pathlib.Path): output file
    - show_progress (bool): if True, then tqdm progress indicator is displayed
    - cache (str, pathlib.Path or None): FABDEM is stored online in large tiles,
    which need to be downloaded in their entirety. If you are unsure about the
    exact bounds you need it is thus advantageous to cache the downloaded files.
    To do so, pass a folder location as a cache parameter which is where the
    raw files will be stored. By default, None, the files are stored in the OS
    temporary folder and will be available until the temporary folder is
    cleaned - usually on OS restart.
    """
    # Convert output path to a pathlib.Path object
    output_path = Path(output_path)

    # Create a rectangle from bounds
    rect = shapely.geometry.box(*bounds)

    # FABDEM base url
    base_url = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn"

    # Download tiles info
    tiles_info_url = f"{base_url}/FABDEM_v1-2_tiles.geojson"
    response = requests.get(tiles_info_url)
    response.raise_for_status()
    
    tiles_gdf = GeoDataFrame.from_features(
        response.json()["features"],
        crs=4326
    )

    # Find tiles that intersect with the rect
    tiles_gdf["intersects"] = tiles_gdf.geometry.intersects(rect)
    
    # Filter to get only the tiles that intersect
    intersecting_tiles = tiles_gdf[tiles_gdf["intersects"]]

    # Download tiles
    with TemporaryDirectory(delete=False) as tmp:
        if cache:
            download_folder = cache
        else:
            download_folder = tmp
        
        for zipfile_name in set(intersecting_tiles.zipfile_name):
            # Fetch a tile
            tile_url = f"{base_url}/{zipfile_name}"
            zip_path = Path(download_folder) / zipfile_name
            if not zip_path.exists():
                __download_file(tile_url, zip_path, show_progress)
            elif zip_path.exists() and show_progress:
                print(f"{zip_path} loaded from cache")

            # Unzip its contents
            with ZipFile(zip_path, 'r') as zip_archive:
                zip_archive.extractall(download_folder)
        
        # File names in the FABDEM_v1-2_tiles.geojson do not match the actual
        # file names in the zip archive. North-south label has an extra zero.
        # If in the future this bug is corrected, simply remove this function.
        def correct_name(json_name):
            return json_name[0] + json_name[2:]
        
        tile_paths = [
            Path(download_folder) / correct_name(f) 
            for f in intersecting_tiles.file_name
        ]
        
        __merge_rasters(output_path, tile_paths, bounds)
