# Public resources worth knowing

Checked on 3 November 2026. Licences decide what you may do: a non-commercial dataset is fine for a hackathon prototype but cannot go into Takomo's product as is. When in doubt, say in your write-up which resources you used and under what licence.

## Golf swing video and pose

| Resource | What it is | Licence / terms | Use it for |
|---|---|---|---|
| **GolfDB** (McNally et al., CVPR Workshops 2019) · github.com/wmcnally/golfdb | 1,400 golf swing videos labelled with eight swing events, bounding box, club type, view (face-on, down-the-line), player sex; SwingNet baseline detects the events at 76.1 % | Repository under CC BY-NC 4.0 (non-commercial). Videos are YouTube links | Testing swing-event and tempo detection; pre-training an event detector |
| **MediaPipe Pose Landmarker** (Google) · developers.google.com/mediapipe/solutions/vision/pose_landmarker | 33 body landmarks per person, in image and 3D world coordinates; lite, full and heavy models; runs on phones and in the browser | Apache 2.0 | Wrist, shoulder, hip and heel tracking for tempo, posture and body measurements |
| **MediaPipe Hand Landmarker** (Google) | 21 hand landmarks including wrist and fingertips; image and world coordinates | Apache 2.0 | Hand length for grip size |
| **OpenCap** (Stanford) · simtk.org/projects/opencap, github.com/stanfordnmbl/opencap-core | 3D kinematics and dynamics from two or more synchronised iPhones; **OpenCap Monocular** (2026 preprint) from a single phone video | Open source, free cloud processing for research; a patent application exists | Research-grade 3D swing analysis; a reference for what a single phone can do |
| **OpenCV ArUco** | Square fiducial markers; detection and homography in a few lines | Apache 2.0 | Scale and perspective from a printed sheet (used in `camera/`) |

## Body measurements

| Resource | What it is | Licence / terms | Use it for |
|---|---|---|---|
| **ANSUR II** (US Army anthropometric survey, 2012; public since 2017) · openlab.psu.edu/ansur2 | 93 directly measured dimensions for 4,082 men and 1,986 women, including stature, wrist height, hand length, hand breadth; millimetres | Cleared for unlimited public release | Realistic priors: wrist height given height, hand length given height; checking your photo estimates. Military sample, not the general population; keep men and women separate |
| **Drillis & Contini proportions** · openlab.psu.edu (proportionality constants) | Average segment lengths as a share of height | Published research | Quick priors; individual variation is large |

## Golf numbers

| Source | What it gives |
|---|---|
| **PING colour code chart** · ping.com/en-us/fitting | The industry-standard static chart (Takomo uses a version of it): height and wrist-to-floor give a lie colour (one colour per degree, Black = standard) and a length. The chart is a graphic; this pack approximates it (see `BASELINE_RULES_INTERPRETATION.md`) |
| **PING grip chart** | Grip colour from two hand measurements: hand length (wrist crease to tip of longest finger) and longest-finger length. White is men's standard |
| **TrackMan tour averages** (PGA and LPGA Tour, per club) | Club speed, ball speed, launch, spin, carry, apex, landing angle. Used to calibrate this pack's ball-flight model |
| **Takomo's own articles** · takomogolf.com/blogs/articles | 7-iron distance by handicap (Shot Scope data) and iron distance charts. Good for sanity checks on any distance you show a golfer |
| **Tour Tempo** (John Novosel) | Tour players' backswing:downswing ratio clusters near 3:1; most amateurs are slower relative to their downswing |
| **Grip-size charts** (for example Golf Pride: hand length from wrist crease to middle fingertip) | 6.6–7.5 in standard, 7.6–9 in midsize, above 9 in jumbo |
| **Ball-line lie test** (described by fitters, for example in GolfWRX and Golf.com articles) | The standard at-home dynamic lie check; marks exaggerate the error because face angle also rotates the line |

## Prior art to know about

Consumer launch monitors already combine phones and cameras: for example Rapsodo's MLM2PRO pairs Doppler radar with a 240 fps impact camera and phone video. Judges may ask how your idea differs from buying one. A good answer: most of Takomo's customers will not own one, and fitting needs body, hand and lie evidence that a launch monitor does not collect.
