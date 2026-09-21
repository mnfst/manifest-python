# Contributing to Manifest Python SDK

Thanks for your interest in contributing to the Manifest Python SDK!

## Prerequisites

- Python 3.10+
- pip

## Getting Started

1. Fork and clone the repository:

```bash
git clone https://github.com/<your-username>/manifest-python.git
cd manifest-python
pip install -e '.[dev]'
```

## Development

```bash
pytest -q       # Run tests
python -m pip wheel --no-deps . --wheel-dir dist   # Build the wheel

# Optional: point only at a disposable app (creates a test customer/project).
MNFST_TEST_APP_URL=http://127.0.0.1:5310 pytest -q tests/test_live_app.py
```

CI runs the suite on Python 3.10, 3.13 and 3.14 and builds the wheel. The live
app test runs locally because the app repository is private.

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

GitHub keeps one rolling `chore: release …` pull request; PyPI publishing starts
only when that release pull request is merged.

## Supported Platforms

The SDK works with:
- `httpx` and `httpx2`, sync and async
- `requests`
- Python 3.10 through 3.14
- `mnfst run <command>` or a `manifest()` call at startup

Custom transports, `aiohttp` and other clients are not covered. See
[the coverage details](docs/guide.md).

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
