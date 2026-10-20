# First Time Setup

### 1. Copy the Repository

With __[git](https://git-scm.com/)__ installed, you can clone the repository with 

```shell
git clone https://github.com/needham-michael/fhba.git
```

This will take a few minutes while the application downloads the landcover mask file.

### 2. Python environment setup

> For ease of use, this project relies on `uv` for python environment management. See the `uv` User's Guide for __[Installation Instructions](https://docs.astral.sh/uv/getting-started/installation/#installing-uv)__ via `pip`.

Run `uv sync` to install the necessary third-party packages into a `.venv` within the project directory:

```shell
# Clean the cache to avoid stale link issues
uv cache clean

# From the same directory with the uv.lock and pyproject.toml file...
uv sync
```

### 3. Launch the application

Launch the panel application

```shell
uv run panel serve app.py --show
```

Including the `--show` flag will cause the application to open in the default web browser