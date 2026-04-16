# Portal Sidebar Restructure + Teams-like UX

**Date:** 2026-04-16
**Status:** Approved for implementation
**Repo:** workready-portal (frontend only — no API changes)

## Problem

The portal sidebar is growing busy: two email sections, a team directory, a workspace section with 6+ stage-gated nav items, external links, and support tools. The post-hire communication experience feels like "email with a chat drawer bolted on" rather than the Teams-like workplace environment students will encounter in real placements.

## Design principles

1. **Communication feel shifts when hired.** Pre-hire = email (formal, slow). Post-hire = Teams (casual, fast, presence-aware). The transition itself teaches workplace norms.
2. **Events arrive through communication, not navigation.** Interview invitations come by email. Lunchroom invitations arrive as messages. Mid-placement check-ins are initiated by the mentor. Students don't click a sidebar button to "go to" an event — they respond to it the way they would in a real workplace.
3. **Progressive disclosure.** Show only what's relevant to the student's current stage. Pre-hire sees a simple Mail section. Post-hire sees Mail split into Personal/Work plus a Teams section.

## Sidebar structure

### Pre-hire

```
Dashboard
Mail ▾
  Inbox (3)
  Sent
Workspace
  Play the Primer
External
  seek.jobs
Support Tools
  Talk Buddy
  Career Compass
```

"Mail" is collapsible. Single inbox/sent since only personal email exists pre-hire.

### Post-hire

```
Dashboard
Mail ▾
  Personal ▸            ← collapsible, expands to Inbox / Sent
  Work ▸                ← collapsible, badge shows unread count
Teams ▾                  ← new section, post-hire only
  ● Karen Whitfield
  ○ Ravi Mehta
  ● Brooke Lawson
  ── Wider Org ──        ← collapsible, email-only contacts
  Dave Collins
Workspace
  Tasks
  Play the Primer
External
  seek.jobs
  Company Intranet
Support Tools
  Talk Buddy
  Career Compass
```

"Teams" is collapsible. Team members show presence dots (green = available, grey = offline). Clicking a team member opens their chat in the main content area. Wider org members are email-only — clicking them shows a tooltip: "Use your Work inbox to email [name]."

## Main content area

### Teams landing (no conversation selected)

When the student enters the Teams view, the main content area shows a team overview card:

```
┌─────────────────────────────────────────────────┐
│  Your Team at IronVale Resources                │
│                                                 │
│  ● Karen Whitfield       Ops Lead          Chat │
│  ○ Ravi Mehta            GM Sustainability      │
│  ● Brooke Lawson         HS&E Manager      Chat │
│                                                 │
│  ── Wider Organisation ──                       │
│  Dave Collins            CEO                    │
│  Sam Torres              HR Manager             │
│                                                 │
│  Wider org contacts are email-only.             │
│  Use your Work inbox to reach them.             │
└─────────────────────────────────────────────────┘
```

Each team member row: presence dot, name, role, "Chat" button (disabled when offline). Wider org rows: name and role only, with a note about email-only access.

This landing view is also where a future #general channel link would appear (Phase 2 — not built now, but the layout should leave visual space for it above the member list).

### Teams conversation (character selected)

```
┌─────────────────────────────────────────────────┐
│  ● Karen Whitfield  ·  Ops Lead                 │
│                              [Email] [Back]     │
├─────────────────────────────────────────────────┤
│                                                 │
│                     Hey Karen, quick question    │
│                     about the supplier matrix    │
│                                                 │
│  Hey Alex! Sure — what's                        │
│  tripping you up?                               │
│                                                 │
│                     The risk weighting column,   │
│                     not sure what scale to use   │
│                                                 │
├─────────────────────────────────────────────────┤
│  [Type a message...                    ] [Send] │
└─────────────────────────────────────────────────┘
```

- **Header bar:** Presence dot, character name, role. "Email" button (opens work inbox compose with recipient pre-filled). "Back" button (returns to team overview).
- **Message area:** Student bubbles right-aligned blue, character bubbles left-aligned white/bordered. Scrolls to bottom on load and new messages.
- **Compose box:** Textarea + Send button. Disabled while sending. On error, restores the text.
- **Polling:** Refreshes the thread every 3 seconds while the conversation is open. Stops when navigating away.

### Mail views

No changes to how inbox/sent views render in the main content area. The existing `loadInbox('personal')` and `loadInbox('work')` work as-is. Colour coding (purple personal, emerald work) remains.

"Email" button on the Teams conversation header opens the work inbox compose view with the recipient email pre-filled. This is a convenience shortcut using existing compose functionality.

## What gets removed

### Sidebar nav buttons removed

| Button | Why | How events reach the student instead |
|--------|-----|--------------------------------------|
| Interview | Stage-gated, single-use | Invitation arrives by email with booking link |
| Lunchroom | Stage-gated, single-use | Invitation arrives as a message |
| Mid-placement check-in | Stage-gated, single-use | Mentor initiates via chat or email |
| Exit Interview | Stage-gated, single-use | Triggered by stage transition, surfaces through communication |
| Team | Replaced by Teams section | Team members listed inline in Teams sidebar section |

The underlying views (interview booking, lunchroom chat, perf-review, exit-interview) remain in the JS — they lose their sidebar entry points but are still reachable via message links and calendar entries.

### Chat drawer removed

The right-side slide-in `<aside class="chat-drawer">` is removed entirely. Chat now renders in the main content area as a Teams conversation view. All chat-drawer CSS and JS (openChatDrawer, closeChatDrawer, chat polling via drawer) is replaced by the new Teams conversation view logic.

## What stays unchanged

- Dashboard view and rendering
- Stage badge at top of page (shows current stage)
- Tasks view (inside Workspace, post-hire only)
- Play the Primer (inside Workspace, always visible)
- External links (seek.jobs, Company Intranet)
- Support Tools (Talk Buddy, Career Compass)
- All API endpoints — no backend changes

## Scope boundary — not part of this spec

- **Calendar improvements:** Making calendar entries clickable links to events. Noted for future work.
- **Missed-event follow-up emails:** "We missed you today..." messages when a student skips a calendar event. Future work.
- **#general channel / team meetings:** Group chat with all team members, structured multi-party meetings with agendas. Phase 2 future work. The Teams landing layout should leave space for this.
- **Mobile responsive adjustments:** The sidebar may need a hamburger menu on mobile. Defer unless it breaks.

## Technical notes

### Files modified

Only three files in `workready-portal`:
- `index.html` — sidebar HTML restructure, remove chat drawer aside, add Teams main-content containers
- `app.js` — new Teams view rendering, mail section collapse/expand logic, remove chat drawer functions, rewire team member click handlers
- `style.css` — Teams conversation styles (full-width chat), updated sidebar section styles, remove chat drawer styles

### State management

Existing `state` object gains:
- `state.teamsView` — `'landing'` | `'conversation'`
- `state.activeCharacterSlug` — which character's chat is open (replaces `chatState.characterSlug`)

Existing `teamState` and `chatState` objects are merged/simplified since the chat is no longer a separate drawer overlay.

### Data flow

```
Sidebar click "Teams"
  → showView('teams')
  → renderTeamsLanding()
  → fetches GET /api/v1/team/{appId}
  → renders team overview card in main content

Team member click
  → state.activeCharacterSlug = slug
  → state.teamsView = 'conversation'
  → renderTeamsConversation()
  → fetches GET /api/v1/chat/thread/{appId}/{slug}
  → renders chat in main content
  → starts 3s polling

Send message
  → POST /api/v1/chat/send
  → re-fetches thread
  → re-renders

"Back" button
  → state.teamsView = 'landing'
  → renderTeamsLanding()
  → stops polling
```
