# FABDEM

Download FABDEM data: a DEM with forests and buildings removed using ML.

FABDEM homepage: https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn

## Installation

To install the package using *pip*
```shell
pip install fabdem
```

## Usage

FABDEM can be used either as a Python library or from the command line.

### Python library

Import the package and call `download()` with geographic bounds in EPSG:4326:

```python
import fabdem

bounds = (1, 30, 5, 35)
fabdem.download(bounds, output_path="dem.tif")
```

The bounds tuple is:

```python
(west, south, east, north)
```

Example:

```python
import fabdem

bounds = (35.35, -1.49, 36.48, -0.23)
fabdem.download(bounds, output_path="narok_dem.tif")
```

`output_path` may point to any raster format supported by GDAL, such as `.tif`.

#### Optional arguments

You can also control caching and progress display:

```python
import fabdem
from pathlib import Path

bounds = (35.35, -1.49, 36.48, -0.23)
fabdem.download(
    bounds,
    output_path="narok_dem.tif",
    cache=Path("./fabdem-cache"),
    show_progress=True,
)
```

Common keyword arguments:

- `bounds`: `(west, south, east, north)` in longitude/latitude.
- `output_path`: output raster path.
- `cache`: optional directory used to store downloaded ZIP archives and extracted tiles.
- `show_progress`: show progress output in terminal or notebook.

#### In notebooks

In Jupyter notebooks, progress output uses an `ipywidgets` widget to avoid flickering from repeated cell output refreshes. If `ipywidgets` is not available, the code falls back to plain text output and logs a warning.

### Command-line interface

The module can also be run directly from the command line.

Basic usage:

```shell
python fabdem.py WEST SOUTH EAST NORTH OUTPUT_PATH
```

Example:

```shell
python fabdem.py 35.35 -1.49 36.48 -0.23 narok_dem.tif
```

With optional arguments:

```shell
python fabdem.py 35.35 -1.49 36.48 -0.23 narok_dem.tif \
    --cache ./fabdem-cache \
    --log-level INFO
```

Available CLI options:

- `--cache PATH`: use a custom cache directory.
- `--hide-progress`: disable progress output.
- `--clear-cache`: clear the selected cache before downloading.
- `--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}`: configure logging verbosity.

You can also inspect the built-in help:

```shell
python fabdem.py --help
```

### Notes

- Coordinates must be given in EPSG:4326 longitude/latitude.
- The package automatically determines which FABDEM tiles intersect the requested bounds.
- Downloaded data may be reused from cache when available.
- The output raster is created by merging all intersecting tiles for the requested area.

## Development

### 1. Clone the repository
```shell
git clone https://github.com/jantomec/fabdem.git
cd fabdem
```

### 2. Create and activate an environment

#### 2.1 Using conda
```shell
conda env create -f fabdem.yml
conda activate fabdem
```
If the environment already exists, update it instead:
```shell
conda env update -f fabdem.yml --prune
conda activate fabdem
```

#### 2.2 Using venv on macOS or Linux
```shell
python -m venv .venv
source .venv/bin/activate
```
#### 2.3 Using venv on Windows
```shell
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install the package in editable mode
With flit:
```shell
flit install --symlink
```

### 4. Publish to PyPI:
```shell
flit publish
```

### TODO:
- [ ] Create a conda-forge package.
- [x] Download only part of a zip.
- [x] Add a CLI tool

### Resources:
- [Python Packaging](https://packaging.python.org/en/latest/overview/)
- [TOML Format](https://github.com/toml-lang/toml)
- [flit](https://flit.pypa.io/en/latest/)
- [PEP 8 - Naming Conventions](https://peps.python.org/pep-0008/#naming-conventions)
- [PEP 484 – Type Hints](https://peps.python.org/pep-0484/)
- [conda-forge](https://conda-forge.org/docs/maintainer/adding_pkgs/)