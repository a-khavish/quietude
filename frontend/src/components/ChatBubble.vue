<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ChatBubble.vue
  One message.

  The reference built these imperatively: createElement for the bubble,
  the text span, the timestamp, then measured scrollHeight to decide
  whether to add an expand button, then attached copy and speaker
  listeners per element - about 90 lines of DOM assembly per message,
  and the only place a message existed was the DOM.

  Here the message is data and this renders it. Collapsibility is still
  measured (there's no way to know whether text overflows without
  measuring) but it's one ref and a computed instead of branching DOM
  construction.
-->
<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import InfoTable from '@/components/InfoTable.vue'
import FileChips from '@/components/FileChips.vue'
import IconButton from '@/components/IconButton.vue'

const props = defineProps({
  message: { type: Object, required: true },
  /** Only the main console offers copy and read-aloud, not the setup or
   *  login logs - which reuse this component for their own bubbles. */
  showActions: { type: Boolean, default: false },
  speaking: { type: Boolean, default: false },
})

const emit = defineEmits(['speak', 'stop-speaking'])

const COLLAPSE_HEIGHT_PX = 120

const textEl = ref(null)
const collapsible = ref(false)
const expanded = ref(false)
const copied = ref(false)

const displayText = computed(() =>
  props.message.masked ? '•'.repeat(props.message.text.length) : props.message.text,
)

const timestamp = computed(() =>
  props.message.at.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }),
)

const hasBody = computed(() => !!props.message.text)

onMounted(async () => {
  await nextTick()
  if (textEl.value) {
    collapsible.value = textEl.value.scrollHeight > COLLAPSE_HEIGHT_PX + 8
  }
})

function toggle() {
  expanded.value = !expanded.value
}

async function copy() {
  try {
    await navigator.clipboard.writeText(props.message.text)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1200)
  } catch {
    // Clipboard unavailable (no permission, or an insecure context) -
    // nothing useful to say about it, so say nothing.
  }
}
</script>

<template>
  <!-- A table-only or files-only message is its own shape, not a text
       bubble with extras bolted on. -->
  <InfoTable v-if="message.table" :rows="message.table" />
  <FileChips v-else-if="message.files" :files="message.files" />

  <div
    v-else
    class="bubble"
    :class="[
      message.role,
      message.status,
      { collapsible, expanded, 'has-copy-btn': showActions, 'has-speak-btn': showActions },
    ]"
  >
    <span ref="textEl" class="bubble-text-content">{{ displayText }}</span>
    <span class="bubble-timestamp">{{ timestamp }}</span>

    <button
      v-if="collapsible"
      type="button"
      class="bubble-expand-btn"
      :title="expanded ? 'Show less' : 'Show more'"
      @click.stop="toggle"
    >
      <svg viewBox="0 0 24 24" fill="none">
        <path
          :d="expanded ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'"
          stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"
        />
      </svg>
    </button>

    <template v-if="showActions && message.role === 'quietude' && hasBody">
      <IconButton
        class="speak-btn"
        :class="{ speaking }"
        :title="speaking ? 'Stop' : 'Hear this response'"
        @click.stop="speaking ? emit('stop-speaking') : emit('speak', message.text)"
      >
        <svg v-if="speaking" viewBox="0 0 24 24" fill="none">
          <rect x="6" y="6" width="12" height="12" rx="1.5"
                stroke="currentColor" stroke-width="2" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="none">
          <path d="M11 5 6 9H3v6h3l5 4V5Z" stroke="currentColor"
                stroke-width="1.6" stroke-linejoin="round" />
          <path d="M15.5 9a4 4 0 0 1 0 6" stroke="currentColor"
                stroke-width="1.6" stroke-linecap="round" />
        </svg>
      </IconButton>

      <IconButton
        class="copy-btn"
        :class="{ copied }"
        :title="copied ? 'Copied!' : 'Copy response'"
        @click.stop="copy"
      >
        <svg v-if="copied" viewBox="0 0 24 24" fill="none">
          <path d="M20 6 9 17l-5-5" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="none">
          <rect x="9" y="9" width="12" height="12" rx="2"
                stroke="currentColor" stroke-width="1.6" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" stroke="currentColor"
                stroke-width="1.6" stroke-linecap="round" />
        </svg>
      </IconButton>
    </template>
  </div>
</template>
