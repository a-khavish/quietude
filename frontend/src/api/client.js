/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * client.js
 * One place that knows how to talk to Quietude's backend.
 *
 * The Windows build had ~40 bare fetch() calls scattered through
 * main.js, each with its own ad-hoc error handling - or none. Collecting
 * them here means "the backend went away" is handled once, and every
 * caller gets the same shape back.
 */

class ApiError extends Error {
  constructor(message, { status = 0, payload = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
    // A dropped connection reads very differently to a 400, and the UI
    // says different things about each: one is "is the server running",
    // the other is "that input was wrong".
    this.offline = status === 0
  }
}

async function request(path, { method = 'GET', body, raw, signal, headers = {} } = {}) {
  const init = { method, signal, headers: { ...headers } }

  if (raw !== undefined) {
    init.body = raw
    init.headers['Content-Type'] = 'application/octet-stream'
  } else if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body // let the browser set the multipart boundary
    } else {
      init.body = JSON.stringify(body)
      init.headers['Content-Type'] = 'application/json'
    }
  }

  let res
  try {
    res = await fetch(path, init)
  } catch (err) {
    if (err?.name === 'AbortError') throw err
    throw new ApiError("I lost connection to my backend. Is the server still running?")
  }

  const isJson = (res.headers.get('content-type') || '').includes('application/json')
  const payload = isJson ? await res.json().catch(() => null) : null

  // A non-2xx with a JSON body is still the backend answering, and its
  // own error text is better than anything invented here - validation
  // failures, 503 "not ready", and the bounded-attempt password gate all
  // rely on reaching the caller intact.
  if (!res.ok) {
    throw new ApiError(payload?.error || `Request failed (${res.status})`, {
      status: res.status,
      payload,
    })
  }
  return payload
}

const get = (path, opts) => request(path, { ...opts, method: 'GET' })
const post = (path, body, opts) => request(path, { ...opts, method: 'POST', body })
const del = (path, opts) => request(path, { ...opts, method: 'DELETE' })

export const api = {
  ApiError,

  // ---- system ----
  status: () => get('/api/status'),
  health: () => get('/api/health'),
  acceptAgreement: () => post('/api/agreement/accept'),
  user: () => get('/api/user'),
  commands: () => get('/api/commands'),
  conversationModeStatus: () => get('/api/conversation_mode_status'),
  postLoginCheck: () => get('/api/login/post_login_check'),
  shutdown: () => post('/api/shutdown'),
  resetExecute: () => post('/api/reset/execute'),

  // ---- setup wizard ----
  setupState: () => get('/api/setup/state'),
  setupAnswer: (message) => post('/api/setup/answer', { message }),

  // ---- password login ----
  passwordLoginStart: () => post('/api/login/password/start'),
  passwordLoginAnswer: (message) => post('/api/login/password/answer', { message }),

  // ---- chat ----
  chat: (message) => post('/api/chat', { message }),
  upload: (files) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    return post('/api/upload', form)
  },

  // ---- face ----
  faceStatus: () => get('/api/face/status'),
  faceResetSamples: () => post('/api/face/reset_samples'),
  faceCapture: (image) => post('/api/face/capture', { image }),
  faceTrain: () => post('/api/face/train'),
  faceVerify: (image) => post('/api/face/verify', { image }),

  // ---- speech (local, offline) ----
  speechStatus: (signal) => get('/api/speech/status', { signal }),
  speechAudio: (pcmBuffer) => request('/api/speech/audio', { method: 'POST', raw: pcmBuffer }),
  speechControl: (action, wakeSeq) => post('/api/speech/control', { action, wake_seq: wakeSeq }),

  // ---- tts ----
  ttsStatus: () => get('/api/tts/status'),
  // `overrides` carries rate/volume/voice_id for the settings page's
  // preview, which plays the controls as they currently read rather
  // than as they were last saved. Omitted everywhere else, where the
  // saved settings are exactly what should be heard.
  ttsSpeak: (text, overrides) => post('/api/tts/speak', { text, ...overrides }),
  ttsSettings: () => get('/api/tts/settings'),
  // Warms a voice without speaking, so the first phrase in a voice you
  // just selected isn't the one that waits for the model to load.
  preloadVoice: (voiceId) => post('/api/voices/preload', { voice_id: voiceId }),
  saveTtsSettings: (settings) => post('/api/tts/settings', settings),

  // ---- voice library ----
  voices: (params = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null),
    ).toString()
    return get(`/api/voices${query ? `?${query}` : ''}`)
  },
  installVoice: (key) => post('/api/voices/install', { key }),
  voiceJob: (id) => get(`/api/voices/jobs/${id}`),
  cancelVoiceJob: (id) => post(`/api/voices/jobs/${id}/cancel`, {}),
  removeVoice: (key) => del(`/api/voices/${encodeURIComponent(key)}`),

  // ---- who the assistant is ----
  identity: () => get('/api/identity'),
  saveIdentity: (identity) => post('/api/identity', identity),
  // Asks whether the wake-word model actually knows a name, before the
  // user commits to one. See speech_engine.wake_name_is_recognisable.
  checkWakeName: (name) => get(`/api/identity/wake-check?name=${encodeURIComponent(name)}`),

  // ---- speech models ----
  speechModels: (params = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null),
    ).toString()
    return get(`/api/models${query ? `?${query}` : ''}`)
  },
  installModel: (key) => post('/api/models/install', { key }),
  modelJob: (id) => get(`/api/models/jobs/${id}`),
  cancelModelJob: (id) => post(`/api/models/jobs/${id}/cancel`, {}),
  removeModel: (key) => del(`/api/models/${encodeURIComponent(key)}`),
  selectModel: (role, key) => post('/api/models/select', { role, key }),
  speechSensitivity: (level) => post('/api/speech/sensitivity', { level }),
}

export { ApiError }
