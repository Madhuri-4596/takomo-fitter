# Phone camera playbook

The brief: the golfer should need only what they have at home, a phone, a laptop and maybe a friend. A phone camera can replace most of today's questions with measurements. This page lists what is realistic in 48 hours, ranked by how much it moves the fitting.

Error figures below are the **assumptions used in the synthetic data**, chosen as plausible targets. They are not measured results. Measure your own pipeline's error against a tape measure and the TrackMan at the event.

## Ranked by value for the fitting

| Rank | Measure | Replaces | Method | Effort |
|---|---|---|---|---|
| 1 | **Lie at impact** | nothing today (static chart only) | Ball-line test, photo of the face | Low |
| 2 | **7-iron speed** | speed-or-carry answer | Slow-motion video, or a launch monitor if the golfer owns one | High (video) / low (device) |
| 3 | **Wrist-to-floor and height** | self-measured answers | Full-body photo against a wall with the sheet | Medium |
| 4 | **Hand length** | glove size | Photo of the hand on the sheet | Low |
| 5 | **Tempo** | "smooth / moderate / quick" | Any swing video plus pose landmarks | Low–medium |
| 6 | **Strike pattern** | nothing today | Foot-powder spray or impact tape, photo | Low |

Why this order: lie is where today's tool is furthest from the reference (41 % agreement), the ball-line test is cheap, and only shot evidence can find a golfer like DEMO-09 who pulls everything despite a "standard" chart result. Speed decides flex and material, the most heavily weighted parts of the spec.

## 1. Ball-line lie test

**Golfer does:** draws a straight line on a ball with a whiteboard marker, sets it down with the line vertical and facing the club, hits five 7-irons off a hard mat (a range mat or a thin plastic board), photographs the face square-on after each shot.

**Reading:** if the transferred line leans toward the toe, the club was toe-up at impact (too upright): go flatter. Toward the heel: go more upright. A fitter treats about 1° of tilt as about 1° of lie, but face angle at impact also rotates the mark, so the line exaggerates and varies shot to shot. Average several shots.

**Code:** `camera/ball_line.py` gives the tilt from four clicked points (two on the line, two on the leading edge). Automatic line detection (Canny + Hough on the face crop) is a good 48-hour target.

**Catch:** the golfer's own club has an unknown lie, so the test measures "need relative to my current club". Asking for the club's brand and model (or a photo of it on a flat floor) closes that gap.

## 2. Swing speed

Few golfers know it, and carry answers are biased (median +15 yd in the simulation, plus loft differences between brands).

- **Launch monitor the golfer owns** (consumer radar units are common): easiest, best. Accept its CSV export.
- **240 fps slow-motion video**: club head displacement between frames near impact. At 75 mph the head moves about 14 cm per frame at 240 fps, so motion blur and frame timing dominate the error. Calibrate scale from a known length (club length, or the sheet on the ground). The synthetic data assumes ±3.5 mph; check what you actually achieve against the TrackMan.
- **Audio**: the impact sound is a precise time marker for syncing and for impact-frame detection; it does not give speed by itself.
- **Indirect**: carry plus loft plus launch evidence (ball flight from video) into a physics-based estimate. `takomo_fit.physics` lets you invert carry for a given loft and delivery.

## 3. Height and wrist-to-floor

Tape the sheet flat on a wall at chest height. The golfer stands with their back to the same wall, in golf shoes, arms hanging. A friend takes the photo straight on from 3–4 m with the phone at waist height.

`camera/measure.py body` converts three image points (top of head, wrist crease, floor line) to centimetres in the wall plane through the sheet's four markers. Points can be clicked, or taken from a pose model: MediaPipe Pose gives wrist and heel landmarks; the top of the head is better found from the segmentation mask than from facial landmarks.

**Catches:** standing away from the wall breaks the single-plane assumption; a wide-angle lens close up distorts; the pose wrist landmark is the joint centre, not the crease. Measure a few people with a tape at the event and fit a correction.

## 4. Hand length → grip size

Hand flat on the printed sheet, fingers together, photo straight down. `camera/measure.py hand` gives wrist crease to middle fingertip in cm. MediaPipe Hand Landmarker returns 21 landmarks per hand (wrist and middle fingertip are two of them); again, the wrist landmark is not exactly the crease.

Takomo's tool picks the grip from glove size (XS–M standard, M/L–L midsize, XL oversize). PING's grip chart, which Takomo pointed to as possibly more accurate, uses two numbers from the same photo: overall hand length and the length of the longest finger (base crease to tip). `camera/measure.py hand --finger …` returns both. Grip makers' single-number charts also exist (Golf Pride: 6.6–7.5 in standard, 7.6–9 in midsize, above 9 in jumbo). Where glove size and hand measurements disagree, show the golfer the disagreement rather than hiding it. Midsize is a stock option at Takomo, so only an oversize grip forces a custom order.

## 5. Tempo

Any face-on video works; 30 fps is enough for a first answer. Track the lead wrist with a pose model, then `camera/tempo.py` finds takeaway, top and impact and returns backswing time, downswing time and their ratio. At 30 fps one frame is 33 ms, a large share of a 250 ms downswing; at 240 fps it is 4 ms. GolfDB (1,400 labelled swing videos with eight swing events) is useful for testing event detection.

## 6. Strike pattern

Foot-powder spray or impact tape on the face, five shots, one photo. Toe strikes suggest the club is too short or the golfer stands too far away; heel strikes, too long. Also tells you how much forgiveness the golfer needs: wide scatter favours the 101 MKII and 201 MKII.

## The printed sheet

`camera/measurement_sheet_A4.pdf`: four ArUco markers (OpenCV dictionary 4X4_50, 40 mm, ids 0–3) and a 100 mm check bar. `camera/sheet.py` finds the markers and computes the image-to-millimetre homography, so the photo does not need to be square-on. Tested: on synthetic perspective photos a 190 mm distance is recovered within 1.5 mm; on the rendered PDF the 100 mm bar measures 99.97 mm.

Print at 100 %. If the bar is not 100 mm, every measurement is off by the same factor.

## What has and has not been tested here

| Part | Status |
|---|---|
| Sheet detection, homography, distances | tested on synthetic warped images and on the rendered PDF |
| Body and hand geometry from given points | tested |
| Tempo event detection | tested on synthetic wrist tracks |
| Ball-line tilt from points | tested |
| Pose and hand landmark extraction on real photos | **not tested here** (no real photos in the build). The code takes points from any source; plug in MediaPipe or another pose model and validate at the event |

## Privacy

Photos and videos of people are personal data. Keep processing on the device where you can, ask for consent before anything is uploaded, and say what is stored. A fitting tool that keeps swing videos needs a reason and a retention period.
