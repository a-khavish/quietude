/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * identity.js
 * Who the assistant is, everywhere at once.
 *
 * Quietude is the application. The assistant inside it is named by the
 * user, and that name appears in a lot of places: the label on every
 * reply, the window title, the wake phrase in the hints, the voice
 * tooltip, the greeting. A third store earns its keep here because
 * those places are scattered across components that have no other
 * reason to know about each other.
 *
 * Two of those places are not Vue bindings and need a hand:
 *
 *   the bubble label   is a CSS ::before content string, because the
 *                      label is decoration rather than content. It is
 *                      driven by a custom property, so renaming her
 *                      relabels every message already on screen without
 *                      re-rendering any of them.
 *   the window title   is document.title, which is also what a dock
 *                      shows under the window.
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'

const APP_NAME = 'Quietude'

const DEFAULT_PRONOUNS = {
  they: 'they', them: 'them', their: 'their',
  theirs: 'theirs', themself: 'themselves', are: 'are',
}

export const useIdentityStore = defineStore('identity', () => {
  const identity = ref({ name: '', pronouns: 'they', age: null, nationality: '' })
  const pronouns = ref({ ...DEFAULT_PRONOUNS })
  const wakePhrase = ref('')
  const wakePrefixes = ref(['hello'])
  /** null, or { known: true|false|null, detail } from the wake-word model. */
  const wakeCheck = ref(null)
  const loaded = ref(false)

  const name = computed(() => identity.value.name || '')
  const named = computed(() => !!name.value)
  /** Never invents a name: unnamed, she is described, not called something. */
  const displayName = computed(() => name.value || 'your assistant')
  /** The label on each reply. Unnamed, she is described, not called
   *  something we picked. */
  const label = computed(() => (name.value || 'assistant').toUpperCase())

  /**
   * What the interface calls her in headings, the top bar and the boot
   * and shutdown screens.
   *
   * Falls back to the application's name only while she has none of her
   * own - which is the first-run screens, before setup has asked. Once
   * she is named, the name she was given is what the interface uses:
   * Quietude is the program, and the program is not who you are
   * talking to.
   */
  const brand = computed(() => (name.value || APP_NAME).toUpperCase())

  /**
   * Fills in the same placeholders the backend's _text does, so a
   * string written on either side reads the same way.
   */
  function say(template) {
    const p = pronouns.value
    return String(template)
      .replaceAll('{name}', displayName.value)
      .replaceAll('{wake}', wakePhrase.value || 'the wake phrase')
      .replaceAll('{they_are}', `${p.they} ${p.are || 'are'}`)
      .replaceAll('{they}', p.they)
      .replaceAll('{them}', p.them)
      .replaceAll('{their}', p.their)
      .replaceAll('{theirs}', p.theirs)
      .replaceAll('{themself}', p.themself)
  }

  function applyToDocument() {
    if (typeof document === 'undefined') return
    // Quoted, because a CSS content value is a string. The backend
    // restricts the name to letters, hyphens and apostrophes, so there
    // is nothing here that needs escaping - but the quotes are stripped
    // anyway rather than trusting that from the browser's side.
    const safe = label.value.replace(/["\\]/g, '')
    document.documentElement.style.setProperty('--assistant-label', `"${safe}"`)
    document.title = named.value ? `${name.value} · ${APP_NAME}` : APP_NAME
  }

  function adopt(payload) {
    if (!payload) return
    if (payload.identity) identity.value = { ...identity.value, ...payload.identity }
    if (payload.pronouns) pronouns.value = { ...DEFAULT_PRONOUNS, ...payload.pronouns }
    if (payload.wake_phrase !== undefined) wakePhrase.value = payload.wake_phrase
    if (payload.wake_prefixes) wakePrefixes.value = payload.wake_prefixes
    if (payload.wake_check !== undefined && payload.wake_check !== null) {
      wakeCheck.value = payload.wake_check
    }
    loaded.value = true
    applyToDocument()
  }

  async function load() {
    try {
      adopt(await api.identity())
    } catch {
      // An assistant with no name still works; everything that shows
      // the name has a fallback. Not worth an error on screen.
      loaded.value = true
      applyToDocument()
    }
    return identity.value
  }

  /**
   * Saves one or more fields and applies them immediately.
   *
   * There is no save button anywhere that calls this: the settings page
   * sends each field as it is edited, which is why the backend accepts
   * partial updates.
   */
  async function save(fields) {
    const payload = await api.saveIdentity(fields)
    adopt(payload)
    return payload
  }

  return {
    identity, pronouns, wakePhrase, wakePrefixes, wakeCheck, loaded,
    name, named, displayName, label, brand,
    appName: APP_NAME,
    say, load, save, adopt, applyToDocument,
  }
})
