/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * chat.js
 * The chat log, as data.
 *
 * The Windows build built each bubble with document.createElement and
 * appended it to a div - which meant the log only existed in the DOM,
 * so navigating away destroyed it, and every feature touching a message
 * (copy, speak, expand) had to attach its own listener at creation time.
 *
 * Here a message is a plain object and ChatBubble.vue renders it. The
 * log survives a trip to /commands and back, and "collapse long
 * messages" or "read this aloud" are properties of the component rather
 * than wiring done per element.
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

let nextId = 1

export const useChatStore = defineStore('chat', () => {
  const messages = ref([])

  /**
   * status: null | 'success' | 'warning' | 'error' - drives the bubble's
   * outline colour, same vocabulary the backend already returns.
   */
  function add({ role, text, status = null, table = null, files = null, masked = false }) {
    messages.value.push({
      id: nextId++,
      role,
      text: text ?? '',
      status,
      table,
      files,
      masked,
      at: new Date(),
    })
    // Returns the object *as stored in the reactive array*, not the
    // local one. Pushing a plain object into a ref([]) array stores the
    // raw object; reading it back through the proxy is what makes
    // mutations reactive. Handing back the local reference instead means
    // `message.text = ...` writes straight to the raw object, bypassing
    // the proxy's set trap, so Vue never learns anything changed - which
    // is exactly why the live countdowns rendered their first value and
    // then sat frozen until the next unrelated render.
    return messages.value[messages.value.length - 1]
  }

  const addUser = (text, { masked = false } = {}) => add({ role: 'user', text, masked })
  const addQuietude = (text, status = null) => add({ role: 'quietude', text, status })
  const addTable = (table) => add({ role: 'quietude', text: '', table })
  const addFiles = (files) => add({ role: 'user', text: '', files })

  /** Live edits to an existing bubble - used by the countdowns. */
  function updateText(message, text) {
    if (message) message.text = text
  }

  function clear() {
    messages.value = []
  }

  return { messages, add, addUser, addQuietude, addTable, addFiles, updateText, clear }
})
