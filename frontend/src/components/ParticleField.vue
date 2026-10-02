<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ParticleField.vue
  The background particle network, ported from static/js/particles.js.

  Only real change: it stops when the tab is hidden. The reference ran
  its requestAnimationFrame loop forever, which on a laptop is a
  constant GPU wakeup for an animation nobody is looking at.
-->
<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const canvas = ref(null)
let ctx = null
let frame = null
let particles = []
let width = 0
let height = 0

const DENSITY = 14000      // one particle per this many pixels
const MAX_PARTICLES = 90
const LINK_DISTANCE = 130

function resize() {
  if (!canvas.value) return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  width = canvas.value.clientWidth
  height = canvas.value.clientHeight
  canvas.value.width = width * dpr
  canvas.value.height = height * dpr
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  seed()
}

function seed() {
  const target = Math.min(MAX_PARTICLES, Math.round((width * height) / DENSITY))
  particles = Array.from({ length: target }, () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    vx: (Math.random() - 0.5) * 0.35,
    vy: (Math.random() - 0.5) * 0.35,
    r: Math.random() * 1.6 + 0.6,
  }))
}

function draw() {
  ctx.clearRect(0, 0, width, height)

  for (const p of particles) {
    p.x += p.vx
    p.y += p.vy
    if (p.x < 0 || p.x > width) p.vx *= -1
    if (p.y < 0 || p.y > height) p.vy *= -1
  }

  // Link nearby particles, fading the line out with distance. The inner
  // loop starts at i+1 so each pair is considered once.
  ctx.lineWidth = 0.6
  for (let i = 0; i < particles.length; i++) {
    for (let j = i + 1; j < particles.length; j++) {
      const dx = particles[i].x - particles[j].x
      const dy = particles[i].y - particles[j].y
      const dist = Math.hypot(dx, dy)
      if (dist > LINK_DISTANCE) continue
      ctx.strokeStyle = `rgba(45,226,230,${0.16 * (1 - dist / LINK_DISTANCE)})`
      ctx.beginPath()
      ctx.moveTo(particles[i].x, particles[i].y)
      ctx.lineTo(particles[j].x, particles[j].y)
      ctx.stroke()
    }
  }

  for (const p of particles) {
    ctx.fillStyle = 'rgba(45,226,230,0.55)'
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
    ctx.fill()
  }

  frame = requestAnimationFrame(draw)
}

function pause() {
  if (frame) cancelAnimationFrame(frame)
  frame = null
}

function resumeIfVisible() {
  if (document.hidden || frame) return
  frame = requestAnimationFrame(draw)
}

function onVisibilityChange() {
  if (document.hidden) pause()
  else resumeIfVisible()
}

onMounted(() => {
  ctx = canvas.value.getContext('2d')
  resize()
  window.addEventListener('resize', resize)
  document.addEventListener('visibilitychange', onVisibilityChange)
  resumeIfVisible()
})

onUnmounted(() => {
  pause()
  window.removeEventListener('resize', resize)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <canvas ref="canvas" class="particle-canvas" aria-hidden="true" />
</template>

<style scoped>
.particle-canvas {
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 0;
  background: radial-gradient(ellipse at 50% 0%, #0c2530 0%, #05080d 60%);
}
</style>
