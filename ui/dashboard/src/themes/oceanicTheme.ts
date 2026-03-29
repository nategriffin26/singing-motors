import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

interface OceanicRuntimeState {
  pulse: number;
}

function ensureRuntime(runtime: Record<string, unknown>): OceanicRuntimeState {
  if (typeof runtime.pulse !== 'number') {
    runtime.pulse = 0;
  }
  return runtime as unknown as OceanicRuntimeState;
}

export const oceanicTheme: ViewerTheme = {
  id: 'oceanic',
  label: 'Oceanic',
  summary: 'Deep sea with bioluminescent accents.',
  dprCap: 2.0,
  createRuntimeState: () => ({ pulse: 0 }),
  draw: ({ ctx, viewport, scene, runtime: runtimeBag, drawUprightText }: ThemeDrawParams) => {
    const runtime = ensureRuntime(runtimeBag);
    const { geometry, frame, visibleBars, activeBarIds, activePitchColors, noteRange, smoothPlayheadUs } = scene;
    const { cssWidth: width, cssHeight: height } = viewport;
    const {
      left,
      right,
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

    // Deep abyss gradient
    const bg = ctx.createLinearGradient(0, 0, 0, height);
    bg.addColorStop(0, '#040d14');
    bg.addColorStop(1, '#0b1d2e');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    runtime.pulse = (runtime.pulse + 0.015) % (Math.PI * 2);

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(0, 15, 30, 0.4)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? 'rgba(0, 200, 255, 0.15)' : 'rgba(0, 150, 200, 0.05)';
      ctx.lineWidth = note % 12 === 0 ? 1.5 : 0.5;
      ctx.beginPath();
      ctx.moveTo(timelineLeft, y);
      ctx.lineTo(timelineRight, y);
      ctx.stroke();
    }

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);
      const activeColor = activePitchColors.get(note);
      
      ctx.fillStyle = black ? '#071826' : '#0c263d';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);

      if (activeColor) {
        ctx.fillStyle = 'rgba(0, 255, 255, 0.3)'; // Bioluminescent glow
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);
        ctx.fillStyle = '#00ffff'; // Bright edge
        ctx.fillRect(left + geometry.pianoWidth - 3, y, 3, noteHeight + 0.5);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#ffffff' : '#00aaff';
        ctx.font = `${clamp(Math.round(noteHeight * 0.6), 10, 12)}px sans-serif`;
        ctx.textBaseline = 'middle';
        drawUprightText(midiLabel(note), left + 7, y + noteHeight * 0.52);
      }
    }

    if (frame) {
      for (const beatUs of frame.beat_markers_us) {
        const x = playheadX + (beatUs - smoothPlayheadUs) * pxPerUs;
        if (x < timelineLeft - 1 || x > timelineRight + 1) {
          continue;
        }
        const major = Math.round(beatUs / 250_000) % 4 === 0;
        ctx.strokeStyle = major ? 'rgba(0, 200, 255, 0.2)' : 'rgba(0, 150, 200, 0.1)';
        ctx.lineWidth = major ? 1.5 : 1;
        ctx.beginPath();
        ctx.moveTo(x, timelineTop);
        ctx.lineTo(x, timelineBottom);
        ctx.stroke();
      }

      const pad = Math.max(1, noteHeight * 0.15);
      for (const bar of visibleBars) {
        const startX = playheadX + (bar.start_us - smoothPlayheadUs) * pxPerUs;
        const endX = playheadX + (bar.end_us - smoothPlayheadUs) * pxPerUs;
        if (endX < playheadX || startX > timelineRight) {
          continue;
        }

        const x = clamp(startX, playheadX, timelineRight);
        const widthPx = Math.max(1, clamp(endX - x, 1, timelineRight - x));
        const pitch = clamp(Math.round(bar.pitch), noteRange.min, noteRange.max);
        const y = timelineTop + (noteRange.max - pitch) * noteHeight + pad;
        const h = Math.max(2, noteHeight - pad * 2);
        const active = activeBarIds.has(Math.round(bar.id));
        const color = resolveBarColor(bar, scene.colorMode, { active });

        ctx.fillStyle = active ? hexToRgba(color, 0.8) : hexToRgba(color, 0.3);
        ctx.fillRect(x, y, widthPx, h);
        
        if (active) {
            ctx.shadowColor = color;
            ctx.shadowBlur = 10;
        }
        ctx.strokeStyle = active ? '#ffffff' : hexToRgba(color, 0.6);
        ctx.lineWidth = active ? 1.5 : 1;
        ctx.strokeRect(x, y, widthPx, h);
        ctx.shadowBlur = 0; // reset
      }
    }

    // Playhead glowing line
    ctx.shadowColor = '#00ffff';
    ctx.shadowBlur = 15;
    ctx.strokeStyle = '#00ffff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();
    ctx.shadowBlur = 0;

    ctx.fillStyle = '#ffffff';
    ctx.fillRect(playheadX - 1.5, timelineTop - 8, 3, 8);

    ctx.strokeStyle = 'rgba(0, 150, 255, 0.3)';
    ctx.lineWidth = 1;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = 'rgba(0, 200, 255, 0.6)';
      ctx.font = `600 ${clamp(Math.round(width * 0.02), 13, 20)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
