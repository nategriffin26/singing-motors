import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

interface MinimalRuntimeState {
  pulse: number;
}

function ensureRuntime(runtime: Record<string, unknown>): MinimalRuntimeState {
  if (typeof runtime.pulse !== 'number') {
    runtime.pulse = 0;
  }
  return runtime as unknown as MinimalRuntimeState;
}

function desaturate(hex: string, alpha: number): string {
  const normalized = hex.replace('#', '');
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  const avg = Math.round((r + g + b) / 3);
  const toned = Math.round(avg * 0.55 + 80);
  return `rgba(${toned}, ${toned}, ${toned}, ${alpha})`;
}

export const minimalTheme: ViewerTheme = {
  id: 'minimal',
  label: 'Minimal',
  summary: 'Bright editorial layout with restrained motion.',
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

    const bg = ctx.createLinearGradient(0, 0, 0, height);
    bg.addColorStop(0, '#f9faf6');
    bg.addColorStop(1, '#e8ecdf');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    runtime.pulse = (runtime.pulse + 0.02) % (Math.PI * 2);
    const pulseAlpha = 0.08 + ((Math.sin(runtime.pulse) + 1) * 0.5) * 0.05;
    ctx.fillStyle = `rgba(87, 106, 84, ${pulseAlpha})`;
    ctx.fillRect(timelineLeft, timelineTop, timelineWidth, timelineHeight);

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(125, 138, 118, 0.14)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? 'rgba(84, 98, 78, 0.42)' : 'rgba(117, 129, 110, 0.18)';
      ctx.lineWidth = note % 12 === 0 ? 1.3 : 0.8;
      ctx.beginPath();
      ctx.moveTo(timelineLeft, y);
      ctx.lineTo(timelineRight, y);
      ctx.stroke();
    }

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);
      const activeColor = activePitchColors.get(note);
      ctx.fillStyle = black ? '#d7dfd0' : '#f7f9f2';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);

      if (activeColor) {
        ctx.fillStyle = desaturate(activeColor, black ? 0.38 : 0.25);
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);
        ctx.fillStyle = 'rgba(61, 71, 53, 0.72)';
        ctx.fillRect(left + geometry.pianoWidth - 2, y, 2, noteHeight + 0.5);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#2f3929' : '#66745f';
        ctx.font = `${clamp(Math.round(noteHeight * 0.63), 10, 12)}px "Libre Baskerville", serif`;
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
        ctx.strokeStyle = major ? 'rgba(80, 91, 71, 0.45)' : 'rgba(122, 131, 115, 0.22)';
        ctx.lineWidth = major ? 1.4 : 0.8;
        ctx.beginPath();
        ctx.moveTo(x, timelineTop);
        ctx.lineTo(x, timelineBottom);
        ctx.stroke();
      }

      const pad = Math.max(1, noteHeight * 0.16);
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

        ctx.fillStyle = active ? hexToRgba(color, 0.5) : desaturate(color, 0.32);
        ctx.fillRect(x, y, widthPx, h);
        ctx.strokeStyle = active ? 'rgba(33, 44, 30, 0.66)' : 'rgba(65, 79, 58, 0.42)';
        ctx.lineWidth = active ? 1.4 : 1;
        ctx.strokeRect(x, y, widthPx, h);
      }
    }

    ctx.strokeStyle = 'rgba(36, 46, 32, 0.92)';
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();

    ctx.fillStyle = 'rgba(44, 56, 39, 0.85)';
    ctx.fillRect(playheadX - 1, timelineTop - 8, 2, 8);

    ctx.strokeStyle = 'rgba(103, 116, 95, 0.35)';
    ctx.lineWidth = 1;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = 'rgba(51, 61, 45, 0.84)';
      ctx.font = `600 ${clamp(Math.round(width * 0.02), 13, 20)}px "Libre Baskerville", serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
