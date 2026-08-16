<!-- markdownlint-configure-file {"MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.1/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [unreleased]

### Added

- `--wayland` (config key `wayland`) forwards the host compositor socket into the box, re-homed
  under the container's own `XDG_RUNTIME_DIR`. Preferred over `--x11`, which hands the box a
  cookie granting access to the whole session.
- `--gpu` now also passes through the DRM render nodes (`/dev/dri/renderD*`), so Mesa on
  AMD/Intel and Vulkan/VA-API work, not only CUDA.

### Fixed

- `--gpu` left the GPU unusable: the supplementary device groups Docker granted were dropped when
  the entrypoint switched to the mapped user, and the driver capability set omitted `graphics`, so
  anything that rendered silently fell back to software.
- The glvnd, Vulkan, and GBM manifests the NVIDIA container toolkit does not install are now
  synthesised at start-up, so GL, EGL, and Vulkan clients find the real driver.
- `~/.config` is no longer left root-owned when something is mounted beneath it, which had made
  Chrome abort at start-up.

## [0.0.1] - 2026-08-07

First release.

`sbclaude` runs Claude Code inside a throwaway Docker container with the bash sandbox and
permission prompts disabled, against a configurable set of host bind mounts.

See the README for the full feature set: RE toolchain, session recovery, MCP support, SSH and GPG
passthrough, and per-project configuration.

[unreleased]: https://github.com/Tatsh/sbclaude/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/Tatsh/sbclaude/releases/tag/v0.0.1
