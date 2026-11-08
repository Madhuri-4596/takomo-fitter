"use strict";

// MediaPipe Pose Landmarker, on-device, in VIDEO mode (https://developers.google.com/edge/mediapipe/
// solutions/vision/pose_landmarker/web_js). The library, WASM and model are served by this app,
// pinned (@mediapipe/tasks-vision 1.1.1, pose_landmarker_lite), so no API key or CDN is needed.
// Landmarks are only used to SUGGEST points and check framing; centimetres always come from the
// printed sheet, never from MediaPipe's world coordinates.

window.Pose = (() => {
  const LIB = "/vendor/mediapipe/vision_bundle.mjs";
  const WASM = "/vendor/mediapipe/wasm";
  const MODEL = "/models/pose_landmarker_lite.task";
  let landmarker = null, loading = null, lastTs = 0, state = "idle", error = null, delegate = null;

  async function load() {
    if (landmarker) return landmarker;
    if (!loading) {
      state = "loading";
      loading = (async () => {
        const mp = await import(LIB);
        const files = await mp.FilesetResolver.forVisionTasks(WASM);
        const options = (d) => ({
          baseOptions: { modelAssetPath: MODEL, delegate: d }, runningMode: "VIDEO", numPoses: 1,
          minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.5, minTrackingConfidence: 0.5,
        });
        try { landmarker = await mp.PoseLandmarker.createFromOptions(files, options("GPU")); delegate = "GPU"; }
        catch { landmarker = await mp.PoseLandmarker.createFromOptions(files, options("CPU")); delegate = "CPU"; }
        state = "ready";
        return landmarker;
      })().catch((e) => { state = "failed"; error = e; loading = null; throw e; });
    }
    return loading;
  }

  // One frame (video, canvas or image). Returns 33 landmarks {x, y, v} normalised to the
  // frame, or null when nobody is found. Timestamps must increase, so keep them monotonic.
  function detect(source) {
    if (!landmarker) return null;
    let ts = performance.now();
    if (ts <= lastTs) ts = lastTs + 1;
    lastTs = ts;
    const r = landmarker.detectForVideo(source, ts);
    const L = r.landmarks && r.landmarks[0];
    return L ? L.map((p) => ({ x: p.x, y: p.y, v: p.visibility ?? 0 })) : null;
  }

  // IMAGE mode: every frame judged on its own, with no temporal smoothing. Used for recorded swing
  // videos, where VIDEO-mode smoothing would delay the start of a slow takeaway.
  let imageLandmarker = null, imageLoading = null;
  async function loadImageMode() {
    if (imageLandmarker) return imageLandmarker;
    if (!imageLoading) {
      imageLoading = (async () => {
        const mp = await import(LIB);
        const files = await mp.FilesetResolver.forVisionTasks(WASM);
        const options = (d) => ({ baseOptions: { modelAssetPath: MODEL, delegate: d }, runningMode: "IMAGE", numPoses: 1 });
        try { imageLandmarker = await mp.PoseLandmarker.createFromOptions(files, options("GPU")); }
        catch { imageLandmarker = await mp.PoseLandmarker.createFromOptions(files, options("CPU")); }
        return imageLandmarker;
      })().catch((e) => { imageLoading = null; throw e; });
    }
    return imageLoading;
  }
  function detectImage(source) {
    if (!imageLandmarker) return null;
    const L = imageLandmarker.detect(source).landmarks?.[0];
    return L ? L.map((p) => ({ x: p.x, y: p.y, v: p.visibility ?? 0 })) : null;
  }

  return { load, detect, loadImageMode, detectImage, state: () => state, error: () => error, delegate: () => delegate };
})();
