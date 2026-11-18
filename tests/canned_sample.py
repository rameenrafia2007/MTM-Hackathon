"""Canned reader outputs for the sample pack and the two practice messages (tests only)."""
from .stubs import C, MS, OC, out

SAMPLE = out(
    MS((1, "Dean's office, email", "9 Nov"), (2, "Margalla Event Halls, email", "3 Nov"),
       (3, "Caterer, WhatsApp", "20 Oct"), (4, "Caterer, WhatsApp", "12 Nov"),
       (5, "Judging coordinator, email", "11 Nov"), (6, "Finance, email", "10 Nov"),
       (7, "Student society, WhatsApp", "13 Nov"),
       (8, "A judge, WhatsApp", "14 Nov", "n", "judge may arrive late")),
    [
        C(1, "duration_minutes", 240, ev="a 4-hour hackathon", area="schedule"),
        C(1, "student_count", 150, ev="150 students", area="venue"),
        C(1, "team_size", 3, ev="teams of 3", area="judging"),
        C(1, "total_budget", 650000, ev="The budget is Rs 650,000, all in.", area="budget", unit="PKR"),
        C(1, "staff_count", 10, ev="Ten staff will be on duty", area="venue"),
        C(1, "break_minutes", 15, ev="15-minute prayer break", area="schedule"),
        C(2, "venue_option", 40000, "Room D", "Room D seats 80 at Rs 40,000", "quote", "venue", cap=80),
        C(2, "venue_option", 120000, "Hall A", "Hall A seats 200 at Rs 120,000", "quote", "venue", cap=200),
        C(2, "venue_option", 150000, "Hall B", "Hall B seats 200 at Rs 150,000 | Halls B and C have a standby generator",
          "quote", "venue", cap=200, feat=["standby generator"]),
        C(2, "venue_option", 220000, "Hall C", "Hall C seats 300 at Rs 220,000 | Halls B and C have a standby generator",
          "quote", "venue", cap=300, feat=["standby generator"]),
        C(3, "unit_price", 1000, "standard", "Standard lunch box Rs 1,000 per head", "quote", "food"),
        C(4, "unit_price", 1200, "standard", "Standard lunch box ab Rs 1,200 per head", "correction", "food"),
        C(4, "unit_price", 800, "basic", "basic wala Rs 800", "quote", "food"),
        C(5, "judges_confirmed", 20, ev="20 judges have confirmed", area="judging"),
        C(5, "judge_fee", 5000, ev="honorarium is Rs 5,000 each", area="judging"),
        C(5, "minutes_per_team", 15, ev="15 minutes per team", area="judging"),
        C(5, "max_teams_per_judge", 3, ev="no more than three teams", area="judging"),
        C(6, "committed_spend", 100000, "prizes", "prizes Rs 100,000", area="budget"),
        C(6, "committed_spend", 20000, "backup internet", "backup internet Rs 20,000", area="budget"),
        C(6, "committed_spend", 10000, "printing", "printing Rs 10,000", area="budget"),
        C(7, "requested_spend", 50000, "DJ", "Can we get a DJ for the closing? The quote is Rs 50,000.", "request", "budget"),
    ])

PRACTICE_CHANGE = out(MS((1, "Finance", "")),
                      [C(1, "committed_spend", 15000, "printing", "printing bill came in at Rs 15,000",
                         "correction", "budget")])
PRACTICE_WRONG = out(MS((1, "Caterer", "")),
                     [C(1, "ordered_quantity", 100, "boxes", "rounded your order down to 100 boxes", "fact", "food")])
