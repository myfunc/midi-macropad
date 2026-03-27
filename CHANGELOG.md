# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-03-27

### Added

- Spotify plugin with OAuth PKCE integration for Web API control
  - Play/Pause, Next, Previous, Like, Shuffle, Repeat via Spotify API (pads 1-6)
  - Right sidebar: OAuth connection, now-playing info, pad mapping docs
  - Center tab: large now-playing view with transport controls
  - Background polling for track state every 3 seconds
  - Automatic token refresh
  - Search and Liked Songs pads via keyboard shortcuts (pads 7-8)
- Spotify app volume control via Knob 3 (CC50) using Windows audio sessions

### Requirements

- Spotify Premium account
- Client ID from developer.spotify.com
