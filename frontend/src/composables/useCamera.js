/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * useCamera.js
 * Webcam access and frame capture for the face flows.
 *
 * Capture stays in the browser, as it was: the camera sits behind the
 * browser's permission prompt, frames arrive as data URLs, and OpenCV
 * does detection and recognition server-side. Nothing in the backend
 * opens /dev/video0, which is one less device and one less permission
 * to arrange.
 */

import { onScopeDispose, readonly, ref } from 'vue'

// Enough resolution for an 80px-minimum face detection with headroom,
// without sending a megabyte per frame to a loopback endpoint.
const CAPTURE_WIDTH = 480
const CAPTURE_HEIGHT = 360

export function useCamera() {
  const stream = ref(null)
  const error = ref(null)
  const active = ref(false)

  let canvas = null

  async function start(videoEl) {
    error.value = null
    try {
      stream.value = await navigator.mediaDevices.getUserMedia({
        video: { width: CAPTURE_WIDTH, height: CAPTURE_HEIGHT },
        audio: false,
      })
    } catch {
      error.value =
        'Camera access is required. Please allow camera permissions and retry.'
      return false
    }

    if (videoEl) {
      videoEl.srcObject = stream.value
      // Chrome autoplays a muted video; Firefox sometimes needs asking.
      // Not swallowed. If this rejects the preview is frozen, and the
      // engine draws its own play button over it, which reads as the
      // camera being broken. Retried once after a beat - the common
      // cause is the element not being ready rather than a policy - and
      // reported if it still will not start, so the screen can say so
      // instead of showing a still frame.
      try {
        await videoEl.play()
      } catch (first) {
        await new Promise((r) => setTimeout(r, 150))
        try {
          await videoEl.play()
        } catch (second) {
          error.value =
            'The camera is connected but the preview will not start '
            + `(${second?.name || first?.name || 'unknown error'}).`
        }
      }
    }
    active.value = true
    return true
  }

  /** A JPEG data URL of the current frame, or null if there isn't one yet. */
  function captureFrame(videoEl) {
    if (!videoEl?.videoWidth) return null
    if (!canvas) canvas = document.createElement('canvas')
    canvas.width = videoEl.videoWidth
    canvas.height = videoEl.videoHeight
    canvas.getContext('2d').drawImage(videoEl, 0, 0)
    // JPEG at 0.8 rather than PNG: roughly a fifth the bytes, and the
    // pipeline immediately converts to grayscale and equalizes, so the
    // compression artifacts don't survive to affect recognition.
    return canvas.toDataURL('image/jpeg', 0.8)
  }

  function stop() {
    stream.value?.getTracks().forEach((t) => t.stop())
    stream.value = null
    active.value = false
  }

  onScopeDispose(stop)

  return { stream: readonly(stream), error, active: readonly(active), start, captureFrame, stop }
}
