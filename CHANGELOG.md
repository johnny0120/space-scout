# Changelog

All notable changes to Space Scout will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases
use semantic versioning where practical.

## [Unreleased]

## [0.1.3] - 2026-09-09

- Fix the GitHub Release publish job so it can resolve the tag before uploading artifacts.

## [0.1.2] - 2026-09-09

- Improve Windows volume and directory-cycle handling in cross-platform scans.
- Preserve native Windows path separators in TUI details and confirmation prompts.
- Keep platform-specific quality checks portable across CI runners.

## [0.1.1] - 2026-09-09

- Publish the first cross-platform release with recursive `On disk` sizing.
- Add GitHub Release packaging, checksums, and contributor templates.
- Fix Windows volume handling in scanner fixtures and CI.
