/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * session.js
 * Who's logged in, where we are in the boot flow, and whether the two
 * engines are up.
 *
 * On Pinia earning its keep: this is the state that genuinely outlives
 * any one component. The user survives navigating to /commands and
 * back; speech readiness is polled in one place and read in four; the
 * boot overlay is driven from whichever screen is handing over. Without
 * a store that all becomes module-level refs in a composable, which is
 * the same thing with less tooling. Only two stores exist, though -
 * everything else is a composable, because everything else is behaviour
 * rather than shared state.
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'

// Generous, for slow hardware loading two models. Past this we stop
// blocking and let the user in without voice rather than leaving them
// staring at a progress line forever.
export const SPEECH_GATE_TIMEOUT_MS = 180_000

export const useSessionStore = defineStore('session', () => {
  const user = ref(null)

  // Whether this session has actually passed a login gate.
  //
  // Deliberately separate from `user`, and the router guard checks this
  // one. They look interchangeable but aren't: GET /api/user returns the
  // stored profile to anyone who asks - it has to, because the login
  // screen needs a name to greet - so "we know who the user is" is not
  // the same claim as "the user proved they're that person". Conflating
  // the two means merely loading the login screen authenticates you, and
  // the password gate (the one path with the bounded-attempt
  // brute-force guard on it) can be walked straight past.
  const authenticated = ref(false)

  const faceRegistered = ref(false)
  const agreementAccepted = ref(false)
  const hasUsers = ref(false)

  const speech = ref({ ready: false, detail: 'Starting speech engine...', available: false })
  const tts = ref({ ready: false, engine: null, available: false, error: null })
  const geminiInstalled = ref(false)

  // Set once the speech gate has been passed or given up on, so a
  // refresh mid-session doesn't make the user sit through it again.
  const speechGateResolved = ref(false)
  const speechGateTimedOut = ref(false)

  const conversationMode = ref(false)
  const conversationTurns = ref(null)

  const bootError = ref(null)

  const voiceAvailable = computed(() => speech.value.ready && !speechGateTimedOut.value)
  const displayName = computed(() => user.value?.name || 'there')

  async function refreshStatus() {
    const data = await api.status()
    hasUsers.value = data.has_users
    agreementAccepted.value = data.agreement_accepted
    faceRegistered.value = data.face_registered
    speech.value = data.speech
    tts.value = data.tts
    geminiInstalled.value = data.gemini_installed
    return data
  }

  async function refreshUser() {
    const data = await api.user()
    if (data?.ok) {
      user.value = data.user
      faceRegistered.value = data.user.face_registered ?? faceRegistered.value
    }
    return user.value
  }

  /** Record who the user is, without claiming they've logged in. */
  function setUser(next) {
    user.value = next ? { ...next } : null
  }

  /**
   * Call this and only this on a successful login - face unlock,
   * password, or finishing first-run setup. It's the one thing that
   * opens the authenticated routes.
   */
  function authenticate(next) {
    if (next) user.value = { ...next }
    authenticated.value = true
  }

  function markFaceRegistered() {
    faceRegistered.value = true
    if (user.value) user.value.face_registered = true
  }

  async function refreshConversationMode() {
    try {
      const data = await api.conversationModeStatus()
      conversationMode.value = !!data.conversation_mode
      conversationTurns.value = data.conversation_turns
    } catch {
      // Not critical - STATIC is both the default and the safe
      // assumption, since it's the fully-local one.
      conversationMode.value = false
    }
  }

  function setConversationMode(on, turns = null) {
    conversationMode.value = on
    conversationTurns.value = on ? turns : null
  }

  function reset() {
    user.value = null
    authenticated.value = false
    faceRegistered.value = false
    conversationMode.value = false
    conversationTurns.value = null
    speechGateResolved.value = false
    speechGateTimedOut.value = false
  }

  return {
    user, authenticated, faceRegistered, agreementAccepted, hasUsers,
    speech, tts, geminiInstalled,
    speechGateResolved, speechGateTimedOut,
    conversationMode, conversationTurns, bootError,
    voiceAvailable, displayName,
    refreshStatus, refreshUser, setUser, authenticate, markFaceRegistered,
    refreshConversationMode, setConversationMode, reset,
  }
})
