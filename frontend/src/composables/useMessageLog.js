/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * useMessageLog.js
 * A standalone conversational log.
 *
 * The setup wizard and the password gate each hold a conversation that
 * isn't part of the chat history - you shouldn't find your password
 * prompt in the console log afterwards. Both need the same four
 * operations, so they share this rather than the chat store.
 */

import { ref } from 'vue'

export function useMessageLog() {
  const messages = ref([])
  let nextId = 1

  /** Returns the message so a caller can mutate .text live - the
   *  countdowns rely on that. */
  function say(text, status = null) {
    messages.value.push({ id: nextId++, role: 'quietude', text, status, at: new Date() })
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

  function echo(text, { masked = false } = {}) {
    messages.value.push({ id: nextId++, role: 'user', text, masked, at: new Date() })
    return messages.value[messages.value.length - 1]
  }

  const clear = () => { messages.value = [] }

  return { messages, say, echo, clear }
}

export const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
