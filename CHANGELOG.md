<!-- markdownlint-configure-file {"MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [unreleased]

### Added

- Run several boxes against the same project directory at once; each `run` gets a uniquely
  suffixed container name (override with `-n`).

### Changed

- `stop` with no arguments now stops every box for the current project, and `shell` selects the
  project's box automatically, asking for `-n NAME` when more than one is running.

### Fixed

- Claude Code failing to start in the container because the managed settings file was not readable
  by the mapped non-root user (`EACCES`).

## [0.0.1] - 2026-00-00

First version.

[unreleased]: https://github.com/Tatsh/sbclaude/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/Tatsh/sbclaude/releases/tag/v0.0.1
