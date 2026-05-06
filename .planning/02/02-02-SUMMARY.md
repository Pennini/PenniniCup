---
phase: 02
plan: 02
subsystem: templates/frontend-chrome
tags: [mobile-first, chrome, contextual-ui, auto-dismiss]
depends_on: []
provides: [mobile-save-button-selector, django-message-auto-dismiss]
affects: [pool:detail JavaScript (Plan 03), user-feedback flow]
tech_stack:
  patterns: [data-attributes, vanilla-js IIFE, TailwindCSS dark theme]
  additions: [data-topbar-save-btn selector, data-django-message selector]
key_files:
  created: []
  modified:
    - src/templates/components/top_nav.html (1 insertion, 1 deletion)
    - src/templates/base.html (13 insertions)
completed_at: 2026-05-06T13:39Z
completed_tasks: 2/2
---

# Phase 02 Plan 02: Palpites Mobile-First — Summary

**Mobile "chrome" preparation:** contextual save button + auto-dismissing Django messages

## Objective

Prepare the application frame (top bar and base layout) for mobile-first palpites flow:

1. **Mobile save button** starts hidden/neutral with data-topbar-save-btn selector (Plan 03 JS toggles its visibility and color based on dirty state)
1. **Django messages** auto-dismiss after 4s with smooth fade (D-19, D-22 feedback requirement)

Both are prerequisites for Plan 03 (detail.html JS integration).

## Completed Tasks

| #   | Name                                             | Status | Commit  |
| --- | ------------------------------------------------ | ------ | ------- |
| 1   | Mobile save button: hidden state + data selector | PASS   | cc03fdf |
| 2   | Django messages: auto-dismiss + data-attribute   | PASS   | 19287d3 |

## Task 1: Mobile Save Button (cc03fdf)

**File:** `src/templates/components/top_nav.html` (lines 198–210)

**Change:**

- Added `data-topbar-save-btn` attribute (JS selector target for Plan 03)
- Added `hidden` class (JS removes when dirty state detected)
- Changed default color from `bg-orange-500 text-black` to `bg-neutral-800 text-neutral-400` (neutral resting state)
- Preserved desktop button (lines 130–141) with original orange styling
- Preserved `form="pool-bets-form"`, `name="submit_action"`, `value="save_all"` attributes
- Preserved disabled logic: `{% if not can_bet or group_locked and knockout_locked %}disabled{% endif %}`
- Kept `absolute left-1/2 -translate-x-1/2` centering in mobile top bar

**Contract for Plan 03:**

```javascript
// Plan 03 will use this selector:
document.querySelector('[data-topbar-save-btn]')

// Initial CSS state (hidden by default):
// - has classes: hidden, bg-neutral-800, text-neutral-400
// - does NOT have: bg-orange-500, text-black

// Plan 03's syncTopBarButton() will toggle:
// - hidden class (removed when dirty)
// - bg-orange-500, text-black (added when dirty)
// - bg-neutral-800, text-neutral-400 (removed when dirty)
```

## Task 2: Django Messages Auto-Dismiss (19287d3)

**File:** `src/templates/base.html` (lines 26, 40–51)

**Changes:**

1. **Message div attribute** (line 26):

   - Added `data-django-message` attribute to each message `<div>`
   - Preserved all conditional color classes (success=green, warning=amber, error=red, info=blue)

1. **Auto-dismiss script** (lines 40–51):

   - Inserted IIFE between `{% block extra_scripts %}` and `lucide.createIcons()` call
   - Script selects all `[data-django-message]` elements
   - Each message: waits 4000ms → fades out over 500ms → removed from DOM
   - Smooth fade via `transition: opacity 0.5s ease; opacity: 0`

**Timing contract (D-19):**

```javascript
// When a message renders, auto-dismiss happens automatically:
// - 0ms: message visible, opacity 1
// - 4000ms: transition starts, opacity becomes 0 (500ms fade)
// - 4500ms: element removed from DOM
// This serves as toast-like feedback for save operations (D-19, D-22)
```

**Visual preservation:**

- Messages remain in `<main>` with existing padding (no z-index manipulation per Pitfall 5)
- All color classes preserved for each message level
- lucide.createIcons() call runs after auto-dismiss setup (correct script order)

## Verification Results

All acceptance criteria pass:

### Task 1

- `data-topbar-save-btn` count = 1 ✓
- `hidden absolute left-1/2 -translate-x-1/2` present ✓
- `bg-neutral-800 text-neutral-400` present ✓
- `bg-orange-500` preserved on desktop button ✓
- `form="pool-bets-form"` appears 2x (desktop + mobile) ✓
- `submit_action` appears 2x ✓
- Django check exits 0 ✓
- Templates load without errors ✓

### Task 2

- `data-django-message` count ≥ 2 (attribute + selector) ✓
- `querySelectorAll('[data-django-message]')` present ✓
- `setTimeout(..., 500)` for removal present ✓
- `}, 4000);` timeout present ✓
- `lucide.createIcons()` preserved ✓
- Success color `border-green-500/30 bg-green-500/10 text-green-300` preserved ✓
- Error color `border-red-500/30 bg-red-500/10 text-red-300` preserved ✓
- `{% block extra_scripts %}` preserved ✓
- Django check exits 0 ✓
- base.html and top_nav.html load without TemplateSyntaxError ✓

## Deviations from Plan

None — plan executed exactly as written. No bugs found, no missing functionality.

## Known Stubs

None. Both files are template modifications with no data placeholders.

## Threat Flags

No new security surface introduced:

- Mobile save button is form-submit only (no new endpoint)
- Django messages auto-dismiss is client-side (no backend change)
- No new query parameters, auth paths, or schema changes
- Attributes are purely structural hooks for JavaScript

## Summary for Plan 03

Plan 03 (detail.html JavaScript) will depend on these two contracts:

1. **Mobile save button selector:** `document.querySelector('[data-topbar-save-btn]')`

   - Initial state: `hidden bg-neutral-800 text-neutral-400` (visible but neutral)
   - Plan 03 toggles: `.hidden`, `.bg-orange-500`, `.text-black`, `.bg-neutral-800`, `.text-neutral-400`
   - Tied to `dirtyCards` Set (existing JS variable)

1. **Message feedback:** `document.querySelectorAll('[data-django-message]')`

   - Plan 03 can render success/error/warning messages via Django `messages` framework
   - Messages auto-disappear after 4s without user intervention
   - Timing: 4s wait → 0.5s fade → DOM removal at 4.5s
   - Colors: green (success), red (error), amber (warning), blue (info) preserved

Both are "chrome" ready for the interactive state-management layer in Plan 03.
