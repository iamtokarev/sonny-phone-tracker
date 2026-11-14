# The Dashboard runs inside Sonny, only watches, and opens only on this Mac

Sonny gets a browser page, the Dashboard, showing the Focus Session, the Attention State, what the agent is doing and what the camera sees, with detection boxes. It is served by Sonny's own process: `SonnyFocusCompanion.custom_app_url = "http://127.0.0.1:8042"` makes the `ReachyMiniApp` base class start a FastAPI `settings_app` that serves the built page from `src/sonny_focus_companion/static/` and stops with the app. The page only watches. It cannot start, end or pause a session, grant an Excuse or trigger a Nudge; the owner still does all of that by voice. The one control is a privacy toggle that stops camera frames reaching the page, which changes nothing in Sonny's behaviour. The server binds to 127.0.0.1 only, because the page streams the camera and has no login. We chose this over a separate server (a second process to start and keep in sync), over controls on the page (a second input channel next to voice, which would need its own rules for how the agent learns about each button, see [ADR 0003](0003-code-observes-agent-decides.md)), and over LAN access (needs a token, and a phone dashboard works against the point of the app).

## Consequences

- v1 is no longer "voice-only, no screen UI": the Dashboard is a window onto Sonny, not a way to drive it. Sonny prints the URL on start and opens no browser by default.
- Camera frames still never leave the Mac ([ADR 0002](0002-camera-local-audio-may-use-cloud.md)): they go only to a browser tab on the same machine.
- Encoding frames for the page costs CPU only while a tab is open, and runs off the vision thread.
- The robot's Reachy Mini Control app shows no gear icon for Sonny, because it lists only apps the daemon launched. The printed URL is how the owner finds the page.
- Adding a control later means revisiting this ADR and ADR 0003 together, not just adding a button.
- Opening it to the LAN later means adding auth first, not just changing the bind address.

Research: [R5: a live web dashboard for Sonny](../research/r5-web-dashboard.md).
