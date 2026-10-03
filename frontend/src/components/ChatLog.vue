<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ChatLog.vue
  The scrolling message list.

  It follows the newest message the way a chat app does: pinned to the
  bottom until you scroll away from it, and pinned again the moment you
  come back. Two things it has to get right, and both were wrong.

  Coming back from another page landed you at the top. The messages are
  held in the store rather than in this component, so returning from the
  reference or the settings screen rebuilds a log that is already full -
  nothing is appended, no watcher fires, and the scroller sits where a
  fresh element sits, which is at the first message you were ever sent.
  It scrolls to the end on arrival now.

  And it only followed a message being *added*. The bottom moves for
  other reasons: a long reply being expanded, the window being resized,
  the playback bar appearing above the panel. Each of those left the
  newest message cut off below the fold with nothing to bring it back.
  So what is watched is the size of the content and the size of the
  viewport, rather than the number of messages - the question is "has
  the bottom moved", and that is the thing that answers it.
-->
<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatBubble from '@/components/ChatBubble.vue'

const props = defineProps({
  messages: { type: Array, required: true },
  showActions: { type: Boolean, default: false },
  speakingId: { type: [Number, null], default: null },
})

const emit = defineEmits(['speak', 'stop-speaking'])

const log = ref(null)
const inner = ref(null)

// How close to the end still counts as being at the end. A line of text
// of slack, so that resting a pixel short of the bottom does not read as
// having walked away from it.
const STICK_THRESHOLD_PX = 60

// Whether the view follows new messages. The whole of the behaviour is
// this flag and the rules that set it.
let stick = true
let lastTop = 0
// How tall the scroller itself was last time anything looked, so that
// the browser repositioning it during a resize can be told apart from
// somebody scrolling it. See onScroll.
let lastView = 0
let observer = null

function fromBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight
}

function pin() {
  const el = log.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  remember(el)
}

function remember(el) {
  lastTop = el.scrollTop
  lastView = el.clientHeight
}

/**
 * Scrolling up is a decision; being near the bottom is a position.
 *
 * Distance alone is not enough. A reply can arrive in the moment
 * between starting to scroll up and getting clear of the threshold, and
 * being snatched back to the bottom mid-gesture is the thing that makes
 * a log feel like it is fighting you. So any deliberate upward movement
 * lets go, however small, and coming to rest at the bottom takes hold
 * again - which is the one gesture that unambiguously means "I'm
 * following along".
 *
 * And a scroll event is not always somebody scrolling. Making the
 * window smaller moves the scroller itself: the browser repositions it
 * to keep what you were looking at where it was, and that arrives as a
 * scroll event *before* the resize is reported - so the log read a
 * resize as the reader walking away, and then declined to follow
 * anything ever again.
 *
 * What is tested for is the scroller's own height changing, not the
 * content's. The content grows every time a message arrives, and
 * ignoring a scroll because of that would swallow a real one from
 * somebody who scrolled up at the moment a reply landed - which is
 * precisely the moment they are most likely to.
 */
function onScroll() {
  const el = log.value
  if (!el) return
  const resized = el.clientHeight !== lastView
  const distance = fromBottom(el)
  const movedUp = el.scrollTop < lastTop - 1
  remember(el)
  if (resized) return

  if (movedUp && distance > 2) stick = false
  else if (distance <= STICK_THRESHOLD_PX) stick = true
}

function onResize() {
  if (stick) pin()
  // Recorded either way. Left only to pin(), a log somebody had
  // scrolled away from would keep a height from before the window
  // changed, and read their next scroll as the resize it had missed.
  else if (log.value) remember(log.value)
}

/*
 * Sending something always takes you to the bottom.
 *
 * The rule above is about *her* messages, which should not move the
 * view while you are reading something further up. Your own are the
 * opposite: you wrote it, you pressed Enter, and not being shown it
 * would be the application ignoring you. So anything you send takes
 * hold again whether or not you had let go.
 */
watch(
  () => props.messages[props.messages.length - 1]?.id,
  () => {
    const last = props.messages[props.messages.length - 1]
    if (last?.role !== 'user') return
    stick = true
    nextTick(pin)
  },
)

onMounted(() => {
  log.value?.addEventListener('scroll', onScroll, { passive: true })

  // Both boxes: the content, which grows as messages arrive and as a
  // long one is expanded, and the scroller itself, which shrinks when
  // the window does or when something appears above it. Either one
  // moves the bottom.
  //
  // A ResizeObserver reports its first measurement as soon as it starts
  // observing, so this is also what puts a log restored from the store
  // at its newest message - no separate scroll-on-mount that has to
  // guess when the bubbles have finished laying out.
  if (log.value) remember(log.value)
  observer = new ResizeObserver(onResize)
  if (inner.value) observer.observe(inner.value)
  if (log.value) observer.observe(log.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  log.value?.removeEventListener('scroll', onScroll)
})

defineExpose({
  scrollToBottom() {
    stick = true
    pin()
  },
})
</script>

<template>
  <div ref="log" class="chat-log">
    <div ref="inner" class="chat-log-inner">
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
  </div>
</template>
