# Contributing

Thank you for your interest in contributing!

## Development Setup

1. Install [pixi](https://pixi.sh) and [gh](https://github.com/cli/cli#installation).
1. Clone the repository.
1. Set up the development environment:

```console
pixi install -e dev
pixi install -e typecheck
```

1. Install pre-commit hooks:

```console
pixi r pcupdate  # optionally bump hooks to latest versions first
pixi r lint      # installs hooks and runs them across the repo
```

## Commit Messages

This project uses
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). All commit
messages **must** follow this format:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Types

| Type       | When to use                            | Changelog section |
| ---------- | -------------------------------------- | ----------------- |
| `feat`     | A new feature                          | Added             |
| `fix`      | A bug fix                              | Fixed             |
| `perf`     | A performance improvement              | Changed           |
| `refactor` | Code restructuring, no behavior change | Changed           |
| `revert`   | Reverting a previous commit            | Fixed             |
| `docs`     | Documentation only                     | _(skipped)_       |
| `test`     | Adding or updating tests               | _(skipped)_       |
| `chore`    | Maintenance, dependencies, tooling     | _(skipped)_       |
| `ci`       | CI/CD changes                          | _(skipped)_       |

### Breaking Changes

Append `!` after the type, or add `BREAKING CHANGE:` in the footer:

```
feat!: drop support for Python 3.11
```

```
feat: new API

BREAKING CHANGE: `old_function` has been removed.
```

### Examples

```
feat(io): add support for GeoParquet output
fix: handle missing CRS in raster inputs
chore: bump ruff to v0.15.5
docs: add example notebook for elevation grid
```

## Running Tests

```console
pixi r test          # unit tests only
pixi r test-network  # network tests only
pixi r test-all      # all tests
```

## Type Checking

```console
pixi r typecheck
```

## Documentation

```console
pixi r docs-serve
```

## Managing the Changelog

The changelog is maintained by [git-cliff](https://git-cliff.org) and generated
automatically from conventional commit messages.

```console
pixi r changelog        # preview unreleased changes
pixi r changelog-update # write them to CHANGELOG.md
```

## Submitting Changes

1. Fork the repository.
1. Create a feature branch.
1. Make your changes with tests.
1. Ensure all checks pass.
1. Submit a pull request.
