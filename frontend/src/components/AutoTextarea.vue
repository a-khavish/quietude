<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  AutoTextarea.vue
  A text box that grows with what you type, up to a point, and cannot be
  dragged.

  Both places that take free text in Quietude want the same behaviour: start
  as one line so it doesn't dominate the panel, grow as the text wraps so
  you can see what you wrote, stop at a fixed ceiling so a long paragraph
  never pushes the send button off screen, and scroll inside from there.
  The drag handle a textarea normally carries is removed - it lets the
  box be pulled over the rest of the interface, and the growth makes it
  redundant anyway.

  One component rather than two, because the awkward part is the
  measurement and it would be the same awkward part twice.
-->
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  /* The ceiling, in lines. This is the "fixed size" the box grows to;
     past it the text scrolls inside instead of the box getting taller. */
  maxRows: { type: Number, default: 6 },
  /* The starting height. One line keeps it looking like the single-line
     field it replaces until there's a reason to be taller. */
  minRows: { type: Number, default: 1 },
  disabled: { type: Boolean, default: false },
  /* Enter submits and Shift+Enter makes a new line - the convention for
     a message box. Turned off where a newline is the likelier intent. */
  submitOnEnter: { type: Boolean, default: true },
  ariaLabel: { type: String, default: null },
})

const emit = defineEmits(['update:modelValue', 'submit'])

const el = ref(null)

/**
 * Sets the height to fit the content, capped at maxRows.
 *
 * The height is measured rather than calculated from the text, because
 * a textarea wraps on word boundaries at whatever width it currently
 * has: how many lines a given string occupies is a fact about the
 * rendered box, and no amount of counting characters will tell you what
 * it is. scrollHeight reports the full height of the content even while
 * the box is shorter than it, which is exactly the number needed.
 */
function fit() {
  const node = el.value
  if (!node) return

  // Collapse before measuring, and hide the scrollbar while doing it.
  // scrollHeight never reports less than the element's own height, so a
  // box already grown to six rows keeps claiming six rows of content
  // after the text is deleted - without this the box would only ever
  // get taller. Hiding the scrollbar matters too: its width changes the
  // wrap, so measuring with it visible measures the wrong layout.
  node.style.overflowY = 'hidden'
  node.style.height = 'auto'

  const s = getComputedStyle(node)
  const line = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.4 || 20
  const padding = parseFloat(s.paddingTop) + parseFloat(s.paddingBottom)
  const border = parseFloat(s.borderTopWidth) + parseFloat(s.borderBottomWidth)

  // scrollHeight includes padding but not border, while the CSS height
  // property means content+padding+border under border-box and content
  // alone under content-box. Converting with the wrong one leaves the
  // box a few pixels short, which reads as a box that is permanently
  // scrolled by one line - so it's worth asking rather than assuming.
  const borderBox = s.boxSizing === 'border-box'
  const content = node.scrollHeight - padding

  const wanted = Math.max(content, line * props.minRows)
  const ceiling = line * props.maxRows
  const capped = Math.min(wanted, ceiling)

  node.style.height = `${borderBox ? capped + padding + border : capped}px`
  // The 1px slack keeps a box whose content lands exactly on the ceiling
  // from showing a scrollbar for nothing, which fractional line heights
  // make a common case.
  node.style.overflowY = wanted > ceiling + 1 ? 'auto' : 'hidden'
}

function onInput(event) {
  emit('update:modelValue', event.target.value)
  // Also fitted here, not only from the watcher: a parent is free to
  // not write the value back (the chat box does exactly that while
  // voice chat is driving it), and the box should still fit what's in
  // it either way.
  fit()
}

function onKeydown(event) {
  if (event.key !== 'Enter') return
  if (!props.submitOnEnter) return
  // Shift+Enter is the new line. isComposing is the one that's easy to
  // miss: with an IME active, Enter confirms the candidate word, and
  // treating that as a submit would send half-typed Japanese or Chinese
  // every time someone picks a character.
  if (event.shiftKey || event.isComposing) return
  event.preventDefault()
  emit('submit')
}

let observer = null
let lastWidth = 0

onMounted(() => {
  fit()
  if (typeof ResizeObserver === 'undefined') return
  // A width change rewraps the text and so changes the line count: the
  // window resizing, a panel reflowing, the scrollbar appearing beside
  // it. Watching the element covers all of them, where a window resize
  // listener would only cover the first.
  //
  // Guarded on width, because this observer is watching the very
  // element whose height fit() sets - reacting to our own height change
  // would be a loop.
  observer = new ResizeObserver(([entry]) => {
    const width = entry.contentRect.width
    if (Math.abs(width - lastWidth) < 0.5) return
    lastWidth = width
    fit()
  })
  observer.observe(el.value)
})

onBeforeUnmount(() => observer?.disconnect())

// flush: 'post' so the textarea's value attribute has already been
// patched by the time the measurement happens. This is the path that
// handles a programmatic change - the box being cleared after a message
// is sent, or a live transcript landing in it.
watch(() => props.modelValue, fit, { flush: 'post' })

defineExpose({
  focus: () => el.value?.focus(),
  fit,
})
</script>

<template>
  <textarea
    ref="el"
    class="auto-textarea"
    :value="modelValue"
    :rows="minRows"
    :placeholder="placeholder"
    :disabled="disabled"
    :aria-label="ariaLabel || undefined"
    autocomplete="off"
    @input="onInput"
    @keydown="onKeydown"
  />
</template>

<style scoped>
/* Deliberately only the structural rules. Type, colour, border and
   line height come from the stylesheet that styles the panel the box
   sits in, the same way its single-line predecessor got them - a scoped
   rule here outranks those, so anything set here would quietly win an
   argument it has no business being in. */
.auto-textarea {
  /* The whole point: no drag handle. The box sizes itself, and a handle
     would only let it be pulled out over the rest of the interface. */
  resize: none;
  display: block;
  /* Width is the consumer's business - both places this is used put it
     in a flex row and give it flex:1. min-width:0 is what lets it
     actually shrink there, since a textarea's default cols would
     otherwise hold the row open. */
  min-width: 0;
  overflow-y: hidden;
}
</style>
