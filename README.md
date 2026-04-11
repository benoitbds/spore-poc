# SPORE

Système de Production d'Opportunités de Recherche par Exploration

## Installation

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

## Usage

```bash
# Run the pipeline
python -m spore run --collisions 50 --domain materials_science

# Bootstrap calibration test
python -m spore bootstrap

# Review interface
python -m spore review
```
