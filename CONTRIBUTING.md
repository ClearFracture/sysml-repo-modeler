# Contributing to Project Analyzer

Thank you for your interest in contributing. This document describes how to set
up your environment, the standards we follow, and how to submit changes.

By participating in this project, you agree to abide by our
[Code of Conduct](https://github.com/ClearFracture/.github/blob/main/CODE_OF_CONDUCT.md).

## Getting Started

1. Fork the repository and clone your fork.
2. Follow [DEVELOPMENT.md](DEVELOPMENT.md) to set up a local environment and run
   the app from source.
3. Install the pre-commit hooks so checks run automatically on each commit:

   ```powershell
   python -m pip install -e ".[dev]"
   pre-commit install
   ```

## Development Workflow

1. Create a topic branch from `master`:

   ```powershell
   git checkout -b feature/short-description
   ```

2. Make your change in focused, logical commits with clear messages.
3. Run the checks described below before pushing.
4. Open a pull request against `master` and describe what changed and why.

## Coding Standards

- **Python** is formatted and linted with [Ruff](https://docs.astral.sh/ruff/).
- **Frontend** (TypeScript/React) is formatted with
  [Prettier](https://prettier.io/).
- Secrets are scanned with
  [detect-secrets](https://github.com/Yelp/detect-secrets); never commit
  credentials.

The pre-commit hooks enforce these standards. You can run them on demand:

```powershell
pre-commit run --all-files
```

## Running Checks

Before opening a pull request, make sure the relevant checks pass.

Python tests and linting:

```powershell
.\.venv\Scripts\Activate.ps1
pytest
ruff check .
```

Frontend formatting and build:

```powershell
cd src\ui
npm run format:check
npm run build
```

## Pull Requests

- Keep pull requests focused; smaller changes are easier to review and merge.
- Ensure all checks pass and the app builds.
- Update documentation when you change behavior, configuration, or setup steps.
- Link any related issues in the description.

## Reporting Issues

- For bugs and feature requests, open a
  [GitHub issue](https://github.com/ClearFracture/project-analyzer/issues) with enough
  detail to reproduce or understand the request.
