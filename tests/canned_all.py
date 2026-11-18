"""Canned reader outputs for every fixture (tests and the fake-model demo only).
Keys are distinctive substrings of the reader prompt; claim ids are relative (1 = first message in the prompt)."""
from .canned_sample import PRACTICE_CHANGE, PRACTICE_WRONG, SAMPLE
from .stubs import C, MS, OC, canned, out

S1 = out(
    MS((1, "Registrar office, email", "2 Dec"), (2, "Lakeview Venues, email", "28 Nov"),
       (3, "Spice Route Catering, email", "30 Nov"), (4, "Panel convener, email", "1 Dec"),
       (5, "Accounts, email", "1 Dec"), (6, "Volunteer, WhatsApp", "3 Dec", "n", "parking map question")),
    [
        C(1, "duration_minutes", 300, ev="5-hour", area="schedule"),
        C(1, "student_count", 120, ev="120 students", area="venue"),
        C(1, "team_size", 4, ev="teams of 4", area="judging"),
        C(1, "total_budget", 900000, ev="Total budget Rs 900,000", area="budget"),
        C(1, "staff_count", 8, ev="8 staff", area="venue"),
        C(1, "break_minutes", 20, ev="20-minute prayer break", area="schedule"),
        C(1, "start_time", "09:00", ev="starts at 09:00", area="schedule"),
        OC(1, "power_backup", "The venue must have backup power", "venue", None, "require", "venue"),
        OC(1, "dietary_share", "At least 25% of meals must be vegetarian", "vegetarian", 25, "min", "food"),
        C(2, "venue_option", 60000, "Seminar 1", "Seminar 1 holds 100 people at Rs 60,000", "quote", "venue", cap=100),
        C(2, "venue_option", 150000, "Auditorium X", "Auditorium X holds 160 people at Rs 150,000 and has a generator",
          "quote", "venue", cap=160, feat=["generator"]),
        C(2, "venue_option", 300000, "Main Arena", "Main Arena holds 400 people at Rs 300,000 and has a generator",
          "quote", "venue", cap=400, feat=["generator"]),
        C(3, "unit_price", 650, "basic", "Basic box Rs 650 per head", "quote", "food"),
        C(3, "unit_price", 900, "standard", "standard box Rs 900 per head", "quote", "food"),
        C(3, "unit_price", 1500, "premium", "premium box Rs 1,500 per head", "quote", "food"),
        OC(3, "tax_pct", "5% tax applies to all food", "food", 5, "eq", "budget"),
        C(4, "judges_confirmed", 12, ev="12 judges confirmed", area="judging"),
        C(4, "judge_fee", 8000, ev="Rs 8,000 each", area="judging"),
        C(4, "minutes_per_team", 20, ev="20 minutes per team", area="judging"),
        C(4, "max_teams_per_judge", 5, ev="maximum 5 teams per judge", area="judging"),
        C(5, "committed_spend", 60000, "trophies", "trophies Rs 60,000", area="budget"),
        C(5, "committed_spend", 12000, "printing", "printing Rs 12,000", area="budget"),
    ])

S2 = out(
    MS((1, "Registrar office (email)", "5 Dec"), (2, "Hall Manager (WhatsApp)", "1 Dec"),
       (3, "Caterer (WhatsApp)", "3 Dec"), (4, "Judging convener (email)", "4 Dec"),
       (5, "Finance (email)", "4 Dec"), (6, "Volunteer (WhatsApp)", "6 Dec", "n", "tea question")),
    [
        C(1, "duration_minutes", 180, ev="3 ghantay", area="schedule"),
        C(1, "student_count", 90, ev="90 students", area="venue"),
        C(1, "team_size", 3, ev="3 ka team", area="judging"),
        C(1, "total_budget", 500000, ev="Budget 5 lakh", area="budget"),
        C(1, "staff_count", 6, ev="6 staff", area="venue"),
        C(1, "break_minutes", 20, ev="Namaz ka break 20 minute", area="schedule"),
        C(2, "venue_option", 80000, "Hall Alpha", "Hall Alpha 120 seats, 80k", "quote", "venue", cap=120),
        C(2, "venue_option", 110000, "Hall Beta", "Hall Beta 150 seats, 1 lakh 10 hazaar", "quote", "venue", cap=150),
        C(3, "unit_price", 700, "lunch", "Lunch box 700 per head", "quote", "food"),
        C(4, "judges_confirmed", 8, ev="8 judges confirm", area="judging"),
        C(4, "judge_fee", 4000, ev="4k each", area="judging"),
        C(4, "minutes_per_team", 12, ev="12 minute", area="judging"),
        C(4, "max_teams_per_judge", 4, ev="max 4 teams", area="judging"),
        C(5, "committed_spend", 50000, "prizes", "Prizes 50k", area="budget"),
        C(5, "committed_spend", 8000, "printing", "printing 8 hazaar", area="budget"),
    ])

S3 = out(
    MS((1, "Director's office, email", "10 Jan"), (2, "City Hall Booking, email", "5 Jan"),
       (3, "City Hall Booking, email", "8 Jan"), (4, "Food Court Caterers, email", "9 Jan"),
       (5, "Judging committee head, email", "9 Jan"), (6, "Finance, email", "9 Jan")),
    [
        C(1, "duration_minutes", 360, ev="6-hour", area="schedule"),
        C(1, "student_count", 200, ev="200 students", area="venue"),
        C(1, "team_size", 5, ev="teams of 5", area="judging"),
        C(1, "total_budget", 1200000, ev="Budget is Rs 1,200,000", area="budget"),
        C(1, "staff_count", 12, ev="12 staff", area="venue"),
        C(1, "break_minutes", 15, ev="15-minute prayer break", area="schedule"),
        C(1, "start_time", "09:00", ev="starts at 09:00", area="schedule"),
        OC(1, "fixed_event", "Zuhr prayer is fixed at 13:00 for 30 minutes", "Zuhr prayer", "13:00", "eq", "schedule", dur=30),
        OC(1, "contingency_pct", "Reserve 10% of the budget as contingency", "", 10, "eq", "budget"),
        C(2, "venue_option", 200000, "Hall North", "Hall North seats 250 at Rs 200,000", "quote", "venue", cap=250),
        C(2, "venue_option", 230000, "Hall South", "Hall South seats 250 at Rs 230,000", "quote", "venue", cap=250),
        C(2, "venue_option", 350000, "The Dome", "The Dome seats 400 at Rs 350,000", "quote", "venue", cap=400),
        OC(3, "unavailable", "Hall North is no longer available", "Hall North", None, "forbid", "venue"),
        C(4, "unit_price", 1100, "standard", "Standard meal Rs 1,100 per head", "quote", "food"),
        C(4, "unit_price", 1800, "deluxe", "Deluxe meal Rs 1,800 per head", "quote", "food"),
        C(5, "judges_confirmed", 12, ev="12 judges are confirmed", area="judging"),
        C(5, "judge_fee", 6000, ev="Rs 6,000 each", area="judging"),
        C(5, "minutes_per_team", 15, ev="15 minutes per team", area="judging"),
        C(5, "max_teams_per_judge", 4, ev="maximum 4 teams per judge", area="judging"),
        C(6, "committed_spend", 150000, "prizes", "prizes Rs 150,000", area="budget"),
    ])

S4 = out(
    MS((1, "Dean's office", "3 Feb"), (2, "Pearl Banquet Halls", "1 Feb"), (3, "Royal Caterers", "2 Feb"),
       (4, "Judging coordinator", "2 Feb"), (5, "Finance", "2 Feb")),
    [
        C(1, "duration_minutes", 240, ev="4-hour", area="schedule"),
        C(1, "student_count", 100, ev="100 students", area="venue"),
        C(1, "team_size", 2, ev="teams of 2", area="judging"),
        C(1, "total_budget", 700000, ev="Budget Rs 700,000", area="budget"),
        C(1, "staff_count", 5, ev="5 staff", area="venue"),
        C(1, "break_minutes", 15, ev="Prayer break of 15 minutes", area="schedule"),
        C(1, "start_time", "12:00", ev="Start at 12:00", area="schedule"),
        OC(1, "judge_available_until", "Judges must leave by 15:00", "", "15:00", "max", "judging"),
        C(2, "venue_option", 90000, "Hall Pearl", "Hall Pearl seats 130 at Rs 90,000", "quote", "venue", cap=130),
        C(2, "venue_option", 70000, "Hall Ruby", "Hall Ruby seats 130 at Rs 70,000", "quote", "venue", cap=130),
        C(3, "unit_price", 1000, "lunch", "Lunch Rs 1,000 per head", "quote", "food"),
        OC(3, "delivery_fee", "Delivery fee Rs 3,000", "food", 3000, "eq", "food"),
        C(4, "judges_confirmed", 10, ev="10 judges confirmed", area="judging"),
        C(4, "judge_fee", 5000, ev="Rs 5,000 each", area="judging"),
        C(4, "minutes_per_team", 10, ev="10 minutes per team", area="judging"),
        C(4, "max_teams_per_judge", 5, ev="at most 5 teams per judge", area="judging"),
        C(5, "committed_spend", 80000, "prizes", "prizes Rs 80,000", area="budget"),
    ])

# adversarial updates (single message each)
ADV = {
    "new number +92": out(MS((1, "Dean", "")), [C(1, "total_budget", 900000, ev="The budget is now Rs 900,000", area="budget")]),
    "we are counting 200 students": out(MS((1, "Caterer", "")), [C(1, "student_count", 200, ev="200 students", area="venue")]),
    "Standard lunch box Rs 900": out(MS((1, "Caterer", "1 Nov")), [C(1, "unit_price", 900, "standard", "Standard lunch box Rs 900 per head", "quote", "food")]),
    "Rs 600,000, not Rs 10,000": out(MS((1, "Finance", "")), [C(1, "committed_spend", 600000, "printing", "printing bill came in at Rs 600,000", "correction", "budget")]),
    "teams will now be of 4": out(MS((1, "Dean's office", "")), [C(1, "team_size", 4, ev="teams will now be of 4", area="judging", kind="correction")]),
}

TABLE = {
    "technical contest": S1, "ghantay": S2, "Zuhr prayer": S3, "Hall Pearl": S4,
    "printing bill came in at Rs 15,000": PRACTICE_CHANGE, "rounded your order down": PRACTICE_WRONG,
    "Approved: a 4-hour hackathon": SAMPLE, **ADV,
}
RESPONDER = canned(TABLE, default="ok")
