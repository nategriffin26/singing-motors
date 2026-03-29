import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  drawRoundedRect,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  color: string;
  life: number;
  maxLife: number;
  size: number;
}

interface NeonRuntimeState {
  particles: Particle[];
  prevActiveBarIds: Set<number>;
}

function ensureRuntime(runtime: Record<string, unknown>): NeonRuntimeState {
  if (!Array.isArray(runtime.particles)) {
    runtime.particles = [];
  }
  if (!(runtime.prevActiveBarIds instanceof Set)) {
    runtime.prevActiveBarIds = new Set<number>();
  }
  return runtime as unknown as NeonRuntimeState;
}

export const neonTheme: ViewerTheme = {
  id: 'neon',
  label: 'Neon',
  summary: 'Current high-glow stage with particles and beam accents.',
  dprCap: 2.0,
  createRuntimeState: () => ({
    particles: [] as Particle[],
    prevActiveBarIds: new Set<number>(),
  }),
  draw: ({
    ctx,
    viewport,
    scene,
    liteMode,
    runtime: runtimeBag,
    drawUprightText,
  }: ThemeDrawParams) => {
    const runtime = ensureRuntime(runtimeBag);
    const { particles, prevActiveBarIds } = runtime;
    const { geometry, frame, visibleBars, activeBarIds, activePitchColors, noteRange, smoothPlayheadUs } = scene;

    const { cssWidth: width, cssHeight: height } = viewport;
    const {
      left,
      right,
      pianoWidth,
      timelineLeft,
      timelineRight,
      timelineTop,
      timelineBottom,
      timelineWidth,
      timelineHeight,
      playheadX,
      noteHeight,
      pxPerUs,
    } = geometry;

    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, '#080718');
    bg.addColorStop(0.55, '#101033');
    bg.addColorStop(1, '#1a1030');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    const glow = ctx.createRadialGradient(width * 0.82, height * 0.1, 10, width * 0.82, height * 0.1, width * 0.8);
    glow.addColorStop(0, 'rgba(255, 99, 164, 0.32)');
    glow.addColorStop(1, 'rgba(255, 99, 164, 0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, width, height);

    const playheadGlow = ctx.createRadialGradient(playheadX, height * 0.5, 10, playheadX, height * 0.5, width * 0.6);
    playheadGlow.addColorStop(0, 'rgba(0, 245, 255, 0.12)');
    playheadGlow.addColorStop(1, 'rgba(0, 245, 255, 0)');
    ctx.fillStyle = playheadGlow;
    ctx.fillRect(0, 0, width, height);

    ctx.fillStyle = 'rgba(14, 16, 42, 0.45)';
    ctx.fillRect(timelineLeft, timelineTop, timelineWidth, timelineHeight);

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);
      if (black) {
        ctx.fillStyle = 'rgba(26, 24, 58, 0.48)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      const drawFineLine = noteHeight >= 6;
      if (drawFineLine || note % 12 === 0) {
        ctx.strokeStyle = note % 12 === 0 ? 'rgba(149, 209, 255, 0.34)' : 'rgba(130, 140, 188, 0.18)';
        ctx.lineWidth = note % 12 === 0 ? 1.4 : 1;
        ctx.beginPath();
        ctx.moveTo(timelineLeft, y);
        ctx.lineTo(timelineRight, y);
        ctx.stroke();
      }
    }

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);
      const activeColor = activePitchColors.get(note);

      const keyOffset = activeColor ? 2 : 0;
      const currentPianoWidth = pianoWidth - keyOffset;
      const keyX = left + keyOffset;

      ctx.fillStyle = black ? '#161728' : '#ece7ff';
      ctx.fillRect(keyX, y, currentPianoWidth, noteHeight + 0.75);

      if (activeColor) {
        ctx.fillStyle = 'rgba(0, 0, 0, 0.4)';
        ctx.fillRect(left, y, keyOffset, noteHeight + 0.75);

        ctx.fillStyle = hexToRgba(activeColor, black ? 0.5 : 0.4);
        ctx.fillRect(keyX, y, currentPianoWidth, noteHeight + 0.75);

        ctx.fillStyle = hexToRgba(activeColor, 0.8);
        ctx.fillRect(keyX + currentPianoWidth - 3, y, 3, noteHeight + 0.75);

        if (!liteMode) {
          ctx.save();
          ctx.globalCompositeOperation = 'screen';
          ctx.shadowColor = activeColor;
          ctx.shadowBlur = 10;
          ctx.fillStyle = hexToRgba(activeColor, 0.35);
          ctx.fillRect(keyX, y, currentPianoWidth, noteHeight + 0.75);
          ctx.restore();
        }
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#ffffff' : '#9ea4d4';
        ctx.font = `${clamp(Math.round(noteHeight * 0.65), 10, 12)}px "IBM Plex Sans", sans-serif`;
        ctx.textBaseline = 'middle';
        const textX = keyX + 7;
        drawUprightText(midiLabel(note), textX, y + noteHeight * 0.52);
      }
    }

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.18)';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(timelineLeft, timelineTop);
    ctx.lineTo(timelineLeft, timelineBottom);
    ctx.stroke();

    if (frame) {
      const beatStepUs =
        frame.beat_markers_us.length >= 2
          ? Math.abs(frame.beat_markers_us[1] - frame.beat_markers_us[0])
          : 250_000;
      for (let idx = 0; idx < frame.beat_markers_us.length; idx += 1) {
        const beatUs = frame.beat_markers_us[idx];
        const x = playheadX + (beatUs - smoothPlayheadUs) * pxPerUs;
        if (x < timelineLeft - 1 || x > timelineRight + 1) {
          continue;
        }

        const globalBeatIdx = beatStepUs > 0 ? Math.round(beatUs / beatStepUs) : idx;
        const major = globalBeatIdx % 4 === 0;
        let strokeAlpha = major ? 0.45 : 0.15;
        let lineWidth = major ? 2 : 1;

        if (major) {
          const dist = Math.abs(x - playheadX);
          const pulseRange = 150;
          if (dist < pulseRange) {
            const intensity = 1 - dist / pulseRange;
            strokeAlpha += intensity * 0.4;
            lineWidth += intensity * 1.5;
          }
        }

        ctx.strokeStyle = major ? `rgba(255, 98, 165, ${strokeAlpha})` : `rgba(130, 219, 255, ${strokeAlpha})`;
        ctx.lineWidth = lineWidth;
        ctx.beginPath();
        ctx.moveTo(x, timelineTop);
        ctx.lineTo(x, timelineBottom);
        ctx.stroke();
      }

      const barPadding = Math.max(1, noteHeight * 0.12);
      for (const bar of visibleBars) {
        const barStartX = playheadX + (bar.start_us - smoothPlayheadUs) * pxPerUs;
        const barEndX = playheadX + (bar.end_us - smoothPlayheadUs) * pxPerUs;
        if (barEndX < playheadX || barStartX > timelineRight) {
          continue;
        }

        const pitch = clamp(Math.round(bar.pitch), noteRange.min, noteRange.max);
        const y = timelineTop + (noteRange.max - pitch) * noteHeight + barPadding;
        const h = Math.max(2, noteHeight - barPadding * 2);
        if (y > timelineBottom || y + h < timelineTop) {
          continue;
        }

        const x = clamp(barStartX, playheadX, timelineRight);
        const widthPx = Math.max(1, clamp(barEndX - x, 1, timelineRight - x));
        const active = activeBarIds.has(Math.round(bar.id));
        const color = resolveBarColor(bar, scene.colorMode, { active });
        const velocityNorm = clamp(bar.velocity / 127, 0, 1);
        const alpha = active ? 0.6 + velocityNorm * 0.4 : 0.38 + velocityNorm * 0.4;

        const beamGrad = ctx.createLinearGradient(x, y, x + widthPx, y);
        beamGrad.addColorStop(0, hexToRgba(color, active ? 0.9 : alpha));
        beamGrad.addColorStop(1, hexToRgba(color, active ? 0.2 : alpha * 0.3));

        if (active && !liteMode) {
          ctx.save();
          ctx.globalCompositeOperation = 'screen';
          ctx.shadowColor = hexToRgba(color, 0.9);
          ctx.shadowBlur = 24;
          drawRoundedRect(ctx, x, y, widthPx, h, Math.min(5, h / 2));
          ctx.fillStyle = hexToRgba(color, 0.6);
          ctx.fill();
          ctx.restore();
        }

        drawRoundedRect(ctx, x, y, widthPx, h, Math.min(5, h / 2));
        ctx.fillStyle = beamGrad;
        ctx.fill();

        ctx.strokeStyle = active ? hexToRgba(color, 1) : hexToRgba(color, 0.75);
        ctx.lineWidth = active ? 2 : 1;
        ctx.stroke();

        if (active && widthPx > 4) {
          ctx.save();
          ctx.globalCompositeOperation = 'screen';
          ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
          const brightWidth = Math.min(widthPx, 6);
          drawRoundedRect(ctx, x, y, brightWidth, h, Math.min(2, h / 2));
          ctx.fill();
          ctx.restore();
        }
      }
    }

    ctx.strokeStyle = 'rgba(0, 245, 255, 0.95)';
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();

    for (const bar of visibleBars) {
      if (!activeBarIds.has(Math.round(bar.id))) continue;

      const pitch = clamp(Math.round(bar.pitch), noteRange.min, noteRange.max);
      const cy = timelineTop + (noteRange.max - pitch) * noteHeight + noteHeight * 0.5;
      const color = resolveBarColor(bar, scene.colorMode, { active: true });
      const velocityNorm = clamp(bar.velocity / 127, 0, 1);
      const intensity = 0.55 + velocityNorm * 0.45;
      const radiusY = Math.max(noteHeight * 1.5, 10);
      const radiusX = Math.max(12, radiusY * 0.6);

      ctx.save();
      ctx.scale(radiusX / radiusY, 1);
      const outerGlow = ctx.createRadialGradient(
        playheadX * (radiusY / radiusX),
        cy,
        0,
        playheadX * (radiusY / radiusX),
        cy,
        radiusY,
      );
      outerGlow.addColorStop(0, hexToRgba(color, 0.45 * intensity));
      outerGlow.addColorStop(0.5, hexToRgba(color, 0.2 * intensity));
      outerGlow.addColorStop(1, hexToRgba(color, 0));
      ctx.fillStyle = outerGlow;
      ctx.fillRect(playheadX * (radiusY / radiusX) - radiusY, cy - radiusY, radiusY * 2, radiusY * 2);
      ctx.restore();

      const coreR = Math.max(noteHeight * 0.7, 5);
      const coreGlow = ctx.createRadialGradient(playheadX, cy, 0, playheadX, cy, coreR);
      coreGlow.addColorStop(0, hexToRgba(color, intensity));
      coreGlow.addColorStop(0.6, hexToRgba(color, 0.5 * intensity));
      coreGlow.addColorStop(1, hexToRgba(color, 0));
      ctx.fillStyle = coreGlow;
      ctx.fillRect(playheadX - coreR, cy - coreR, coreR * 2, coreR * 2);

      const dotR = Math.max(noteHeight * 0.28, 2.5);
      const dot = ctx.createRadialGradient(playheadX, cy, 0, playheadX, cy, dotR);
      dot.addColorStop(0, `rgba(255,255,255,${0.9 * intensity})`);
      dot.addColorStop(0.5, hexToRgba(color, 0.6 * intensity));
      dot.addColorStop(1, hexToRgba(color, 0));
      ctx.fillStyle = dot;
      ctx.fillRect(playheadX - dotR, cy - dotR, dotR * 2, dotR * 2);
    }

    if (!liteMode) {
      ctx.fillStyle = 'rgba(0, 245, 255, 0.85)';
      ctx.beginPath();
      ctx.moveTo(playheadX, timelineTop);
      ctx.lineTo(playheadX - 7, timelineTop - 10);
      ctx.lineTo(playheadX + 7, timelineTop - 10);
      ctx.closePath();
      ctx.fill();
    }

    for (const bar of visibleBars) {
      const barId = Math.round(bar.id);
      if (!activeBarIds.has(barId)) {
        continue;
      }
      const pitch = clamp(Math.round(bar.pitch), noteRange.min, noteRange.max);
      const cy = timelineTop + (noteRange.max - pitch) * noteHeight + noteHeight * 0.5;
      const color = resolveBarColor(bar, scene.colorMode, { active: true });

      if (!prevActiveBarIds.has(barId)) {
        const spawnCount = Math.floor(Math.random() * 3) + 4;
        for (let idx = 0; idx < spawnCount; idx += 1) {
          const angle = Math.PI + (Math.random() - 0.5) * Math.PI * 1.4;
          const speed = Math.random() * 3 + 1.5;
          particles.push({
            x: playheadX,
            y: cy + (Math.random() - 0.5) * noteHeight,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            color,
            life: Math.random() * 0.2 + 0.2,
            maxLife: 0.4,
            size: Math.random() * 1.5 + 0.8,
          });
        }
      }

      if (Math.random() > 0.8) {
        const dir = Math.random() > 0.35 ? -1 : 1;
        particles.push({
          x: playheadX,
          y: cy + (Math.random() - 0.5) * noteHeight,
          vx: dir * (Math.random() * 2 + 0.3),
          vy: (Math.random() - 0.5) * 1.2,
          color,
          life: Math.random() * 0.15 + 0.1,
          maxLife: 0.25,
          size: Math.random() * 1.2 + 0.5,
        });
      }
    }

    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    for (let idx = particles.length - 1; idx >= 0; idx -= 1) {
      const p = particles[idx];
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.05;
      p.life -= 0.016;

      if (p.life <= 0) {
        particles.splice(idx, 1);
        continue;
      }

      const alpha = Math.max(0, p.life / p.maxLife);
      ctx.fillStyle = hexToRgba(p.color, alpha * 0.9);
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size * (0.5 + 0.5 * alpha), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    prevActiveBarIds.clear();
    for (const id of activeBarIds) {
      prevActiveBarIds.add(id);
    }

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.23)';
    ctx.lineWidth = 1;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = 'rgba(233, 239, 255, 0.9)';
      ctx.font = `600 ${clamp(Math.round(width * 0.022), 14, 22)}px "IBM Plex Sans", sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
