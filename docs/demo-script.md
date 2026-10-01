# Demo video script (about 2 minutes)

Record on the live API. Before filming: call `/health` once so the container is awake (a cold
start takes about 30 seconds), generate two tokens
(`uv run python scripts/get_demo_token.py` and `... --admin`; never show the terminal output on
screen), and open Swagger at `/docs`, Google Calendar and a private notes page for the ids. Use a time
slot a few days out, and cancel the demo booking afterwards so the calendar stays clean. Keep the
demo user's token and the password off screen.

Business hours: Monday to Friday 09:00-18:00, Saturday 10:00-16:00, closed Sunday.

| Time | On screen | Say |
|---|---|---|
| 0:00 | README top, then `/docs` | "This is an AI receptionist API. Customers chat in plain English, an agent proposes a booking, and nothing is booked until they say yes. Live on a free tier, so the first call can take 30 seconds." |
| 0:15 | Swagger, **Authorize**, paste the customer token | "Sign-ups are closed, so I use a demo user. The API only verifies Supabase tokens." |
| 0:25 | `POST /chat`: "I'd like a haircut on Monday at 10am" | "The agent reads the message, checks the slot and proposes. Nothing is written yet." |
| 0:45 | `GET /bookings` returns an empty list | "Proof: no booking exists after the proposal." |
| 0:55 | `POST /chat`: "yes" | "Only an explicit yes on the next turn books it. The check is a fixed allow-list, not the model." |
| 1:05 | Google Calendar: the new "Haircut - ..." event | "The event is already in Google Calendar. Booking and calendar entry succeed or fail together." |
| 1:20 | `POST /bookings/{id}/cancel` using an id that is **not** the customer's (copy it from the admin view, prepared beforehand) | "Another customer's booking. Every query filters by the user id from the token." |
| 1:35 | Response: 404 `booking_not_found`, standard error body | "Refused, and it reveals nothing about whether that booking exists." |
| 1:40 | Swagger **Authorize** with the admin token, `GET /admin/bookings` | "The admin sees all bookings. Admin rights come from the database, not from the token." |
| 1:50 | Swagger **Authorize** with the customer token, `GET /admin/bookings` returns 403 | "The customer gets 403 on the same route." |
| 1:55 | Cancel the demo booking, show the calendar event gone, README highlights | "Cancel removes the calendar event too. Real findings are in the README: a booking deadlock fixed with an advisory lock, and live Google tests that caught the API's behaviour." |

## Notes before recording

- The cross-user step needs a booking id that belongs to someone else. Create one as the admin
  user beforehand (an admin is also a customer), and keep its id on a notes page. **Verify the
  exact response of that call first** (it should be 404 `booking_not_found`; see
  [security.md](security.md), API1) and adjust the narration to match.
- All demo bookings are real rows and real calendar events in production. Cancel every one you
  create, in the order: customer booking, admin booking.
- If the chat answer is the fallback "Sorry, I can't process that right now", Gemini is down or
  out of quota. Nothing was changed; wait a minute and retry.
- The chat rate limit is 10 requests per 60 seconds per user, so do not rehearse and film in one
  minute.
