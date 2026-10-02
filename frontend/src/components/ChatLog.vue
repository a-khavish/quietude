<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ChatLog.vue
  The scrolling message list.

  Auto-scroll respects the reader: it only follows new messages when
  you're already at the bottom. The reference set scrollTop =
  scrollHeight on every append, which yanked you back down if you'd
  scrolled up to read something.
-->
<script setup>
import { nextTick, ref, watch } from 'vue'
import ChatBubble from '@/components/ChatBubble.vue'

const props = defineProps({
  messages: { type: Array, required: true },
  showActions: { type: Boolean, default: false },
  speakingId: { type: [Number, null], default: null },
})

const emit = defineEmits(['speak', 'stop-speaking'])

const log = ref(null)
const STICK_THRESHOLD_PX = 60

function atBottom() {
  const el = log.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX
}

watch(
  () => props.messages.length,
  async () => {
    const shouldStick = atBottom()
    await nextTick()
    if (shouldStick && log.value) log.value.scrollTop = log.value.scrollHeight
  },
)

defineExpose({
  scrollToBottom() {
    if (log.value) log.value.scrollTop = log.value.scrollHeight
  },
})
</script>

<template>
  <div ref="log" class="chat-log">
    <ChatBubble
      v-for="message in messages"
      :key="message.id"
      :message="message"
      :show-actions="showActions"
      :speaking="speakingId === message.id"
      @speak="emit('speak', message)"
      @stop-speaking="emit('stop-speaking')"
    />
  </div>
</template>
