/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * useBoot.js
 * The full-screen boot overlay, as a promise.
 *
 * The reference did this with a callback that the overlay awaited before
 * hiding itself - deliberately, so there was never a frame where the
 * overlay had gone but the screen underneath hadn't changed yet, which
 * let the previous screen flash back into view. That ordering is worth
 * keeping exactly; it just reads better as an awaited async function
 * than as a nested callback.
 */

import { reactive, readonly } from 'vue'

const state = reactive({
  visible: false,
  label: 'INITIALIZING QUIETUDE',
  subtext: '',
  shuttingDown: false,
})

export function useBoot() {
  /**
   * Shows the overlay for `duration`, then runs `task` to completion,
   * and only then hides it. If `task` is slower than `duration` the
   * overlay stays up for as long as it needs - the point is that the
   * overlay outlives the transition, never the other way round.
   */
  async function run(task, {
    label = 'INITIALIZING QUIETUDE',
    subtext = '',
    duration = 1600,
    shuttingDown = false,
  } = {}) {
    state.label = label
    state.subtext = subtext
    state.shuttingDown = shuttingDown
    state.visible = true

    await new Promise((r) => setTimeout(r, duration))
    try {
      if (task) await task()
    } finally {
      state.visible = false
      state.shuttingDown = false
    }
  }

  function update({ label, subtext }) {
    if (label !== undefined) state.label = label
    if (subtext !== undefined) state.subtext = subtext
  }

  const hide = () => { state.visible = false }

  return { boot: readonly(state), run, update, hide }
}
