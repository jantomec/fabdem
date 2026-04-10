# FABDEM

Download FABDEM data: a DEM with forests and buildings removed using ML.

FABDEM homepage: https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn

## Installation

To install the package using *pip*
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
- [ ] Download only part of a zip.

### Resources:
- [Python Packaging](https://packaging.python.org/en/latest/overview/)
- [TOML Format](https://github.com/toml-lang/toml)
- [flit](https://flit.pypa.io/en/latest/)
- [PEP 8 - Naming Conventions](https://peps.python.org/pep-0008/#naming-conventions)
- [PEP 484 – Type Hints](https://peps.python.org/pep-0484/)
- [conda-forge](https://conda-forge.org/docs/maintainer/adding_pkgs/)