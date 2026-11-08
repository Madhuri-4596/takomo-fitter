# Today's fitting tool as code: rules and where each one comes from

`takomo_fit/baseline.py` implements Takomo's 12-question iron tool. Sources: Takomo's participant pack, `takomo_products.json`, and Jonas Koisti's corrections of 5 November 2026, which describe the live tool. Rules marked **interpretation** are our reading where neither source gives the detail. Each rule is one constant or one function, so it is easy to change.

## Head choice

Total = skill match + speed points + characteristics + forgiveness penalty. The two best heads are shown.

| Part | Rule | Source |
|---|---|---|
| Skill match | 5 − \|score bucket − head's target bucket\| | pack |
| Speed points | lookup table below | Takomo, 5 Nov |
| Over-92 cap | a golfer scoring over 92 keeps their real swing bucket, but in buckets 4 and 5 the speed points are capped at 3 | Takomo, 5 Nov |
| Characteristics | add the head's rating for each chosen priority; forged feel counts double; "less offset" adds 5 − offset rating | pack; "5 − rating" is our interpretation of "inverse" |
| Forgiveness penalty | if "forgiveness" is a priority, 301 CB and 301 MB lose 1 point | Takomo, 5 Nov |
| Ties | higher skill match first, then product order (101 → 301 MB) | interpretation |

Speed points:

| Swing bucket | 101 MKII | 201 MKII | 201T MKII | 301 CB | 301 MB |
|---|---|---|---|---|---|
| 1 (under 60 mph / under 120 yd) | 7 | 5 | 3 | 1 | 1 |
| 2 (60–70 / 120–135) | 4 | 5 | 3 | 1 | 1 |
| 3 (71–80 / 135–155) | 3 | 4 | 5 | 3 | 3 |
| 4 (81–90 / 155–175) | 2 | 3 | 4 | 5 | 5 |
| 5 (over 90 / over 175) | 1 | 2 | 3 | 5 | 5 |

## Shaft

Flex from the swing bucket; graphite for ladies and senior flex, all women and anyone 70 or older; within material and flex, match desired flight and tempo, then flight only, then tempo only, then the nearest available flex. Ties prefer the head's stock shaft models. (Pack.)

## Length and lie: the PING colour code chart

Takomo works from a version of PING's colour code chart, the industry's standard static fitting chart (Takomo, 5 Nov).

- **Length** comes from height. PING's length row and the pack's table are the same (Takomo is confirming that their version also takes length from height only): under 155.5 cm −1.5 in, 155.5–160.5 −1.0, 160.5–168.5 −0.5, 168.5–183.5 standard, 183.5–191.5 +0.5, 191.5–196.5 +1.0, from 196.5 +1.5. Ladies-flex shafts get 1 in added back. Arm length enters through the lie, not as a separate length correction.
- **Lie** comes from height and wrist-to-floor together: PING's diagonal colour bands, one colour per degree. PING publishes the chart as a graphic, so we approximate it with a plane: lie = −14.86 − 0.0186 × height + 0.2151 × wrist-to-floor (cm), rounded and capped at ±3° (Takomo's options). Checked against every reference point we could find, it matches nine of ten:

| Height | Wrist-to-floor | Expected | Plane | Source |
|---|---|---|---|---|
| 152 cm | 86 cm | +1 | +0.81 → +1 | Takomo pack |
| 173 cm | 86 cm | 0 | +0.42 → 0 | Takomo pack |
| 188 cm | 94 cm | +2 | +1.86 → +2 | Takomo pack |
| 196 cm | 100 cm | +3 | +3.01 → +3 | Takomo pack |
| 193 cm | 76 cm | −2 | −2.10 → −2 | Takomo pack |
| 5′8″ | 33″ | Black (0) | −0.05 → 0 | published PING chart example (Golf Hub) |
| 5′10″ | 34″ | Black (0) | +0.42 → 0 | published PING chart example (Caddie AI) |
| 5′11″ | 34.5″ | Blue (+1) | +0.63 → +1 | published PING chart example (Caddie AI) |
| 6′0″ | 35″ | Blue (+1) | +0.86 → +1 | published PING chart example (Caddie AI) |
| 5′6″ | 32″ | Red (−1) | −0.49 → 0 | published PING chart example (TGW) — **miss**, just inside the rounding line |

The approximation is good near the middle of the chart and less certain toward its corners and band edges; the one miss sits half a band from its target. Secondary sources reproduce the chart by eye, so treat single points with care. If Takomo shares their own table, it replaces the plane in one function (`lie_continuous`) and the data is rebuilt.

A machine-readable version of the chart as implemented: `takomo_fit/data/static_fit_chart.csv` (height × wrist-to-floor → length and lie).

## Grip and stock

- **Grip** from glove size: XS, S, M → standard; M/L, L → midsize; XL → oversize (pack, confirmed by Takomo). Takomo's real split is about 35 % standard, 52 % midsize, 13 % oversize; the simulated population is calibrated to it.
- PING's own grip chart uses two hand measurements instead of glove size: hand length (wrist crease to middle fingertip) and the length of the longest finger. Takomo does not use it today, but it may be more accurate. The data includes both measurements (`true_finger_length_cm`, `finger_length_photo_cm`), and `camera/measure.py hand --finger` measures the finger from the same photo. A team that proposes the two-measurement method needs to argue for it; the reference grip stays the glove rule.
- **Stock set**: steel shaft whose model is a stock shaft of the head, standard length and lie, and a standard **or midsize** grip (Takomo, 5 Nov: midsize is a stock option; only oversize forces a custom order).

## Smaller details

- Carry buckets share endpoints in the pack (120–135, 135–155 yd): a value on an endpoint goes to the lower bucket. Speed buckets are whole numbers (60–70, 71–80 …): a decimal between them goes up, so 70.4 mph is 71–80.
- The reference spec in the labels applies these same rules to each golfer's true values, with one change: lie is the golfer's delivered need (what a ball-line or lie-board test shows), rounded.
