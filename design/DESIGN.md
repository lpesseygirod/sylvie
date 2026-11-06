# Sylvie — Design Spec

This is how Sylvie must look and feel. Build the real interface to match it. The reference screens are in `design/reference/` (open them to see layout and copy) and on Lea's design canvas: https://claude.ai/artifact/QKWLdVJAuBY5pGZhfBwQJe

The reference files are mockups written in a design-tool format (`.dc.html`): use them for layout, spacing, colors and copy, not as production code. Rebuild the UI in the stack from the build plan (web UI served by FastAPI for chat; SwiftUI for the menu bar, panel and iPhone app).

If something in this spec conflicts with what works technically, keep the look as close as possible, write the change in `docs/decisions.md`, and tell Lea.

---

## 1. Look: "midnight glass"

Dark, calm, minimal. One accent color. Sylvie's "face" is a glowing animated orb. No emoji, no gradient backgrounds on panels (the orb is the only gradient), no clutter.

### Colors

| Token | Hex | Use |
| --- | --- | --- |
| `bg` | `#0E0F13` | App background (chat) |
| `bg-deep` | `#08090C` | Voice mode and iPhone background |
| `sidebar` | `#121318` | Conversation sidebar |
| `panel` | `#131419` | Right work panel |
| `surface` | `#16181E` | Cards, composer, chips |
| `surface-2` | `#1D2028` | Buttons, selected conversation |
| `bubble` | `#1F2330` | Lea's message bubbles, selected tabs |
| `border` | `#2A2E38` | Card and button borders |
| `divider` | `#23262F` | Lines between areas |
| `text` | `#ECEEF3` | Main text |
| `text-soft` | `#DADDE4` | Sylvie's answer text |
| `text-2` | `#C9CDD6` | Secondary text, conversation names |
| `muted` | `#9AA1B0` | Labels, hints, timestamps |
| `accent` | `#8EA8FF` | Primary buttons, links, state labels, focus |
| `accent-hover` | `#B7C6FF` | Link hover |
| `ok` | `#5FD39A` | "Running on this Mac" status dot |
| `muted-danger-bg` / `fg` | `#3A2226` / `#FFB4B4` | Mic muted state |

Orb colors: lilac `#C59BFF`, blue `#5B7CFF`, cyan `#6FE0FF`, core `#FFFFFF` → `#E6DEFF`.

All text must keep 4.5:1 contrast on its background. Support light mode later only if Lea asks; dark is the default.

### Type

- Headings, Sylvie's name, big voice transcripts: **Space Grotesk** 500–600.
- Everything else: **Inter** 400–600.
- Code and formulas: system monospace (`ui-monospace, Menlo`).
- Sizes: body 14–15 px, answer text in chat 14 px, voice transcript 30 px (Mac) / 22 px (iPhone), labels 12–13 px.
- Bundle the fonts locally (no Google Fonts requests at runtime: privacy rule).

### Shape and spacing

- Radius: buttons and inputs 10 px, cards 12–14 px, composer 18 px, wake panel 22 px, pills and round buttons full.
- Spacing on an 8 px rhythm; chat column max width 760 px.
- Touch/click targets at least 44 px.
- Icons: thin stroke icons (2 px), SF Symbols in SwiftUI, an equivalent stroke set in the web UI.

---

## 2. Screens

### 2.1 Chat workspace (like Claude)

Three areas side by side:

1. **Left sidebar (≈240 px):** Sylvie logo (small breathing orb + name), "New chat" button, search, conversations grouped by Today / This week / Older, and at the bottom "About Lea", "Learning dashboard" and a status line "Running on this Mac" with a green dot.
2. **Center chat:** header with conversation title, a small chip showing the model version (e.g. "Sylvie v003 · local"), and a **Voice mode** button that switches to the orb screen.
   - Lea's messages: right-aligned bubbles (`bubble` color, radius 16/16/4/16).
   - Sylvie's messages: no bubble, plain text on the background (like Claude).
   - Above Sylvie's answer, small muted activity notes when relevant: "Remembered: …", "Searched your course files and the web", "Played on Spotify".
   - Sources as small pill chips under the answer.
   - Under each answer: thumbs up, thumbs down, "Edit to what I wanted".
   - When Sylvie creates something to work on (notes, a draft email, a cheat sheet, code), she shows a **card** in the chat that opens it in the right panel.
   - **Composer** at the bottom: rounded box, placeholder "Message Sylvie, or just say “Sylvie”…", attach button, "Web search on" toggle chip, mic button (opens voice mode), round send button in `accent`.
3. **Right work panel (≈400 px, opens only when needed):** title, Preview / Edit tabs, close button, the document itself, and actions at the bottom (Copy, Export PDF, Save to notes). Lea can edit it directly. Closing it gives the chat the full width.

At narrow widths the areas stack (sidebar collapses to a button, panel becomes a full-screen sheet).

### 2.2 Voice mode (Jarvis orb)

Full window, `bg-deep` background, everything centered.

- Top: "Sylvie" + status pill "On your Mac · private".
- Center: the **orb** (300 px), the state label in small uppercase accent text (LISTENING / THINKING / SPEAKING), a sound-wave bar row while speaking, then what Lea said in big Space Grotesk text (live, with a blinking caret while listening), then Sylvie's answer below in softer text.
- Bottom: mute-mic button, big white **Stop** button, Open chat button; hint "Say “Sylvie, stop” to interrupt · ⌥ Space to open".
- Switching between chat and voice keeps the same conversation.

### 2.3 Wake panel (pops up on "Sylvie")

A floating panel like Spotlight, ≈620 px wide, centered near the top of the screen, over whatever Lea is doing. Dark translucent background with blur (`NSVisualEffectView` / material), radius 22, soft shadow.

- Row 1: mini orb (44 px, animated) + what Lea is saying, live.
- Row 2: Sylvie's written answer streaming in.
- For actions that send/delete/buy: a **confirmation card** (app + recipient, the exact content, Send / Edit / Cancel). Nothing is sent without Lea's "yes" or a click on Send.
- Footer: hint text + "Open chat" link that expands into the full chat window.
- Dismiss: Esc, click outside, or a few seconds after the answer finishes (unless a confirmation is waiting).

### 2.4 iPhone app

Same look as voice mode, in portrait.

- Top: "Sylvie" + status pill "Connected to your Mac" (green dot; amber "Can't reach your Mac" when offline).
- Center: orb (200 px), state label, transcript.
- A small card with the last answer.
- Bottom: keyboard button (type instead), big round **hold-to-talk** button, conversations button.
- Hint: "Or press the Action Button · “Hey Siri, Sylvie”".

### 2.5 Menu bar icon

A tiny orb glyph that shows state: dim (idle), glowing (listening), slowly turning (thinking), pulsing (speaking), crossed out (muted). Click for: Open chat, Mute mic, Pause learning, Dashboard, Quit.

### 2.6 Dashboard (Phase 6)

Not designed yet. Use the same tokens: sidebar navigation (About Lea, Memory, Corrections, Versions, Actions, Privacy), cards on `surface`, tables with `divider` lines. Ask Lea to review a first version.

---

## 3. The orb

The orb is the most important visual element. Build it once and reuse it (menu bar glyph, wake panel 44 px, iPhone 200 px, voice mode 300 px).

**Construction (layered, all blurred and blended with "screen"):**

1. Outer glow: soft radial halo in accent/lilac, extends ~25% beyond the orb.
2. Thin ring: 1 px accent at low opacity.
3. Three color blobs (lilac, blue, cyan), each a radial gradient offset from the center, blurred (~16 px at 300 px size), slowly rotating at different speeds and directions (≈12 s, 16 s reverse, 20 s) with a slight scale wobble.
4. Bright core: white → pale lilac radial center, slightly blurred, breathing (scale 0.96 ↔ 1.06).

In SwiftUI, build it with `Canvas`/`TimelineView` or layered `Circle`s with blur and `.blendMode(.screen)`; on the web, CSS radial gradients + `filter: blur` + `mix-blend-mode: screen` + keyframes (as in `reference/Voice.dc.html`). Use Metal only if performance needs it.

**States:**

| State | Motion |
| --- | --- |
| Idle | Very slow breathing (≈4 s), dim glow |
| Listening | Breathing faster (≈2.2 s); later, core size follows Lea's mic volume |
| Thinking | Blobs swirl fast (≈2.6 s per turn), core dims slightly |
| Speaking | Core pulses quickly (≈0.4 s), glow pulses; driven by Sylvie's actual audio level when available; sound-wave bars under the orb |
| Muted | Grey, no motion |

Transitions between states are smooth (≈300 ms ease), never jumps. Respect "Reduce motion": keep only a gentle opacity change.

---

## 4. Copy and tone in the interface

- Short, warm, plain. Sylvie speaks as "I", calls Lea by name only in greetings ("Hello Lea").
- Status lines are honest: "Running on this Mac", "Web search on", "Can't reach your Mac".
- Never fake data in the real app; these mockup texts (classes, Marta, the TVM sheet) are examples only.

---

## 5. Reference files

| File | Screen |
| --- | --- |
| `reference/Main.dc.html` | Chat workspace |
| `reference/Voice.dc.html` | Voice mode with orb and states |
| `reference/Panel.dc.html` | Wake panel with confirmation card |
| `reference/Phone.dc.html` | iPhone app |
