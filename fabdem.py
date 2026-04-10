"""Download FABDEM data: a DEM with forests and buildings removed using ML."""

__version__ = "0.2.0"
__author__ = "Jan Tomec"


import argparse
from pathlib import Path
import shutil
from tempfile import gettempdir
from zipfile import ZipFile
import logging
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import sys
import html
try:
    from IPython.display import display
except ImportError:
    display = None

try:
    import ipywidgets as widgets
except ImportError:
    widgets = None
import requests
from requests import Session
from geopandas import GeoDataFrame
import shapely
import shapely.geometry
import rasterio
import rasterio.merge
from pyproj import CRS


logger = logging.getLogger(__name__)

MAX_DOWNLOAD_WORKERS = 4
CACHE_FALLBACK_NAME = "fabdem-cache"
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_STATE = {
    "mode": None,
    "header_lines": [],
    "lines": {},
    "order": [],
    "widget": None,
    "rendered_line_count": 0,
}


def __format_progress_bar(completed, total, width=20):
    if total <= 0:
        return "[" + ("-" * width) + "]"

    filled = int(width * completed / total)
    filled = max(0, min(width, filled))
    return "[" + ("*" * filled) + ("-" * (width - filled)) + "]"


def __supports_live_terminal_updates():
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def __progress_mode():
    return "terminal" if __supports_live_terminal_updates() else "notebook"


def __reset_progress_display():
    with _PROGRESS_LOCK:
        _PROGRESS_STATE["mode"] = __progress_mode()
        _PROGRESS_STATE["header_lines"] = []
        _PROGRESS_STATE["lines"] = {}
        _PROGRESS_STATE["order"] = []
        _PROGRESS_STATE["widget"] = None
        _PROGRESS_STATE["rendered_line_count"] = 0


def __render_progress_display():
    mode = _PROGRESS_STATE["mode"] or __progress_mode()
    header_lines = list(_PROGRESS_STATE["header_lines"])
    progress_lines = [
        _PROGRESS_STATE["lines"][name]
        for name in _PROGRESS_STATE["order"]
        if name in _PROGRESS_STATE["lines"]
    ]
    rendered_lines = header_lines + progress_lines

    if mode == "terminal":
        previous_line_count = _PROGRESS_STATE["rendered_line_count"]
        if previous_line_count:
            print(f"\x1b[{previous_line_count}F", end="")

        for line in rendered_lines:
            print("\r\x1b[2K" + line)

        extra_lines = previous_line_count - len(rendered_lines)
        for _ in range(max(0, extra_lines)):
            print("\r\x1b[2K")

        if rendered_lines or previous_line_count:
            print("\r", end="", flush=True)

        _PROGRESS_STATE["rendered_line_count"] = len(rendered_lines)
    else:
        if widgets is None or display is None:
            logger.warning(
                "Notebook progress display requested but ipywidgets/IPython.display is not available; falling back to plain text output."
            )
            previous_line_count = _PROGRESS_STATE["rendered_line_count"]
            if previous_line_count:
                return
            for line in rendered_lines:
                print(line)
            _PROGRESS_STATE["rendered_line_count"] = len(rendered_lines)
            return

        widget = _PROGRESS_STATE["widget"]
        if widget is None:
            widget = widgets.HTML()
            _PROGRESS_STATE["widget"] = widget
            display(widget)

        escaped_lines = [html.escape(line) for line in rendered_lines]
        widget.value = "<pre style='margin: 0; white-space: pre-wrap;'>" + "\n".join(escaped_lines) + "</pre>"
        _PROGRESS_STATE["rendered_line_count"] = len(rendered_lines)


def __add_progress_header(line):
    with _PROGRESS_LOCK:
        if _PROGRESS_STATE["mode"] is None:
            _PROGRESS_STATE["mode"] = __progress_mode()
        _PROGRESS_STATE["header_lines"].append(line)
        __render_progress_display()


def __set_progress_line(name, completed, total):
    line = f"{name} {__format_progress_bar(completed, total)} {completed}/{total}"
    with _PROGRESS_LOCK:
        if _PROGRESS_STATE["mode"] is None:
            _PROGRESS_STATE["mode"] = __progress_mode()
        if name not in _PROGRESS_STATE["lines"]:
            _PROGRESS_STATE["order"].append(name)
        _PROGRESS_STATE["lines"][name] = line
        __render_progress_display()


def __finalize_progress_display():
    with _PROGRESS_LOCK:
        if _PROGRESS_STATE["mode"] == "terminal" and _PROGRESS_STATE["rendered_line_count"]:
            print()
        _PROGRESS_STATE["mode"] = None
        _PROGRESS_STATE["header_lines"] = []
        _PROGRESS_STATE["lines"] = {}
        _PROGRESS_STATE["order"] = []
        _PROGRESS_STATE["widget"] = None
        _PROGRESS_STATE["rendered_line_count"] = 0


def __make_progress_callback(name):
    return lambda completed, total: __set_progress_line(name, completed, total)


def _clear_cache(cache=None):
    """Delete all files in the cache folder.

    Parameters:
    - cache (str, pathlib.Path or None): Cache directory to clear. If None,
      the default OS temporary cache folder is used.
    """
    cache_dir = Path(cache) if cache is not None else Path(gettempdir()) / CACHE_FALLBACK_NAME
    logger.debug("Clearing cache folder: %s", cache_dir)
    if not cache_dir.exists():
        return

    for path in cache_dir.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


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
    logger.debug(
        "Merging %d raster tile(s) into %s with bounds=%s and crs=%s",
        len(tiles),
        output_path,
        bounds,
        crs,
    )
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


def __download_file(session, url, destination_path, show_progress):
    logger.debug("Downloading full file from %s to %s", url, destination_path)
    # First, send a HEAD request to get the total size of the file
    response = session.head(url)
    total_size = int(response.headers.get('content-length', 0))
    logger.debug("Full download size for %s: %d bytes", url, total_size)

    # Stream the download
    response = session.get(url, stream=True)
    response.raise_for_status()
    logger.debug("Started streaming full download from %s", url)

    completed = 0

    # Open the file to write to
    with open(destination_path, "wb") as file:
        for data in response.iter_content(chunk_size=1024 * 64):
            if not data:
                continue
            size = file.write(data)
            completed += size
            if show_progress:
                __set_progress_line(destination_path.name, completed, total_size)

    if show_progress:
        __finalize_progress_display()


def __head_file(session, url):
    response = session.head(url, allow_redirects=True)
    response.raise_for_status()
    logger.debug("HEAD %s returned headers: %s", url, dict(response.headers))
    file_size = int(response.headers.get("content-length", 0))
    accept_ranges = response.headers.get("accept-ranges", "")
    logger.debug(
        "Remote file info for %s: size=%d, supports_ranges=%s",
        url,
        file_size,
        "bytes" in accept_ranges.lower(),
    )
    return file_size, "bytes" in accept_ranges.lower()


def __download_byte_range(session, url, start, end, progress_callback=None):
    logger.debug("Requesting byte range %d-%d from %s", start, end, url)
    response = session.get(
        url,
        headers={"Range": f"bytes={start}-{end}"},
        stream=True,
    )
    response.raise_for_status()
    logger.debug(
        "Byte-range response for %s: status=%d, content-length=%s, content-range=%s",
        url,
        response.status_code,
        response.headers.get("content-length"),
        response.headers.get("content-range"),
    )

    if response.status_code != 206:
        raise ValueError("Server does not support HTTP byte-range requests for this file.")

    chunks = []
    total = end - start + 1
    completed = 0
    for chunk in response.iter_content(chunk_size=1024 * 64):
        if not chunk:
            continue
        chunks.append(chunk)
        completed += len(chunk)
        if progress_callback is not None:
            progress_callback(completed, total)

    return b"".join(chunks)


def __find_zip_eocd(tail_bytes):
    signature = b"PK\x05\x06"
    index = tail_bytes.rfind(signature)
    if index == -1:
        raise ValueError("ZIP end of central directory record not found.")
    logger.debug("Found ZIP EOCD at offset %d within tail buffer", index)

    return index


def __read_remote_zip_index(session, url):
    logger.debug("Reading remote ZIP index from %s", url)
    file_size, supports_ranges = __head_file(session, url)
    if not file_size:
        raise ValueError("Could not determine remote ZIP file size.")
    if not supports_ranges:
        raise ValueError("Server does not advertise HTTP byte-range support.")

    tail_size = min(file_size, 131072)
    tail_start = file_size - tail_size
    tail_bytes = __download_byte_range(session, url, tail_start, file_size - 1)
    logger.debug(
        "Downloaded ZIP tail for %s: start=%d, end=%d, bytes=%d",
        url,
        tail_start,
        file_size - 1,
        len(tail_bytes),
    )

    eocd_offset = __find_zip_eocd(tail_bytes)
    eocd = tail_bytes[eocd_offset:eocd_offset + 22]
    (
        _signature,
        disk_number,
        central_dir_disk_number,
        disk_entries,
        total_entries,
        central_dir_size,
        central_dir_offset,
        _comment_length,
    ) = struct.unpack("<4s4H2LH", eocd)
    logger.debug(
        "ZIP EOCD for %s: entries=%d, central_dir_size=%d, central_dir_offset=%d",
        url,
        total_entries,
        central_dir_size,
        central_dir_offset,
    )

    if disk_number != 0 or central_dir_disk_number != 0:
        raise ValueError("Multi-disk ZIP archives are not supported.")
    if disk_entries != total_entries:
        raise ValueError("ZIP archive spans multiple disks and is not supported.")
    if central_dir_offset == 0xFFFFFFFF or central_dir_size == 0xFFFFFFFF:
        raise ValueError("ZIP64 archives are not supported.")

    central_directory = __download_byte_range(
        session,
        url,
        central_dir_offset,
        central_dir_offset + central_dir_size - 1,
    )
    logger.debug(
        "Downloaded central directory for %s: bytes=%d",
        url,
        len(central_directory),
    )

    entries = {}
    offset = 0
    while offset < len(central_directory):
        if central_directory[offset:offset + 4] != b"PK\x01\x02":
            raise ValueError("Invalid ZIP central directory entry.")

        header = central_directory[offset:offset + 46]
        (
            _signature,
            _version_made_by,
            _version_needed,
            flags,
            compression_method,
            _mod_time,
            _mod_date,
            crc32,
            compressed_size,
            uncompressed_size,
            filename_length,
            extra_length,
            comment_length,
            _disk_start,
            _internal_attributes,
            _external_attributes,
            local_header_offset,
        ) = struct.unpack("<4s6H3L5H2L", header)

        filename_start = offset + 46
        filename_end = filename_start + filename_length
        extra_end = filename_end + extra_length
        comment_end = extra_end + comment_length

        filename = central_directory[filename_start:filename_end].decode("utf-8")
        entries[filename] = {
            "flags": flags,
            "compression_method": compression_method,
            "crc32": crc32,
            "compressed_size": compressed_size,
            "uncompressed_size": uncompressed_size,
            "local_header_offset": local_header_offset,
        }
        logger.debug(
            "Indexed ZIP member %s: compressed=%d, uncompressed=%d, local_header_offset=%d, compression=%d",
            filename,
            compressed_size,
            uncompressed_size,
            local_header_offset,
            compression_method,
        )

        offset = comment_end

    logger.debug("Indexed %d ZIP member(s) from %s", len(entries), url)
    return entries


def __extract_remote_zip_member(session, url, entries, member_name, destination_path, progress_callback=None):
    logger.debug("Extracting remote ZIP member %s from %s to %s", member_name, url, destination_path)
    if member_name not in entries:
        raise FileNotFoundError(f"ZIP member not found: {member_name}")

    entry = entries[member_name]
    logger.debug("ZIP member metadata for %s: %s", member_name, entry)
    local_header_offset = entry["local_header_offset"]
    local_header = __download_byte_range(session, url, local_header_offset, local_header_offset + 29)
    logger.debug("Downloaded local ZIP header for %s: %d bytes", member_name, len(local_header))

    if local_header[:4] != b"PK\x03\x04":
        raise ValueError("Invalid ZIP local file header.")

    (
        _signature,
        _version_needed,
        _flags,
        compression_method,
        _mod_time,
        _mod_date,
        _crc32,
        _compressed_size,
        _uncompressed_size,
        filename_length,
        extra_length,
    ) = struct.unpack("<4s5H3L2H", local_header)

    data_start = local_header_offset + 30 + filename_length + extra_length
    data_end = data_start + entry["compressed_size"] - 1
    compressed_data = __download_byte_range(
        session,
        url,
        data_start,
        data_end,
        progress_callback=progress_callback,
    )
    logger.debug(
        "Downloaded member payload for %s: compressed_bytes=%d, expected=%d",
        member_name,
        len(compressed_data),
        entry["compressed_size"],
    )

    if compression_method == 0:
        data = compressed_data
    elif compression_method == 8:
        data = zlib.decompress(compressed_data, -zlib.MAX_WBITS)
    else:
        raise ValueError(f"Unsupported ZIP compression method: {compression_method}")

    logger.debug(
        "Extracted member %s using compression method %d to %d bytes",
        member_name,
        compression_method,
        len(data),
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with open(destination_path, "wb") as file:
        file.write(data)
    logger.debug("Wrote extracted member %s to %s", member_name, destination_path)


def __download_selected_zip_members(session, url, entries, member_names, download_folder, show_progress):
    logger.debug("Selecting %d ZIP member(s) from %s", len(member_names), url)

    missing_members = []
    for member_name in member_names:
        destination_path = Path(download_folder) / Path(member_name).name
        if destination_path.exists():
            logger.debug("ZIP member %s already cached at %s", member_name, destination_path)
            if show_progress:
                __add_progress_header(f"{destination_path} loaded from cache")
            continue
        missing_members.append(member_name)

    if show_progress and missing_members:
        __add_progress_header(f"Selecting members from {Path(url).name}")

    use_parallel = len(missing_members) > 1
    if use_parallel:
        workers = min(MAX_DOWNLOAD_WORKERS, len(missing_members))
        logger.debug(
            "Downloading %d ZIP member(s) from %s in parallel with %d worker(s)",
            len(missing_members),
            url,
            workers,
        )

        progress_callbacks = {}
        if show_progress:
            for member_name in missing_members:
                total = entries[member_name]["compressed_size"]
                __set_progress_line(member_name, 0, total)
                progress_callbacks[member_name] = __make_progress_callback(member_name)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for member_name in missing_members:
                destination_path = Path(download_folder) / Path(member_name).name
                logger.debug("Queueing ZIP member %s into %s", member_name, destination_path)
                future = executor.submit(
                    __download_selected_zip_member_task,
                    url,
                    entries,
                    member_name,
                    destination_path,
                    progress_callbacks.get(member_name),
                )
                futures[future] = member_name

            for future in as_completed(futures):
                member_name = futures[future]
                future.result()
                logger.debug("Completed ZIP member %s", member_name)

        if show_progress and missing_members:
            __finalize_progress_display()
    else:
        for member_name in missing_members:
            destination_path = Path(download_folder) / Path(member_name).name
            logger.debug("Downloading ZIP member %s into %s", member_name, destination_path)
            if show_progress:
                __set_progress_line(member_name, 0, entries[member_name]["compressed_size"])
            __extract_remote_zip_member(
                session,
                url,
                entries,
                member_name,
                destination_path,
                progress_callback=__make_progress_callback(member_name) if show_progress else None,
            )
            if show_progress:
                __finalize_progress_display()


def __download_selected_zip_member_task(url, entries, member_name, destination_path, progress_callback=None):
    logger.debug("Starting parallel download task for %s", member_name)
    with Session() as session:
        __extract_remote_zip_member(
            session,
            url,
            entries,
            member_name,
            destination_path,
            progress_callback=progress_callback,
        )
    logger.debug("Finished parallel download task for %s", member_name)


def download(bounds, output_path, show_progress=True, cache=None):
    """Use this function to download FABDEM data as a raster image.

    Parameters:
    - bounds (tuple): coordinates (west, south, east, north) in EPSG:4326
    - output_path (str or pathlib.Path): output file
    - show_progress (bool): if True, then tqdm progress indicator is displayed
    - cache (str, pathlib.Path or None): Folder used to cache downloaded tile
    files between calls. When provided, extracted TIFF tiles and any fallback
    ZIP downloads are stored there and reused on subsequent calls. By default,
    None, a persistent cache directory named by CACHE_FALLBACK_NAME in the OS
    temporary folder is used.
    """
    # Convert output path to a pathlib.Path object
    output_path = Path(output_path)
    logger.debug(
        "Starting FABDEM download with bounds=%s, output_path=%s, show_progress=%s, cache=%s",
        bounds,
        output_path,
        show_progress,
        cache,
    )

    __reset_progress_display()

    # Create a rectangle from bounds
    rect = shapely.geometry.box(*bounds)
    logger.debug("Constructed request bounds geometry: %s", rect)

    # FABDEM base url
    base_url = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn"

    with Session() as session:
        zip_index_cache = {}

        # Download tiles info
        tiles_info_url = f"{base_url}/FABDEM_v1-2_tiles.geojson"
        response = session.get(tiles_info_url)
        response.raise_for_status()
        logger.debug("Downloaded tiles index from %s", tiles_info_url)

        tiles_gdf = GeoDataFrame.from_features(
            response.json()["features"],
            crs=4326
        )
        logger.debug("Loaded %d tile definitions from GeoJSON", len(tiles_gdf))

        # Find tiles that intersect with the rect
        tiles_gdf["intersects"] = tiles_gdf.geometry.intersects(rect)

        # Filter to get only the tiles that intersect
        intersecting_tiles = tiles_gdf[tiles_gdf["intersects"]]
        logger.debug("Found %d intersecting tile(s)", len(intersecting_tiles))

        def run_download(download_folder):
            download_folder = Path(download_folder)
            download_folder.mkdir(parents=True, exist_ok=True)
            logger.debug("Using download folder: %s", download_folder)

            # File names in the FABDEM_v1-2_tiles.geojson do not match the actual
            # file names in the zip archive. North-south label has an extra zero.
            # If in the future this bug is corrected, simply remove this function.
            def correct_name(json_name):
                corrected = json_name[0] + json_name[2:]
                logger.debug("Corrected tile name from %s to %s", json_name, corrected)
                return corrected

            grouped_tiles = {}
            for row in intersecting_tiles.itertuples():
                zipfile_name = row.zipfile_name.replace("S-", "S").replace("N-", "N")
                grouped_tiles.setdefault(zipfile_name, []).append(correct_name(row.file_name))
                logger.debug(
                    "Mapped requested tile %s to ZIP %s",
                    row.file_name,
                    zipfile_name,
                )

            logger.debug("Grouped intersecting tiles into %d ZIP archive(s)", len(grouped_tiles))
            if show_progress:
                __add_progress_header(f"Found {len(intersecting_tiles)} intersecting tile(s) across {len(grouped_tiles)} ZIP archive(s)")

            for zipfile_name, member_names in grouped_tiles.items():
                tile_url = f"{base_url}/{zipfile_name}"
                logger.debug(
                    "Processing ZIP %s with %d requested member(s): %s",
                    zipfile_name,
                    len(member_names),
                    member_names,
                )
                if show_progress:
                    __add_progress_header(f"ZIP {zipfile_name}: {len(member_names)} requested tile(s)")

                member_paths = [download_folder / member_name for member_name in member_names]
                if all(path.exists() for path in member_paths):
                    logger.debug(
                        "All requested members for %s are already cached: %s",
                        zipfile_name,
                        member_paths,
                    )
                    if show_progress:
                        __add_progress_header(f"{zipfile_name} loaded from cache")
                        for path in member_paths:
                            __add_progress_header(f"{path} loaded from cache")
                    continue

                if tile_url in zip_index_cache:
                    entries = zip_index_cache[tile_url]
                    logger.debug("Using cached ZIP index for %s", tile_url)
                else:
                    entries = __read_remote_zip_index(session, tile_url)
                    zip_index_cache[tile_url] = entries
                    logger.debug("Cached ZIP index for %s", tile_url)

                try:
                    __download_selected_zip_members(
                        session,
                        tile_url,
                        entries,
                        member_names,
                        download_folder,
                        show_progress,
                    )
                except Exception as exc:
                    logger.debug(
                        "Falling back to full ZIP download for %s because partial extraction failed: %s",
                        zipfile_name,
                        exc,
                        exc_info=True,
                    )
                    zip_path = download_folder / zipfile_name
                    if not zip_path.exists():
                        __download_file(session, tile_url, zip_path, show_progress)
                    elif show_progress:
                        __add_progress_header(f"{zip_path} loaded from cache")

                    with ZipFile(zip_path, "r") as zip_archive:
                        for member_name in member_names:
                            destination_path = download_folder / member_name
                            if destination_path.exists():
                                logger.debug("ZIP member %s already cached at %s", member_name, destination_path)
                                continue
                            logger.debug("Extracting %s from cached ZIP %s", member_name, zip_path)
                            zip_archive.extract(member_name, download_folder)

            tile_paths = [
                download_folder / correct_name(f)
                for f in intersecting_tiles.file_name
            ]
            logger.debug("Prepared tile paths for merge: %s", tile_paths)

            __merge_rasters(output_path, tile_paths, bounds)
            logger.debug("Finished FABDEM download into %s", output_path)
            if show_progress:
                output_path_resolved = output_path.resolve()
                __add_progress_header(f"Created output raster: {output_path_resolved}")
                __finalize_progress_display()

        # Download tiles
        if cache is None:
            default_cache = Path(gettempdir()) / CACHE_FALLBACK_NAME
            logger.debug("No cache directory provided, using OS temp cache folder: %s", default_cache)
            if show_progress:
                __add_progress_header(f"Using cache folder: {default_cache}")
            run_download(default_cache)
        else:
            if show_progress:
                __add_progress_header(f"Using cache folder: {Path(cache)}")
            run_download(cache)


def _parse_cli_args():
    parser = argparse.ArgumentParser(
        description="Download FABDEM data and merge intersecting tiles into a raster output file."
    )
    parser.add_argument(
        "west",
        type=float,
        help="Western longitude of the requested bounds in EPSG:4326.",
    )
    parser.add_argument(
        "south",
        type=float,
        help="Southern latitude of the requested bounds in EPSG:4326.",
    )
    parser.add_argument(
        "east",
        type=float,
        help="Eastern longitude of the requested bounds in EPSG:4326.",
    )
    parser.add_argument(
        "north",
        type=float,
        help="Northern latitude of the requested bounds in EPSG:4326.",
    )
    parser.add_argument(
        "output_path",
        type=Path,
        help="Path of the output raster file.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Optional cache directory for downloaded ZIPs and extracted tiles.",
    )
    parser.add_argument(
        "--hide-progress",
        action="store_true",
        help="Disable progress output.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the selected cache directory before downloading.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Configure logging verbosity.",
    )
    return parser.parse_args()


def _main():
    args = _parse_cli_args()
    logging.basicConfig(level=getattr(logging, args.log_level))

    bounds = (args.west, args.south, args.east, args.north)
    cache = args.cache
    if args.clear_cache:
        _clear_cache(cache)

    download(
        bounds=bounds,
        output_path=args.output_path,
        show_progress=not args.hide_progress,
        cache=cache,
    )


if __name__ == "__main__":
    _main()
