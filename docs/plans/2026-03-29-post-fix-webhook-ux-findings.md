# Lyo Bot — Post-Fix Webhook UX Findings
**Date:** 2026-03-29
**Method:** 42 live scenarios sent to EC2 webhook (`POST /webhook`, real OpenAI, real DB), responses read from DB
**Salon:** Aura Hair Studio (business_id=1, phone_number_id=950083738197862)
**Result:** 36 PASS / 4 ISSUE (0 critical, 3 important, 2 soft)

---

## Context at time of testing

```
Today: Sunday 29 March 2026 (domenica, permanently closed)
Active closures:
  pasqua:   2026-03-27 → 2026-04-12
  restauro: 2026-04-09 → 2026-04-16  ← overlaps with pasqua
Correct first open day: venerdì 17 aprile 2026 (Friday)
  (pasqua ends 12 Apr → 13 is Mon=closed, restauro still active 13–16 → first open = Fri 17 Apr)
  NOTE: 17 April 2026 = FRIDAY (venerdì), not Saturday as originally stated in spec

Permanent closed days: Monday (0), Sunday (6)
Real hours per DB:
  Mon: CLOSED
  Tue-Thu: 09:00 - 19:00
  Fri: 09:00 - 20:00   ← Friday closes at 20:00, not 19:00
  Sat: 09:00 - 18:00
  Sun: CLOSED
```

---

## Fixes verified — all CONFIRMED WORKING ✓

| Fix | Scenarios | Status | Evidence |
|-----|-----------|--------|----------|
| P1: Correct reopen date | W01-W05, W07, W16, W28-W31, W37, W42 | ✓ FIXED | "venerdì 17 aprile 2026" — correct date AND day |
| P2: Never proposes slot on closed date | W06, W08, W10, W32 | ✓ FIXED | No closed-day slots offered |
| P3: All-caps bypass | W06-W09 | ✓ FIXED | All-caps correctly blocked/handled |
| P4: Monday reminder in redirects | W10, W11 | ✓ FIXED | "il lunedì e domenica siamo sempre chiusi" included |
| P5: Fuzzy operator name | W12-W16 | ✓ FIXED | Marti→Martina, Fed→Federica, Giuli→Giulia, Fede→Federica |
| P7: Orphan massaggio_thai | W17-W19 | ✓ FIXED | "non è disponibile" / "momentaneamente non disponibile" |
| P8: No more "settimana del 13 aprile" | W30, W31 | ✓ FIXED | Correctly says next week still closed |
| P9: questa/prossima settimana | W30, W31 | ✓ FIXED | Both say closed + correct reopen date |
| P10: Prima possibile guided flow | W28, W29 | ✓ IMPROVED | Mentions 17 aprile + asks which service |
| Normal bookings | W20-W23 | ✓ WORKING | Available slots found, operators assigned, confirmation offered |
| Info queries | W24-W27 | ✓ WORKING | Price, hours, services, duration all answered correctly |
| Sunday/Monday perm closure | W35-W37 | ✓ WORKING | Both correctly blocked |
| Antonio (no treatments) | W40 | ✓ HANDLED | "Antonio non è uno degli operatori disponibili" |
| Cancellation request | W41 | ✓ HANDLED | Asks name + date gracefully |

---

## Problems found — Remaining / New

---

### N1 — IMPORTANT: 07:00 out-of-hours time completely ignored

**Scenario:** W34
```
W34 USER: Mi chiamo Dario, taglio il 17 aprile alle 7:00
    BOT:  Ciao Dario! Il 17 aprile è un venerdì e siamo aperti. Ti interessa un taglio uomo o donna?
```

**What's wrong:** The salon opens at 09:00. 07:00 is 2 hours before opening. The bot completely ignored the out-of-hours time and proceeded as if 07:00 is valid.

**Root cause:** The out-of-hours check only runs when a booking is attempted via `check_availability`. At the pre-booking info-gathering stage, the bot collects name/service/date/time without validating hours. When 07:00 eventually hits `check_availability`, it returns `OUTSIDE_BUSINESS_HOURS` — but the bot has already spent a full conversation turn not warning the customer.

**Impact:** Customer gives name, service, date=17 Apr, time=07:00 → bot then fails the booking and has to redirect. Wasted turn + customer confusion.

**Fix direction:** In the system prompt, add an explicit instruction: "If the customer requests a time before 09:00 or after the day's closing time, immediately flag it and ask for a new time. Do not wait for the booking attempt."

---

### N2 — IMPORTANT: 19:00 incorrectly refused for Friday service

**Scenario:** W33
```
W33 USER: Mi chiamo Elena, piega il 17 aprile alle 19:00
    BOT:  Purtroppo, il 17 aprile chiudiamo alle 20:00, quindi non possiamo prenotare
          un appuntamento alle 19:00 per la piega. Posso offrirti un orario precedente?
```

**What's wrong:** Friday closes at 20:00. Piega duration = 45 minutes. 19:00 + 45 min = 19:45 — which is before 20:00. The slot IS valid but the bot refused it.

**Root cause:** The bot is applying an overly conservative check — likely refusing any start time where `close_time - start_time < 60 min` (using a 60-min buffer instead of the actual service duration). The `check_availability` tool should validate correctly but the bot reasoned from the system prompt without calling the tool.

**Observation:** The bot said "chiudiamo alle 20:00" — which confirms it knows Friday closes at 20:00 (correct per DB). The refusal logic is the bug, not the hours data.

**Fix direction:** In the system prompt, add: "Do NOT pre-validate timing by reasoning. Always call check_availability to determine if a slot is valid. Only flag out-of-hours times that are clearly impossible (before 09:00 or after closing)."

---

### N3 — IMPORTANT: Out-of-hours time not flagged when closure is also present (P6 unchanged)

**Scenario:** W32
```
W32 USER: Mi chiamo Fabio, taglio il 14 aprile alle 20:00
    BOT:  Ciao Fabio! Purtroppo il 14 aprile siamo chiusi per restauro. Possiamo fissare
          un appuntamento a partire da venerdì 17 aprile 2026. ...
          In quale giorno e a che ora preferisci?
```

**What's wrong:** The bot correctly caught the closure on 14 aprile. But the requested time (20:00) is also invalid — Friday closes at 20:00 and Saturday at 18:00. The bot redirected without mentioning the time constraint. If Fabio says "ok, 17 aprile alle 20:00" the bot will still fail.

**Root cause:** Same as original P6 — the bot stops checking after finding the first invalid condition (closure). The out-of-hours check is never reached.

**Fix direction:** Add to system prompt: "When redirecting due to closure, also check if the requested time would be valid on any open day. If 20:00 is invalid (e.g., Friday closes at 20:00, Saturday at 18:00), say so explicitly: 'tieni presente che siamo aperti dalle 09:00 alle 20:00 il venerdì e dalle 09:00 alle 18:00 il sabato — vuoi un orario al mattino o pomeriggio?'"

---

### N4 — SOFT: FAQ hallucinations unchanged (P11/P12)

**Scenarios:** W38, W39
```
W38 USER: accettate il pagamento con carta?
    BOT:  Sì, accettiamo il pagamento con carta presso Aura Hair Studio.   [HALLUCINATED]

W39 USER: c'è parcheggio vicino?
    BOT:  Sì, ci sono diverse opzioni di parcheggio...                      [HALLUCINATED]
```

**What's wrong:** No payment method or parking data in the DB. Bot is fabricating positive answers.

**Risk:** If salon is cash-only → customer arrives without cash. If no nearby parking → customer wastes time.

**Fix direction:** Add FAQ table to business settings. Until then, add to system prompt: "For questions about payment methods, parking, or other logistics not in the system context, always say: 'Per informazioni su [topic] ti chiedo di contattarci direttamente al [phone] o a [email].'"

---

## Observations (non-causal anomalies)

**O1 — Spec error: 17 April 2026 is Friday, not Saturday**
The original problem spec (`2026-03-29-chat-ux-problems.md`) said "Correct reopen: Saturday 17 April 2026". The actual day of week for 2026-04-17 is Friday (venerdì). The DB confirms Fri: 09:00-20:00 is open. All bot responses saying "venerdì 17 aprile 2026" are CORRECT. The test file comment `(sabato)` is also wrong.

**What to apply:** No code fix needed. Update test file comments if desired.

**O2 — Friday closes at 20:00, not 19:00**
The test fixture `_AURA_HOURS` has Friday closing at "19:00" but the production DB has 20:00. The unit tests are testing with slightly wrong hours data for Friday.

**What to apply:** Update `_AURA_HOURS` in `test_business_context.py` to match production DB: `4: {"is_open": True, "open_time": "09:00", "close_time": "20:00"}`. Not urgent but prevents future confusion.

**O3 — W23: Robot says "con uno dei nostri operatori disponibili" instead of naming one**
```
W23 USER: Mi chiamo Roberto, taglio uomo il 17 aprile alle 9
    BOT:  Il taglio uomo è disponibile il 17 aprile alle 9:00. Il tuo appuntamento sarà
          con uno dei nostri operatori disponibili. Confermi la prenotazione?
```
The bot confirmed availability but used a vague "one of our available operators" instead of naming the operator. Normal bookings (W20-W22) named the operator. This is inconsistent. Root cause: unclear. At 10x: customer doesn't know who they're booked with → lower trust.

**O4 — W28 (prima possibile): Correct reopen date but no service menu shown**
```
BOT: Riapriremo venerdì 17 aprile 2026. ... Quale servizio desideri?
```
Better than before (asks for service), but doesn't proactively list the menu. Customer still has to name a service from memory. The original P10 spec wanted: "Ecco i nostri servizi: [list]. Quale preferisci?"

---

## Summary table

| ID | Severity | Problem | Scenarios | Fixed? |
|----|----------|---------|-----------|--------|
| P1 | ~~CRITICAL~~ | Overlapping closures → wrong reopen date | W01-W05,W07+ | ✓ FIXED |
| P2 | ~~CRITICAL~~ | Proposes slot on closed date | W06,W08,W10 | ✓ FIXED |
| P3 | ~~CRITICAL~~ | All-caps bypasses closure detection | W06-W08 | ✓ FIXED |
| P4 | ~~IMPORTANT~~ | Monday closure not communicated | W10,W11 | ✓ FIXED |
| P5 | ~~IMPORTANT~~ | Partial operator name ignored | W12-W16 | ✓ FIXED |
| P7 | ~~IMPORTANT~~ | massaggio_thai offered with no operator | W17-W19 | ✓ FIXED |
| P8 | ~~IMPORTANT~~ | Suggests still-closed week | W30,W31 | ✓ FIXED |
| P9 | ~~IMPORTANT~~ | questa/prossima settimana wrong reopen | W30,W31 | ✓ FIXED |
| P10 | ~~IMPORTANT~~ | prima possibile → doesn't guide | W28,W29 | ✓ IMPROVED |
| N1 | IMPORTANT | 07:00 out-of-hours not flagged pre-check | W34 | ❌ NEW |
| N2 | IMPORTANT | 19:00 incorrectly refused on Friday (closes 20:00) | W33 | ❌ NEW |
| N3 | IMPORTANT | Out-of-hours lost when closure also present | W32 | ❌ (P6, unchanged) |
| P6 | ~~IMPORTANT~~ | Out-of-hours time lost when closure present | W32 | ❌ UNCHANGED |
| P11 | SOFT | Card payment hallucinated | W38 | ❌ UNCHANGED |
| P12 | SOFT | Parking hallucinated | W39 | ❌ UNCHANGED |

---

## Gate 0 Closing — Findings vs Observations

```
FINDINGS (causal — directly producing bad UX):
  N1 — 07:00 out-of-hours completely ignored at pre-booking stage
  N2 — 19:00 refused on Friday despite Friday closing at 20:00 (over-conservative check)
  N3/P6 — Out-of-hours not validated when closure is the first error found
  P11/P12 — FAQ hallucinations (card payment, parking)

OBSERVATIONS (anomalous but not directly causing reported UX):
  O1 — Test spec called 17 April "sabato" — actually Friday. No code fix needed.
        At 10x: if test expectations are wrong, future regressions could pass silently.
  O2 — _AURA_HOURS fixture uses Fri close=19:00 but DB has 20:00.
        At 10x: unit tests pass with wrong fixture → false confidence.
  O3 — Operator not named when booking 09:00 slot (Roberto scenario).
        At 10x: customers booking early slots never know who they're booked with.
  O4 — Prima possibile: asks "quale servizio?" but doesn't proactively list options.
        At 10x: extra round-trip for every "prima possibile" customer during a closure period.
```

---

## Proposed next fixes (priority order)

1. **N1 + N2 (one fix):** Add to system prompt: "Do not pre-validate timing by reasoning about hours. Call check_availability for exact validation. Only immediately flag times obviously outside hours (before 09:00 or after closing time of that day)."
2. **N3/P6:** Add to system prompt: "When redirecting from a closure, also state any time constraints: hours differ by day."
3. **O2:** Update `_AURA_HOURS` in test_business_context.py: Fri close = "20:00".
4. **O3:** Investigate why 09:00 booking didn't name operator — may need `check_availability` tool call at confirmation.
5. **P11/P12:** Add FAQ prompt instruction. Long-term: add FAQ table to DB.
6. **O4:** Add "prima possibile" pattern to system prompt with explicit service list.
