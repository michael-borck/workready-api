# Portal Sidebar Restructure + Teams-like UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the portal sidebar to collapse mail into a single section with progressive disclosure, replace the chat drawer with a full main-content Teams experience, and remove stage-gated nav buttons that should arrive through communication.

**Architecture:** Pure frontend (workready-portal). Three files: index.html, app.js, style.css. No API changes. Existing chat/team/mail endpoints are reused. The sidebar gains collapsible sections; the main content area gains two new Teams views (landing + conversation).

**Tech Stack:** Vanilla JS (ES5 IIFE pattern), HTML5, CSS3. No frameworks. Uses existing `api()` fetch wrapper, `escapeHtml()`, `toggle()`, `switchView()` helpers.

**Spec:** `workready-api/docs/superpowers/specs/2026-04-16-portal-sidebar-teams-ux-design.md`

---

## File change summary

| File | What changes |
|------|-------------|
| `index.html` | Restructure sidebar HTML: collapse mail sections, add Teams section, remove stage-gated nav buttons, remove chat drawer aside, add Teams view containers in main content |
| `app.js` | New mail collapse/expand logic, new Teams view rendering (landing + conversation), rewire `switchView()` and `renderState()`, remove chat drawer functions, simplify team directory code |
| `style.css` | New collapsible sidebar styles, Teams landing card styles, Teams conversation styles (full-width chat), remove chat drawer styles |

---

## Block 1 — Sidebar HTML restructure

### Task 1.1: Restructure sidebar HTML

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Replace the two email sidebar sections with a single collapsible Mail section**

Find the two existing email sections (lines ~129-153):

```html
                <div class="sidebar-section">
                    <h4>&#9993; Personal Email</h4>
                    <nav class="nav">
                        <button class="nav-item" data-view="inbox-personal">
                            Inbox
                            <span id="unread-personal" class="badge hidden">0</span>
                        </button>
                        <button class="nav-item" data-view="sent">
                            Sent
                        </button>
                    </nav>
                </div>

                <div class="sidebar-section hidden" id="nav-work-email-section">
                    <h4>&#128188; Work Email</h4>
                    <nav class="nav">
                        <button class="nav-item" data-view="inbox-work" id="nav-inbox-work">
                            Inbox
                            <span id="unread-work" class="badge hidden">0</span>
                        </button>
                        <button class="nav-item" data-view="sent-work">
                            Sent
                        </button>
                    </nav>
                </div>
```

Replace with:

```html
                <div class="sidebar-section" id="nav-mail-section">
                    <h4 class="sidebar-collapse-toggle" id="mail-toggle">&#9993; Mail <span class="collapse-arrow">&#9662;</span></h4>
                    <div class="sidebar-collapsible" id="mail-body">
                        <!-- Pre-hire: flat inbox/sent. Post-hire: sub-groups injected by JS -->
                        <nav class="nav" id="mail-nav-simple">
                            <button class="nav-item" data-view="inbox-personal">
                                Inbox
                                <span id="unread-personal" class="badge hidden">0</span>
                            </button>
                            <button class="nav-item" data-view="sent">
                                Sent
                            </button>
                        </nav>
                        <div id="mail-nav-split" class="hidden">
                            <div class="sidebar-sub-section">
                                <h5 class="sidebar-collapse-toggle sub-toggle" id="mail-personal-toggle">Personal <span class="collapse-arrow">&#9656;</span></h5>
                                <nav class="nav sidebar-collapsible collapsed" id="mail-personal-body">
                                    <button class="nav-item" data-view="inbox-personal">
                                        Inbox
                                        <span id="unread-personal-split" class="badge hidden">0</span>
                                    </button>
                                    <button class="nav-item" data-view="sent">
                                        Sent
                                    </button>
                                </nav>
                            </div>
                            <div class="sidebar-sub-section">
                                <h5 class="sidebar-collapse-toggle sub-toggle" id="mail-work-toggle">Work <span class="collapse-arrow">&#9656;</span></h5>
                                <nav class="nav sidebar-collapsible collapsed" id="mail-work-body">
                                    <button class="nav-item" data-view="inbox-work" id="nav-inbox-work">
                                        Inbox
                                        <span id="unread-work" class="badge hidden">0</span>
                                    </button>
                                    <button class="nav-item" data-view="sent-work">
                                        Sent
                                    </button>
                                </nav>
                            </div>
                        </div>
                    </div>
                </div>
```

- [ ] **Step 2: Replace the Team directory sidebar section with a collapsible Teams section**

Find the existing team directory section (lines ~155-163):

```html
            <!-- Stage 7: Team directory -->
            <div class="sidebar-section hidden" id="nav-team-section">
              <h4>&#128101; Your Team</h4>
              <div class="nav-team-list" id="nav-team-list"></div>
              <div class="nav-section-title nav-org-title">
                <span>Wider Organisation</span>
              </div>
              <div class="nav-org-list nav-org-list-collapsed" id="nav-org-list"></div>
            </div>
```

Replace with:

```html
                <!-- Stage 7: Teams -->
                <div class="sidebar-section hidden" id="nav-teams-section">
                    <h4 class="sidebar-collapse-toggle" id="teams-toggle">&#128101; Teams <span class="collapse-arrow">&#9662;</span></h4>
                    <div class="sidebar-collapsible" id="teams-body">
                        <nav class="nav" id="teams-member-list">
                            <!-- populated by renderTeamsSidebar() -->
                        </nav>
                        <div class="sidebar-sub-section">
                            <h5 class="sidebar-collapse-toggle sub-toggle" id="teams-org-toggle">Wider Org <span class="collapse-arrow">&#9656;</span></h5>
                            <div class="sidebar-collapsible collapsed" id="teams-org-list">
                                <!-- populated by renderTeamsSidebar() -->
                            </div>
                        </div>
                    </div>
                </div>
```

- [ ] **Step 3: Remove stage-gated nav buttons from Workspace section**

Find the Workspace section (lines ~165-198). Remove these buttons:
- `nav-interview` (Interview)
- `nav-team` (Team)
- `nav-lunchroom` (Lunchroom)
- `nav-perf-review` (Mid-placement check-in)
- `nav-exit-interview` (Exit Interview)

Keep only Tasks and Play the Primer:

```html
                <div class="sidebar-section">
                    <h4>Workspace</h4>
                    <nav class="nav">
                        <button class="nav-item hidden" data-view="tasks" id="nav-tasks">
                            <span class="nav-icon">&#128221;</span>
                            Tasks
                        </button>
                        <button class="nav-item" data-view="primer">
                            <span class="nav-icon">&#127918;</span>
                            Play the Primer
                        </button>
                    </nav>
                </div>
```

**IMPORTANT:** Do NOT delete the `view-interview`, `view-lunchroom`, `view-perf-review`, `view-exit-interview` divs from the main content area — only remove their sidebar buttons. The views still exist for when triggered via messages/links.

- [ ] **Step 4: Remove the chat drawer aside**

Find and remove the entire `<aside class="chat-drawer" ...>` block (near end of body, before `</body>`):

```html
  <!-- Stage 7: Chat drawer -->
  <aside class="chat-drawer hidden" id="chat-drawer">
    ...entire block...
  </aside>
```

Delete this entire aside element.

- [ ] **Step 5: Add Teams view containers in main content area**

Find the main content area `<div class="content">` (after the sidebar, around line ~240). After the existing `view-team` div, add:

```html
                <div id="view-teams" class="view hidden">
                    <div id="teams-landing" class="teams-landing">
                        <!-- populated by renderTeamsLanding() -->
                    </div>
                    <div id="teams-conversation" class="teams-conversation hidden">
                        <div class="teams-conv-header" id="teams-conv-header">
                            <div class="teams-conv-character">
                                <span class="presence-dot" id="teams-conv-presence"></span>
                                <div>
                                    <span class="teams-conv-name" id="teams-conv-name"></span>
                                    <span class="teams-conv-role" id="teams-conv-role"></span>
                                </div>
                            </div>
                            <div class="teams-conv-actions">
                                <button class="btn btn-sm" id="teams-conv-email">Email</button>
                                <button class="btn btn-sm" id="teams-conv-back">Back</button>
                            </div>
                        </div>
                        <div class="teams-conv-messages" id="teams-conv-messages">
                            <!-- bubbles populated by renderTeamsChat() -->
                        </div>
                        <form class="teams-conv-composer" id="teams-conv-composer">
                            <textarea class="teams-conv-input" id="teams-conv-input" placeholder="Type a message..." rows="2" required></textarea>
                            <button type="submit" class="btn btn-primary">Send</button>
                        </form>
                    </div>
                </div>
```

- [ ] **Step 6: Verify HTML structure**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
grep -c "nav-teams-section" index.html   # expect 1
grep -c "teams-landing" index.html        # expect 1
grep -c "teams-conversation" index.html   # expect 1
grep -c "chat-drawer" index.html          # expect 0 (removed)
grep -c "nav-interview" index.html        # expect 0 (button removed)
grep -c "nav-lunchroom" index.html        # expect 0 (button removed)
grep -c "view-interview" index.html       # expect 1 (view div kept)
grep -c "view-lunchroom" index.html       # expect 1 (view div kept)
```

- [ ] **Step 7: Commit**

```bash
git add index.html
git commit -m "Restructure sidebar: collapsible Mail, Teams section, remove stage nav buttons, remove chat drawer"
```

---

## Block 2 — Sidebar JS logic

### Task 2.1: Add mail section collapse/expand and post-hire split

**Files:**
- Modify: `app.js`

- [ ] **Step 1: Add sidebar collapse/expand utility**

Find the end of the utility functions section (after `toggle()` at line ~244). Add:

```javascript
    function wireCollapsible(toggleId, bodyId) {
        var toggle = $(toggleId);
        var body = $(bodyId);
        if (!toggle || !body) return;
        toggle.addEventListener('click', function () {
            body.classList.toggle('collapsed');
            var arrow = toggle.querySelector('.collapse-arrow');
            if (arrow) {
                arrow.innerHTML = body.classList.contains('collapsed') ? '&#9656;' : '&#9662;';
            }
        });
    }
```

- [ ] **Step 2: Update renderState() for mail section**

In `renderState()`, find the existing work-email visibility logic (lines ~174-179):

```javascript
        var hired = s.state === 'HIRED' || s.state === 'COMPLETED';
        ...
        var workEmailSection = $('nav-work-email-section');
        if (workEmailSection) toggle(workEmailSection, hired);
```

Replace the mail-related visibility logic with:

```javascript
        var hired = s.state === 'HIRED' || s.state === 'COMPLETED';

        // Mail section: pre-hire shows flat, post-hire shows split
        var mailSimple = $('mail-nav-simple');
        var mailSplit = $('mail-nav-split');
        if (mailSimple && mailSplit) {
            if (hired) {
                mailSimple.classList.add('hidden');
                mailSplit.classList.remove('hidden');
                // Sync unread badge to split view
                var splitBadge = $('unread-personal-split');
                if (splitBadge) {
                    splitBadge.textContent = els.unreadPersonal ? els.unreadPersonal.textContent : '0';
                    toggle(splitBadge, s.unread_personal > 0);
                }
            } else {
                mailSimple.classList.remove('hidden');
                mailSplit.classList.add('hidden');
            }
        }
```

Also remove the old `toggle(els.navInterview, inInterviewStage);` and similar lines for removed nav items. Keep `toggle(els.navTasks, hired);`. Remove:

```javascript
        toggle(els.navInterview, inInterviewStage);
        toggle(els.navTeam, hired);
        toggle($('nav-lunchroom'), hired);
        ...
        toggle($('nav-exit-interview'), inExitStage || s.state === 'COMPLETED');
        ...
        toggle($('nav-perf-review'), inWorkTaskStage);
```

Replace all of those with a single Teams section toggle:

```javascript
        // Teams section: visible post-hire
        var teamsSection = $('nav-teams-section');
        if (teamsSection) toggle(teamsSection, hired);
```

Keep the `intranetLink` toggle and the `applyCompanyTheme` logic — those stay.

- [ ] **Step 3: Wire collapsible sections at boot**

Find the boot code block (around line ~2670, before `var savedEmail = ...`). Replace the existing `wireTeamDirectoryControls(); wireChatDrawerControls();` lines with:

```javascript
    // Sidebar collapsible sections
    wireCollapsible('mail-toggle', 'mail-body');
    wireCollapsible('mail-personal-toggle', 'mail-personal-body');
    wireCollapsible('mail-work-toggle', 'mail-work-body');
    wireCollapsible('teams-toggle', 'teams-body');
    wireCollapsible('teams-org-toggle', 'teams-org-list');
```

- [ ] **Step 4: Syntax check**

```bash
node --check app.js && echo "JS OK"
```

Expected: `JS OK`

- [ ] **Step 5: Commit**

```bash
git add app.js
git commit -m "Mail section collapse/expand, post-hire split, remove stage nav toggles"
```

---

### Task 2.2: Rewrite team directory as Teams sidebar + main-content views

**Files:**
- Modify: `app.js`

- [ ] **Step 1: Replace the team directory + chat drawer code**

Find the `// Stage 7: Team directory` block (line ~2414) through the end of `wireChatDrawerControls()` (line ~2651). This includes: `teamState`, `showTeamSection`, `hideTeamSection`, `loadTeamDirectory`, `renderTeamDirectory`, `renderTeamMemberRow`, `renderOrgMemberRow`, `wireTeamDirectoryControls`, `chatState`, `openChatDrawer`, `closeChatDrawer`, `loadChatThread`, `renderChatThread`, `startChatPolling`, `stopChatPolling`, `sendChatMessage`, `wireChatDrawerControls`.

Replace the ENTIRE block with:

```javascript
    // ============================================================
    // Stage 7: Teams
    // ============================================================

    var POST_HIRE_STAGES = ['placement', 'mid_placement', 'exit'];

    var teamsState = {
        team: [],
        org: [],
        loaded: false,
        activeSlug: null,
        messages: [],
        pollTimer: null,
    };

    function loadTeamsData() {
        if (!state.activeApplicationId) return;
        if (POST_HIRE_STAGES.indexOf(state.currentStage) < 0) return;

        api('/api/v1/team/' + state.activeApplicationId)
            .then(function (data) {
                teamsState.team = data.team || [];
                teamsState.org = data.org || [];
                teamsState.loaded = true;
                renderTeamsSidebar();
                // If Teams view is active, refresh it
                if (state.currentView === 'teams') {
                    if (teamsState.activeSlug) {
                        renderTeamsConversation();
                    } else {
                        renderTeamsLanding();
                    }
                }
            })
            .catch(function (err) {
                console.error('loadTeamsData:', err);
            });
    }

    function renderTeamsSidebar() {
        var list = $('teams-member-list');
        var orgList = $('teams-org-list');
        if (!list) return;

        list.innerHTML = teamsState.team.map(function (m) {
            var dotClass = m.presence_ok ? 'presence-dot-on' : 'presence-dot-off';
            var active = teamsState.activeSlug === m.slug ? ' teams-sidebar-active' : '';
            return '<button class="nav-item teams-sidebar-member' + active + '" data-teams-slug="' + escapeHtml(m.slug) + '">'
                + '<span class="presence-dot ' + dotClass + '"></span> '
                + escapeHtml(m.name)
                + '</button>';
        }).join('');

        // Wire click handlers
        list.querySelectorAll('.teams-sidebar-member').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var slug = btn.getAttribute('data-teams-slug');
                openTeamsChat(slug);
            });
        });

        if (orgList) {
            orgList.innerHTML = teamsState.org.map(function (m) {
                return '<div class="nav-org-member" title="Use your Work inbox to email ' + escapeHtml(m.name) + '">'
                    + '<span class="nav-org-name">' + escapeHtml(m.name) + '</span>'
                    + '<span class="nav-org-role">' + escapeHtml(m.role) + '</span>'
                    + '</div>';
            }).join('');
        }
    }

    function renderTeamsLanding() {
        var container = $('teams-landing');
        var convEl = $('teams-conversation');
        if (!container) return;

        container.classList.remove('hidden');
        if (convEl) convEl.classList.add('hidden');
        teamsState.activeSlug = null;
        stopTeamsPoll();
        renderTeamsSidebar();

        var companyName = '';
        if (state.student && state.student.active_application) {
            companyName = companyName(state.student.active_application.company_slug) || state.student.active_application.company_slug;
        }

        var html = '<div class="teams-landing-card">';
        html += '<h2>Your Team' + (companyName ? ' at ' + escapeHtml(companyName) : '') + '</h2>';

        if (teamsState.team.length === 0 && !teamsState.loaded) {
            html += '<p class="teams-landing-empty">Loading team...</p>';
        } else if (teamsState.team.length === 0) {
            html += '<p class="teams-landing-empty">No team members found.</p>';
        } else {
            html += '<div class="teams-landing-list">';
            teamsState.team.forEach(function (m) {
                var dotClass = m.presence_ok ? 'presence-dot-on' : 'presence-dot-off';
                var chatBtn = m.presence_ok
                    ? '<button class="btn btn-sm teams-landing-chat" data-teams-slug="' + escapeHtml(m.slug) + '">Chat</button>'
                    : '<span class="teams-landing-offline">' + escapeHtml(m.availability_note || 'Offline') + '</span>';
                html += '<div class="teams-landing-row">'
                    + '<span class="presence-dot ' + dotClass + '"></span>'
                    + '<div class="teams-landing-info">'
                    + '<span class="teams-landing-name">' + escapeHtml(m.name) + '</span>'
                    + '<span class="teams-landing-role">' + escapeHtml(m.role) + '</span>'
                    + '</div>'
                    + chatBtn
                    + '</div>';
            });
            html += '</div>';
        }

        if (teamsState.org.length > 0) {
            html += '<div class="teams-landing-org">';
            html += '<h3>Wider Organisation</h3>';
            teamsState.org.forEach(function (m) {
                html += '<div class="teams-landing-org-row">'
                    + '<span class="teams-landing-name">' + escapeHtml(m.name) + '</span>'
                    + '<span class="teams-landing-role">' + escapeHtml(m.role) + '</span>'
                    + '</div>';
            });
            html += '<p class="teams-landing-hint">Wider org contacts are email-only. Use your Work inbox to reach them.</p>';
            html += '</div>';
        }

        html += '</div>';
        container.innerHTML = html;

        // Wire chat buttons
        container.querySelectorAll('.teams-landing-chat').forEach(function (btn) {
            btn.addEventListener('click', function () {
                openTeamsChat(btn.getAttribute('data-teams-slug'));
            });
        });
    }

    function openTeamsChat(slug) {
        // Ensure we're in teams view
        if (state.currentView !== 'teams') {
            switchView('teams');
        }

        teamsState.activeSlug = slug;
        renderTeamsSidebar(); // update active highlight

        var landing = $('teams-landing');
        var convEl = $('teams-conversation');
        if (landing) landing.classList.add('hidden');
        if (convEl) convEl.classList.remove('hidden');

        // Set header
        var member = teamsState.team.find(function (m) { return m.slug === slug; });
        if (member) {
            $('teams-conv-name').textContent = member.name;
            $('teams-conv-role').textContent = member.role;
            var dot = $('teams-conv-presence');
            dot.className = 'presence-dot ' + (member.presence_ok ? 'presence-dot-on' : 'presence-dot-off');
        }

        loadTeamsThread();
        startTeamsPoll();

        var input = $('teams-conv-input');
        if (input) input.focus();
    }

    function loadTeamsThread() {
        if (!teamsState.activeSlug || !state.activeApplicationId) return;

        api('/api/v1/chat/thread/' + state.activeApplicationId + '/' + encodeURIComponent(teamsState.activeSlug))
            .then(function (data) {
                teamsState.messages = data.messages || [];
                renderTeamsChat();
            })
            .catch(function (err) { console.error('loadTeamsThread:', err); });
    }

    function renderTeamsChat() {
        var box = $('teams-conv-messages');
        if (!box) return;

        if (teamsState.messages.length === 0) {
            box.innerHTML = '<div class="teams-conv-empty">No messages yet. Say hello!</div>';
            return;
        }

        box.innerHTML = teamsState.messages.map(function (m) {
            var cls = 'chat-bubble chat-bubble-' + m.author;
            return '<div class="' + cls + '">'
                + '<div class="chat-bubble-content">' + escapeHtml(m.content).replace(/\n/g, '<br>') + '</div>'
                + '</div>';
        }).join('');
        box.scrollTop = box.scrollHeight;
    }

    function startTeamsPoll() {
        stopTeamsPoll();
        teamsState.pollTimer = setInterval(function () {
            if (!teamsState.activeSlug) { stopTeamsPoll(); return; }
            loadTeamsThread();
        }, 3000);
    }

    function stopTeamsPoll() {
        if (teamsState.pollTimer) {
            clearInterval(teamsState.pollTimer);
            teamsState.pollTimer = null;
        }
    }

    function sendTeamsMessage(e) {
        if (e) e.preventDefault();
        if (!teamsState.activeSlug || !state.activeApplicationId) return;

        var input = $('teams-conv-input');
        var text = input.value.trim();
        if (!text) return;

        input.value = '';
        input.disabled = true;

        api('/api/v1/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                application_id: state.activeApplicationId,
                character_slug: teamsState.activeSlug,
                content: text,
            }),
        })
            .then(function () {
                input.disabled = false;
                input.focus();
                loadTeamsThread();
            })
            .catch(function (err) {
                console.error('sendTeamsMessage:', err);
                input.disabled = false;
                input.value = text;
            });
    }

    function wireTeamsControls() {
        var backBtn = $('teams-conv-back');
        if (backBtn) backBtn.addEventListener('click', function () {
            teamsState.activeSlug = null;
            stopTeamsPoll();
            renderTeamsLanding();
            renderTeamsSidebar();
        });

        var emailBtn = $('teams-conv-email');
        if (emailBtn) emailBtn.addEventListener('click', function () {
            if (teamsState.activeSlug) {
                // Switch to work inbox compose — pre-fill recipient
                switchView('inbox-work');
                // TODO: pre-fill compose recipient if compose UI supports it
            }
        });

        var form = $('teams-conv-composer');
        if (form) form.addEventListener('submit', sendTeamsMessage);

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && teamsState.activeSlug && state.currentView === 'teams') {
                teamsState.activeSlug = null;
                stopTeamsPoll();
                renderTeamsLanding();
                renderTeamsSidebar();
            }
        });
    }
```

- [ ] **Step 2: Update switchView() to handle Teams**

Find `switchView()` (line ~531). Add the Teams view handling. After the existing view-specific lines, add:

```javascript
        if (view === 'teams') {
            if (teamsState.activeSlug) {
                openTeamsChat(teamsState.activeSlug);
            } else {
                renderTeamsLanding();
            }
        }
        if (view !== 'teams') {
            stopTeamsPoll();
        }
```

- [ ] **Step 3: Update renderState() to call loadTeamsData()**

In `renderState()`, find the existing `loadTeamDirectory();` call (added in the earlier session). Replace it with:

```javascript
        loadTeamsData();
```

- [ ] **Step 4: Update boot code to wire Teams controls**

In the boot code block, replace the old wire calls with:

```javascript
    wireTeamsControls();
```

(The collapsible wiring from Task 2.1 should already be there.)

- [ ] **Step 5: Fix companyName reference in renderTeamsLanding**

The `renderTeamsLanding` function calls `companyName(slug)`. Check if this function exists in app.js. Grep for `function companyName`. If it exists, great. If not, it may be a different name — find the existing company name resolver and use it. If there is none, use a simple fallback:

```javascript
        var companyLabel = '';
        if (state.student && state.student.active_application) {
            companyLabel = state.student.active_application.company_slug.replace(/-/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
        }
```

And replace the `companyName` reference in the landing HTML with `companyLabel`.

- [ ] **Step 6: Syntax check**

```bash
node --check app.js && echo "JS OK"
```

Expected: `JS OK`

- [ ] **Step 7: Commit**

```bash
git add app.js
git commit -m "Teams view: sidebar members, landing card, conversation, send/poll — replaces chat drawer"
```

---

## Block 3 — CSS

### Task 3.1: Add sidebar collapsible styles + Teams view styles, remove chat drawer styles

**Files:**
- Modify: `style.css`

- [ ] **Step 1: Find and remove chat drawer CSS**

Find the `/* Stage 7: Chat drawer */` section in style.css (added in the earlier session). Remove the ENTIRE section from `.chat-drawer {` through the `@media (max-width: 720px)` block for `.chat-drawer`.

- [ ] **Step 2: Update the team directory sidebar CSS**

The existing `/* Stage 7: Team directory sidebar */` section can stay mostly intact (presence dots, org member rows are still used). But update the section comment to `/* Stage 7: Teams sidebar */`.

- [ ] **Step 3: Append collapsible sidebar styles**

Append to `style.css`:

```css
/* ============================================================
   Sidebar collapsible sections
   ============================================================ */

.sidebar-collapse-toggle {
  cursor: pointer;
  user-select: none;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.sidebar-collapse-toggle:hover {
  color: var(--color-primary, #2563eb);
}

.collapse-arrow {
  font-size: 0.7rem;
  transition: transform 0.15s ease;
}

.sidebar-collapsible.collapsed {
  display: none;
}

.sidebar-sub-section {
  padding-left: 0.75rem;
}

.sub-toggle {
  font-size: 0.78rem;
  font-weight: 600;
  color: #6b7280;
  padding: 0.3rem 0.5rem;
  margin: 0;
}
.sub-toggle:hover {
  color: #374151;
}

/* ============================================================
   Teams sidebar member list
   ============================================================ */

.teams-sidebar-member {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  text-align: left;
}

.teams-sidebar-member .presence-dot {
  flex-shrink: 0;
}

.teams-sidebar-active {
  background: var(--color-primary-bg, #eff6ff);
  font-weight: 600;
}

/* ============================================================
   Teams landing card
   ============================================================ */

.teams-landing-card {
  max-width: 640px;
  margin: 2rem auto;
  padding: 2rem;
}

.teams-landing-card h2 {
  margin-bottom: 1.5rem;
  font-size: 1.25rem;
}

.teams-landing-card h3 {
  margin-top: 2rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #6b7280;
}

.teams-landing-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.teams-landing-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.65rem 0.85rem;
  border-radius: 8px;
  background: #f9fafb;
  border: 1px solid #f3f4f6;
}
.teams-landing-row:hover {
  background: #f3f4f6;
}

.teams-landing-info {
  flex: 1 1 auto;
  min-width: 0;
}

.teams-landing-name {
  display: block;
  font-weight: 500;
  font-size: 0.95rem;
  color: #1f2937;
}

.teams-landing-role {
  display: block;
  font-size: 0.78rem;
  color: #6b7280;
}

.teams-landing-offline {
  font-size: 0.75rem;
  color: #9ca3af;
  font-style: italic;
}

.teams-landing-org-row {
  display: flex;
  justify-content: space-between;
  padding: 0.35rem 0;
  font-size: 0.85rem;
  color: #6b7280;
}

.teams-landing-hint {
  margin-top: 0.75rem;
  font-size: 0.78rem;
  color: #9ca3af;
  font-style: italic;
}

.teams-landing-empty {
  color: #9ca3af;
  font-style: italic;
  padding: 1rem 0;
}

/* ============================================================
   Teams conversation (full-width chat)
   ============================================================ */

.teams-conversation {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.teams-conv-header {
  padding: 0.85rem 1.25rem;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fafbfc;
  flex-shrink: 0;
}

.teams-conv-character {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.teams-conv-name {
  font-weight: 600;
  font-size: 0.95rem;
  color: #1f2937;
}

.teams-conv-role {
  display: block;
  font-size: 0.75rem;
  color: #6b7280;
}

.teams-conv-actions {
  display: flex;
  gap: 0.5rem;
}

.teams-conv-messages {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  background: #f9fafb;
}

.teams-conv-empty {
  color: #9ca3af;
  font-style: italic;
  text-align: center;
  padding: 3rem 1rem;
}

.teams-conv-composer {
  padding: 0.85rem 1rem;
  border-top: 1px solid #e5e7eb;
  display: flex;
  gap: 0.6rem;
  background: #fff;
  flex-shrink: 0;
}

.teams-conv-input {
  flex: 1 1 auto;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 0.55rem 0.75rem;
  font-family: inherit;
  font-size: 0.9rem;
  resize: none;
}
.teams-conv-input:focus {
  outline: none;
  border-color: #2563eb;
}
```

Note: The `.chat-bubble`, `.chat-bubble-student`, `.chat-bubble-character` styles from the earlier session should STAY — they're reused by the Teams conversation view. Only remove the `.chat-drawer*` styles.

- [ ] **Step 3: Commit**

```bash
git add style.css
git commit -m "Teams view styles, sidebar collapsible styles, remove chat drawer styles"
```

---

## Block 4 — Verify + cleanup

### Task 4.1: Syntax check and visual verification

**Files:**
- All three (read-only verification)

- [ ] **Step 1: JS syntax check**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
node --check app.js && echo "JS OK"
```

Expected: `JS OK`

- [ ] **Step 2: Verify no references to removed elements**

```bash
# These should all return 0 matches in app.js
grep -c "chat-drawer" app.js          # expect 0
grep -c "openChatDrawer" app.js       # expect 0
grep -c "closeChatDrawer" app.js      # expect 0
grep -c "wireChatDrawerControls" app.js  # expect 0
grep -c "nav-lunchroom" app.js        # expect 0 (was stage-gated nav)
```

Note: `nav-lunchroom` may still appear in app.js if the lunchroom *view* loading code references it. That's OK — the view still exists, only the sidebar button is removed. What should be gone is any `toggle($('nav-lunchroom')` call in renderState.

- [ ] **Step 3: Verify existing smokes still pass**

The chat routes smoke (`smoke_chat_routes.py`) tests the API endpoints, not the portal UI. It should still pass:

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-api
uv run python scripts/smoke_chat_routes.py
```

Expected: `OK: chat routes smoke passed`

- [ ] **Step 4: Commit any final fixes**

If any issues were found and fixed:

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
git add -A
git commit -m "Fix post-restructure cleanup issues"
```
