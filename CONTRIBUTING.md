# Contributing to Manifest Python SDK

Thanks for your interest in contributing to the Manifest Python SDK!

## Prerequisites

- Python 3.10+
- pip and virtual environment

## Getting Started

1. Fork and clone the repository:

```bash
git clone https://github.com/<your-username>/manifest-python.git
cd manifest-python
```

2. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

## Testing

Run the test suite:

```bash
pytest -q
```

To run live tests against a local Manifest app:

```bash
MNFST_TEST_APP_URL=http://127.0.0.1:5310 pytest -q tests/test_live_app.py
```

## Making Changes

1. Create a branch from `main` for your change
2. Make your changes
3. Run tests to make sure everything passes
4. Write clear commit messages using conventional commits (e.g., `feat:`, `fix:`, `docs:`)
5. Open a pull request against `main`

## Commit Messages

Use conventional commit titles:
- `feat:` for new features (prepares a minor version)
- `fix:` for bug fixes (prepares a patch version)
- `docs:` for documentation changes
- `!` or `BREAKING CHANGE:` for breaking changes (prepares a major version)

## Code Style

The SDK follows PEP 8 and uses type hints throughout. Format code with Black and lint with Pylint.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
