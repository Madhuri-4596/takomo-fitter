# Data dictionary

All files are UTF-8 CSV with a header row. Missing values are empty. `golfer_id` joins every table. IDs are random (`G` + six digits); demo golfers are `DEMO-01` … `DEMO-16`.

**Units:** mph, yards, degrees, rpm, cm, mm, seconds.
**Directions:** in `shots.csv` lateral values are in the **target frame**, so positive means right of the target for everyone. A left-hander's slice therefore shows negative `side_yd`. Toe/heel strike location is relative to the club and is not mirrored.

## golfers.csv

| Column | Meaning |
|---|---|
| golfer_id | ID |
| split | train, dev, test or demo |
| age | years |
| sex | male / female |
| handedness | right / left |
| rounds_per_year | Under 10, 10-30, 31-50, Over 50 (questionnaire answer 3; unused by today's tool) |
| own_7i_loft_deg | loft of the golfer's current 7-iron, only when the golfer knows it (about a third) |
| persona, story | demo split only: name and a short case description |

## questionnaire.csv — what the golfer answered

The 12 questions of today's tool, as the golfer answered them. Errors are deliberate: see `SIMULATOR.md` § Self-reports.

| Column | Meaning |
|---|---|
| age, sex, handedness, rounds_per_year | as answered (assumed correct) |
| score_bucket, score_label | typical 18-hole score: 1 Under 71, 2 71-77, 3 78-84, 4 85-92, 5 Over 92 |
| speed_or_carry | which one the golfer answered: `speed` or `carry` |
| reported_7i_speed_mph | if speed was answered |
| reported_7i_carry_yd | if carry was answered (rounded to 5 yd, as people do) |
| swing_bucket | 1-5, derived from the speed or carry answer with Takomo's table |
| characteristics | top desired characteristics, `|`-separated: forgiveness, distance, forged feel, distance control, workability, less offset, more offset |
| flight | desired ball flight: low / mid / high |
| tempo | smooth / moderate / quick |
| height_unit, height_cm | unit the golfer used (cm or ft_in), value converted to cm |
| wrist_to_floor_unit, wrist_to_floor_cm | unit used (cm or in), value in cm, in golf shoes |
| glove | XS, S, M, M/L, L, XL |
| glove_was_guessed | the golfer ticked "not sure" and the form defaulted (M for men, S for women) |

## phone_measurements.csv — simulated output of at-home capture

What a reasonable phone pipeline might return. **These are simulated measurement outputs with assumed errors, not images.** Use them to design fusion and uncertainty; replace them with your own pipeline's output when you have one.

| Column | Meaning | Assumed error (1 SD) |
|---|---|---|
| body_photo | full-body photo with the sheet was taken | |
| body_photo_quality | good / poor | |
| height_photo_cm | height from the photo | 1.6 cm good, 3.5 cm poor |
| wrist_to_floor_photo_cm | wrist-to-floor from the same photo (missing if arms not visible) | 2.0 cm good, 3.5 cm poor |
| hand_photo | hand photo on the sheet was taken | |
| hand_length_photo_cm | wrist crease to middle fingertip | 0.35 cm |
| finger_length_photo_cm | middle finger, base crease to tip (PING's second grip measurement) | 0.30 cm |
| swing_video, swing_video_fps | face-on swing video and its frame rate (30 or 240) | |
| backswing_video_s, downswing_video_s | takeaway→top and top→impact times | ±1 frame plus 12 ms |
| clubhead_speed_video_mph | 7-iron speed estimate from 240 fps video only | 3.5 mph |

## home_tests.csv — practice-mat tests, one row per shot

Done with the golfer's **own** 7-iron, whose exact lie and length the golfer does not know. Columns: `golfer_id`, `test`, `shot_no`, `value`, `unit`.

| test | value |
|---|---|
| ball_line_tilt | tilt of the transferred line, degrees; + = top of the line leans toward the toe = club toe-up at impact = club too upright for this golfer. Also tilted by face angle at impact, so it exaggerates (see `PHONE_CAMERA_PLAYBOOK.md`) |
| impact_spray_offset | strike location on the face from a powder-spray photo, mm; + = toward the toe |

## shots.csv — launch-monitor shots

Two session types:

- `trackman_fitting`: full club and ball data. 10 shots with the golfer's own 7-iron, 8 with a Takomo **201 MKII** 7-iron (KBS Tour Regular, standard length, lie and grip) and 6 with a **101 MKII** (KBS Tour Lite Regular, standard). About 25 % of golfers.
- `home_radar`: a consumer radar unit; 10 shots with the own 7-iron; club speed, ball speed, launch, carry and total only, with larger errors. About 15 % of golfers.

| Column | Meaning |
|---|---|
| session, club | session type; `own_7i` or `takomo_<head>_<shaft>_std` |
| club_loft_deg | known loft (Takomo clubs always; own club only if the golfer knows it) |
| shot_no | 1… within the club |
| club_speed_mph | club head speed |
| attack_angle_deg | + = up, - = down |
| dynamic_loft_deg | loft at impact |
| club_path_deg, face_angle_deg, face_to_path_deg | target frame, + = right |
| ball_speed_mph, smash_factor | |
| launch_angle_deg, launch_direction_deg | vertical launch; horizontal start, + = right |
| spin_rate_rpm, spin_axis_deg | spin axis + = curving right |
| carry_yd, total_yd, side_yd | landing distance, distance after roll, lateral (+ right) |
| apex_yd, landing_angle_deg | peak height, descent angle |
| impact_offset_mm, impact_height_mm | strike location from the face centre; + = toe / high |
| impact_toe_up_deg | lie at impact: + = toe up (club too upright for this swing), - = toe down |

## labels.csv — truth and reference fitting (train, dev, demo)

**Truth**

| Column | Meaning |
|---|---|
| true_score | average 18-hole score |
| true_7i_speed_mph | average 7-iron club speed |
| true_height_cm, true_wrist_to_floor_cm, true_hand_length_cm | body, in golf shoes |
| true_finger_length_cm | middle finger, base crease to tip |
| true_glove | glove size from hand length: edges 16.8 / 17.8 / 18.59 / 19.4 / 20.22 cm for XS / S / M / M-L / L / XL. The M|M-L and L|XL edges reproduce Takomo's real grip mix (35 % standard, 52 % midsize, 13 % oversize) |
| true_tempo | from total swing time: quick < 1.05 s, smooth > 1.35 s, else moderate |
| true_backswing_s, true_downswing_s | |
| joint_pain | golfer has joint pain in the hands (not asked by today's tool) |
| true_own_7i_loft_deg, true_own_club_lie_adj_deg, true_own_club_length_adj_in | the golfer's current 7-iron |
| true_lie_need_deg | the lie (degrees from standard, + upright) this golfer's delivery needs; continuous |
| needed_flight, needed_flight_landing_angle_deg | trajectory class with a 31° reference 7-iron: high if landing angle < 35°, low if > 45° |
| own_true_mean_carry_yd, own_true_mean_total_yd | true average with the golfer's own 7-iron |

**Reference spec** (today's rules on true values; lie from delivery)

| Column | Meaning |
|---|---|
| ref_head, ref_head_score | best head and its score |
| ref_head_2, ref_head_2_score | runner-up |
| ref_shaft, ref_shaft_model, ref_flex, ref_material | shaft slug from Takomo's product file, model, flex, steel/graphite |
| ref_shaft_alt | the shaft the rules give on the other side of the nearest flex line (accepted by the scorer when `close_call_flex`) |
| ref_length_in | -1.5 … +1.5 in, 0.5 steps |
| ref_lie_deg | -3 … +3, 1° steps: `true_lie_need_deg` rounded |
| ref_static_lie_deg | what the static chart alone gives with the true body measurements |
| ref_grip | standard / midsize / oversize |
| ref_purchase | stock / custom (stock = stock steel shaft, standard length and lie, standard or midsize grip). Takomo's `takomo_products.json` still says "standard grip"; Takomo corrected this on 5 November: midsize is stock too |
| ref_handedness | right / left |
| ref_swing_bucket, ref_score_bucket | the true buckets |
| close_call_head | top two heads tie |
| close_call_flex | true speed within 1 mph of 60/70/80/90 |
| close_call_length | height within 0.5 cm of a length line |
| close_call_lie | lie need within 0.1° of a rounding line |
| close_call_grip | hand length within 0.1 cm of a grip line |

**Expected outcome per Takomo head** (for every head, with the reference shaft for that head and the reference length, lie and grip; average swing, typical mishits included)

| Column | Meaning |
|---|---|
| exp_carry_yd_<head> | expected carry |
| exp_side_mean_yd_<head> | where the average shot finishes, target frame (+ right) |
| exp_side_sd_yd_<head> | left-right spread, 1 SD |
| exp_apex_yd_<head> | peak height |

Heads: `101-mkii`, `201-mkii`, `201t-mkii`, `301-cb`, `301-mb`.

## Prediction file for the scorer

```
golfer_id,head,shaft,length_in,lie_deg,grip,purchase,handedness
G123456,201-mkii,kbs-tour-regular,0.5,1,midsize,custom,right
```

`shaft` is a slug from `takomo_official/takomo_products.json` and must be offered on the predicted head; length and lie must be among the product file's options. Case and stray units (`0.5"`, `Right`) are tolerated. Optional extra columns are reported as mean absolute error: `est_7i_speed_mph`, `est_height_cm`, `est_wrist_to_floor_cm`, `est_hand_length_cm`, `est_lie_need_deg`, `est_carry_yd_<head>`.
