# Unified OBS mode (OBS + OBS Session)

**Date:** 2026-03-29  
**Context:** Two modes duplicated OBS/WebSocket concerns and split session vs scene behavior.  
**Decision:** Single **OBS** mode with three named scenes, one pad map, and session/segment feedback cues; retire the separate OBS Session mode entry and plugin label.

**Reasoning:** One mental model for streaming: scene selection, mic, session lifecycle, and recording/segments on one layer. Settings map 1:1 to scene roles instead of a single `working_scene` plus implicit session behavior.

**Consequences:**
- Scenes: `MM_Screen` (right-half monitor), `MM_Camera` (webcam fullscreen), `MM_ScreenPiP` (screen + camera PiP).
- Pads 1–3 scenes; 4 mic mute; 5 session start/stop (state-dependent); 6 record/segment (state-dependent); 7–8 unassigned.
- Config/settings: `scene_screen`, `scene_camera`, `scene_pip` replace `working_scene`.
- MIDI feedback: `session.start`, `session.stop`, `session.segment_start`, `session.segment_stop` (plus existing OBS/action feedback as implemented).
- Plugin folder may remain `obs_session` on disk; user-facing name is **OBS**.
