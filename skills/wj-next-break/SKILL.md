---
name: wj-next-break
description: Answer questions about the current or next class period, break, passing period, lunch, or bell at Walter Johnson High School (WJ / WJHS, Bethesda MD). Use whenever the user asks about Walter Johnson's schedule — "when does lunch end", "what period is it right now", "when's the next break", "is school on today", "what time do classes end" — or mentions WJ bells, periods, or dismissal. Knows all six WJ bell-schedule variants (regular, early dismissal, 2-hour delay, homeroom, two assembly variants) plus spring testing-day adjusted schedules, and relies on the agent's judgment — informed by the user's subscribed WJHS calendar, web search, and other context — to pick which one applies today.
---

# Walter Johnson HS — next-break resolver

This skill answers "when's the next break / class / lunch / bell at WJ?" by splitting the work:

1. **You (the agent) pick today's schedule type** using the user's WJHS calendar and judgment. This step is intentionally not automated because event titles are ambiguous, same-day weather decisions may not hit the calendar in time, and the value of a right answer beats the cost of one extra tool call.
2. **The script does the time math.** Given a schedule type and the current moment, it deterministically returns what segment we're in and when the next break starts and ends.

## Step 1 — pick the schedule type

The allowed values: `regular`, `early_dismissal`, `two_hour_delay`, `homeroom`, `assembly_double_3`, `assembly_3abc`. Or decide school isn't in session. For spring testing days (see below), use the testing-day schedule directly from the table in the "Spring 2026 Testing Schedules" section.

**Source: the user's subscribed `WJHS` calendar.** This is the user's iOS/iCloud subscription to the WJ webmaster's public iCal feed (`https://calendar.google.com/calendar/ical/wjhswebmaster%40gmail.com/public/basic.ics`). Use `calendar_search_v0` to find the calendar named `WJHS`, then `event_search_v0` over a window that includes today.

The WJHS calendar is a firehose — mostly games, prom, Fine Arts Festival, and similar after-school items. **Filter hard before interpreting.** Ignore anything that is clearly not about the structure of the school day. Schedule-affecting event titles look like:

- `Schools closed`, `No school`, `Holiday`, `Observance — no school`
- `Professional Day`, `Non-Instructional Day`, `Teacher Workday`
- `Early Release`, `Early Dismissal`, `Half Day`, `N-Hour Early Dismissal`
- `2-Hour Delay`, `Two-Hour Delay`, `Delayed Opening`, `Late Start`
- `Homeroom` (when indicating a homeroom-schedule day, not an after-school meeting)
- `Assembly` (when indicating a daytime assembly schedule)
- Testing-day keywords: `ELA 10`, `LS-MISA`, `Government`, `MAP R`, `MAP M`, `Algebra 1 MCAP`, `MCAP`, `Spring Testing` — check the "Spring 2026 Testing Schedules" section below for exact dates and schedules

Everything else — sports games, club meetings, concerts, prom, PTSA meetings, tryouts, the Fine Arts Festival, "Senior Week," etc. — does not affect the bell schedule and should be ignored.

**Same-day weather closures and delays.** MCPS announces these in the morning via their website, social media, and email, and they don't always land on the calendar in time. If the user asks on a morning when weather is a plausible factor (winter storm, hurricane warning, heat emergency), web-search "MCPS closed today" or "MCPS 2-hour delay today" before answering. Skip this check on clearly normal weather days.

### Mapping filtered events to schedule types

Apply judgment rather than pattern matching. A reasonable decision tree:

- Any event stating school is closed, a holiday, a non-instructional/professional day, or an observance that MCPS closes for → **not in session**. Tell the user and stop.
- Early Release / Early Dismissal / Half Day → `early_dismissal`.
- 2-Hour Delay / Delayed Opening / Late Start → `two_hour_delay`.
- Homeroom day indicator → `homeroom`.
- Daytime assembly schedule indicator: the WJ PDF defines two variants (`assembly_double_3` with one assembly block followed by a regular 3rd period, or `assembly_3abc` with 3A/3B/3C rotations). If the event title doesn't distinguish, ask the user or default to `regular` and note the uncertainty.
- Spring testing day (see table in "Spring 2026 Testing Schedules" below) → look up the exact schedule for that date and use it directly.
- Nothing notable, weekday → `regular`.
- Saturday or Sunday → **not in session**.

If two signals conflict (stale "Early Release" alongside a "No School — inclement weather" on the same day), **not in session wins**.

Use web search, prior conversation context, and any other available tool when an event title is genuinely ambiguous. A three-second search that resolves "Professional Day" → "no students" is worth it.

### Fallback when calendar tools aren't available

If you're running somewhere without `event_search_v0` (headless container, CI job, etc.), `scripts/next_break.py --fetch-ics --date YYYY-MM-DD` will HTTP-fetch the public iCal and print summary lines for events intersecting that date. Apply the same filtering and judgment to those strings.

## Step 2 — run the time-math script

Once you have the schedule type:

```bash
python scripts/next_break.py --schedule regular
```

Pass `--now 2026-04-22T11:18` to answer hypothetical or historical questions. Pass `--list` to print every remaining segment for the day (useful for "walk me through the afternoon" questions). Pass `--kind lunch` to restrict "next break" to lunch only.

Output is human-readable and can be returned verbatim or lightly rephrased. Example:

> Right now (11:18 AM) students are in **Lunch** (11:05 AM–12:00 PM).
>
> The next break between classes begins at **12:00 PM** and ends at **12:05 PM**.

If the schedule type isn't `regular`, surface that fact to the user in the first line.

## What counts as a "break"

Passing periods and lunch. Class, homeroom, and assembly blocks are not breaks. Passing periods are auto-computed as the gap between consecutive periods whose end time doesn't meet the next start time; lunch is an explicit period of kind `lunch` in the schedule JSON.

## Schedule variants in scope

All six transcribed from the official WJ PDF into `references/bell_schedules.json`:

| Key | PDF label |
|---|---|
| `regular` | Regular Schedule |
| `early_dismissal` | Early Dismissal |
| `two_hour_delay` | Two Hour Delay |
| `homeroom` | Homeroom Schedule |
| `assembly_double_3` | Assembly — Double 3rd Period |
| `assembly_3abc` | Assembly — 3A, 3B, 3C |

## Spring 2026 Testing Schedules

WJHS uses an **Adjusted Bell Schedule** on state and district testing days. These are hard-coded dates — not derived from the calendar at runtime. If the user asks about one of these dates, skip the calendar lookup and use the schedule below directly.

**Note:** Only students taking the relevant test arrive at 7:45 for the testing block. Students not being tested follow their normal schedule until the afternoon periods begin. The schedule below reflects the overall school day structure.

### AP Exams: May 4–15, 2026

AP exams run May 4–15. These occur alongside the regular bell schedule for non-AP students. AP exam times vary by subject; WJ follows College Board's published schedule. This does not alter the standard bell schedule for non-AP students.

### Date-specific adjusted schedules

#### Tuesday, April 28, 2026 — ELA 10
*Students in English 10 arrive at 7:45*

| Segment | Start | End |
|---|---|---|
| Testing Block | 7:45 AM | 10:50 AM |
| Lunch | 10:55 AM | 11:50 AM |
| Period 1 | 11:55 AM | 12:30 PM |
| Period 2 | 12:35 PM | 1:10 PM |
| Period 3 | 1:15 PM | 1:50 PM |
| Period 4 | 1:55 PM | 2:30 PM |

#### Wednesday, April 29, 2026 — ELA 10
*Students in English 10 arrive at 7:45*

| Segment | Start | End |
|---|---|---|
| Testing Block | 7:45 AM | 10:50 AM |
| Lunch | 10:55 AM | 11:50 AM |
| Period 5 | 11:55 AM | 12:30 PM |
| Period 6 | 12:35 PM | 1:10 PM |
| Common Sense Media Lesson | 1:15 PM | 1:50 PM |
| Period 7 | 1:55 PM | 2:30 PM |

#### Monday, May 4, 2026 — LS-MISA (Biology)
*Students in Biology classes arrive at 7:45*

| Segment | Start | End |
|---|---|---|
| Testing Block | 7:45 AM | 10:55 AM |
| Lunch | 10:55 AM | 11:50 AM |
| Period 1 | 11:55 AM | 12:30 PM |
| Period 2 | 12:35 PM | 1:10 PM |
| Period 3 | 1:15 PM | 1:50 PM |
| Period 4 | 1:55 PM | 2:30 PM |

#### Tuesday, May 5, 2026 — Government
*Students in Government courses arrive at 7:45*

| Segment | Start | End |
|---|---|---|
| Testing Block | 7:45 AM | 10:55 AM |
| Lunch | 10:55 AM | 11:50 AM |
| Period 5 | 11:55 AM | 12:30 PM |
| Period 6 | 12:35 PM | 1:10 PM |
| Wellness Period (Double 6th) | 1:15 PM | 1:50 PM |
| Period 7 | 1:55 PM | 2:30 PM |

#### Thursday, May 7, 2026 — MAP R (9th Grade)
*All 9th grade students arrive at 7:45*

| Segment | Start | End |
|---|---|---|
| Testing Block | 7:45 AM | 10:55 AM |
| Lunch | 10:55 AM | 11:50 AM |
| Period 1 | 11:55 AM | 12:30 PM |
| Period 2 | 12:35 PM | 1:10 PM |
| Period 3 | 1:15 PM | 1:50 PM |
| Period 4 | 1:55 PM | 2:30 PM |

#### Friday, May 8, 2026 — MAP M (Algebra 1)
*All students in Algebra 1 arrive at 7:45*

| Segment | Start | End |
|---|---|---|
| Testing Block | 7:45 AM | 10:55 AM |
| Lunch | 10:55 AM | 11:50 AM |
| Period 5 | 11:55 AM | 12:30 PM |
| Period 6 | 12:35 PM | 1:10 PM |
| WJ Climate Survey (in 6th Period) | 1:15 PM | 1:50 PM |
| Period 7 | 1:55 PM | 2:30 PM |

#### Monday, May 11, 2026 — Algebra 1 MCAP
*All students in Algebra 1 arrive at 7:45*
*Note: longer testing block; afternoon periods are shortened to 15 minutes each*

| Segment | Start | End |
|---|---|---|
| Testing Block | 7:45 AM | 11:15 AM |
| Lunch | 11:15 AM | 12:10 PM |
| Period 1 | 12:15 PM | 12:30 PM |
| Period 2 | 12:35 PM | 12:50 PM |
| Period 3 | 12:55 PM | 1:10 PM |
| Period 4 | 1:15 PM | 1:30 PM |
| Period 5 | 1:35 PM | 1:50 PM |
| Period 6 | 1:55 PM | 2:10 PM |
| Period 7 | 2:15 PM | 2:30 PM |

### How to use testing-day schedules with the script

Since testing schedules are date-specific and not yet in `references/bell_schedules.json`, compute the answer directly from the table above and tell the user which segment they're in.

## Verifying the schedule is still current

The bell-schedule PDF is linked from https://www.montgomeryschoolsmd.org/schools/wjhs/about/bells. If the user reports the times are wrong, re-fetch that page, grab the current PDF, and update `references/bell_schedules.json`. The schedule changes rarely (on the order of once every few years) but it does change — and when it does, the skill breaks silently.

The spring testing calendar is typically published by WJHS administration. The 2026 version lives at:
https://docs.google.com/document/d/1I_pIpe3FfQYR5UeH99Ol-yybDsSU8jweygt-Kt2r4ic/

## Tests

```bash
python -m pytest scripts/test_next_break.py -v
```

Tests cover time-math edge cases (exact start/end moments, before/after school, every schedule variant) and the iCal fallback parser. Add a test for any behavior change before modifying `next_break.py` — the whole point of the split design is that the time math is deterministic and testable.
