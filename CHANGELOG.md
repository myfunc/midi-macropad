# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-03-27

### Added

- Spotify plugin with OAuth PKCE integration for Web API control
  - Play/Pause, Next, Previous, Like, Shuffle via Spotify API
  - DJ Mix pad (keyboard shortcut), Add to Playlist, Remove from Playlist (API)
  - Right sidebar: OAuth connection, now-playing info, pad mapping docs
  - Center tab: large now-playing view with transport controls
  - Background polling for track state every 3 seconds
  - Automatic token refresh
- Spotify app volume control via Knob 3 (CC50) using Windows audio sessions
- Plugin selector combo in right sidebar to open any plugin's properties
- Mode icons and redesigned mode selection buttons with custom themes
- Unique MIDI mini-melodies (~2s) per mode for auditory feedback on switch
- Joystick Y-axis (CC16) dedicated to mode switching (up/down)
- Joystick X-axis (pitch bend) for real-time melody transposition (±24 semitones)
- Transpose value persists across sessions (`melody_transpose` setting)
- All mode melodies normalized to C major for consistent tonality

### Changed

- Default window size doubled to 2400×1560 for high-resolution displays
- Voicemeeter joystick binding removed; joystick is now global mode switcher
- Mode order: Spotify, Voicemeeter, Voice Scribe placed first in the list

### Removed

- Productivity, Development, Media mode sections (broken/unused)
- Spotify Liked Songs, Search, Repeat pads (replaced with DJ Mix, playlist actions)

### Requirements

- Spotify Premium account
- Client ID from developer.spotify.com
