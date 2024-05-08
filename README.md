# FABDEM

Download FABDEM data: a DEM with forests and buildings removed using ML.

FABDEM homepage: https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn

## Installation

To install the package using pip
```shell
pip install fabdem
```

## Usage

Define coordinates bounding the area of interest:
```python
bounds = (1, 30, 5, 35)
```
Call the download function to create a raster:
```python
import fabdem
fabdem.download(bounds, output_path="dem.tif")
```
Supports any raster format supported by GDAL.

## Development

To install the package locally for development, run:
```shell
flit install --symlink
```
`--symlink` option tells flit to create a symbolic link to your package directory inside the site-packages directory of your environment instead of copying files. This is useful for development, as changes in your package directory immediately affect the installed package without needing reinstallation.

Run this command to upload the code to PyPI:
```shell
flit publish
```

### TODO:
[ ] Create a conda package.
[ ] Download only part of a zip.

### Resources:
- [Python Packaging](https://packaging.python.org/en/latest/overview/)
- [TOML Format](https://github.com/toml-lang/toml)
- [flit](https://flit.pypa.io/en/latest/)
- [PEP 8 - Naming Conventions](https://peps.python.org/pep-0008/#naming-conventions)
- [PEP 484 – Type Hints](https://peps.python.org/pep-0484/)