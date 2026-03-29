import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

interface PhosphorGhost {
  x: number;
  y: number;
  w: number;
  h: number;
  life: number;
  color: string;
}

interface RetroRuntimeState {
  ghosts: PhosphorGhost[];
  scanOffset: number;
}

function ensureRuntime(runtime: Record<string, unknown>): RetroRuntimeState {
  if (!Array.isArray(runtime.ghosts)) {
    runtime.ghosts = [];
  }
  if (typeof runtime.scanOffset !== 'number') {
    runtime.scanOffset = 0;
  }
  return runtime as unknown as RetroRuntimeState;
}

export const retroTheme: ViewerTheme = {
  id: 'retro',
  label: 'Retro',
  summary: 'CRT monitor style with scanlines and phosphor afterglow.',
  dprCap: 1.6,
  createRuntimeState: () => ({
    ghosts: [] as PhosphorGhost[],
    scanOffset: 0,
  }),
  draw: ({ ctx, viewport, scene, runtime: runtimeBag, drawUprightText }: ThemeDrawParams) => {
    const runtime = ensureRuntime(runtimeBag);
    const { ghosts } = runtime;
    const { geometry, frame, visibleBars, activeBarIds, activePitchColors, noteRange, smoothPlayheadUs } = scene;
    const { cssWidth: width, cssHeight: height } = viewport;
    const {
      top,
      right,
      left,
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
    bg.addColorStop(0, '#020502');
    bg.addColorStop(0.65, '#05130a');
    bg.addColorStop(1, '#020602');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    runtime.scanOffset = (runtime.scanOffset + 0.45) % 6;
    ctx.strokeStyle = 'rgba(139, 255, 171, 0.06)';
    ctx.lineWidth = 1;
    for (let y = runtime.scanOffset; y < height; y += 6) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    ctx.fillStyle = 'rgba(4, 19, 10, 0.7)';
    ctx.fillRect(timelineLeft, timelineTop, timelineWidth, timelineHeight);

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);
      if (black) {
        ctx.fillStyle = 'rgba(9, 26, 15, 0.55)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }
      if (note % 12 === 0 || noteHeight >= 7) {
        ctx.strokeStyle = note % 12 === 0 ? 'rgba(193, 255, 106, 0.26)' : 'rgba(113, 189, 119, 0.18)';
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

      ctx.fillStyle = black ? '#0f200f' : '#bacfab';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);

      if (activeColor) {
        ctx.fillStyle = hexToRgba(activeColor, black ? 0.32 : 0.24);
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);
        ctx.fillStyle = 'rgba(232, 255, 198, 0.8)';
        ctx.fillRect(left + geometry.pianoWidth - 2, y, 2, noteHeight + 0.5);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#f6ffd2' : '#56745a';
        ctx.font = `${clamp(Math.round(noteHeight * 0.65), 10, 12)}px "Courier Prime", monospace`;
        ctx.textBaseline = 'middle';
        drawUprightText(midiLabel(note), left + 6, y + noteHeight * 0.52);
      }
    }

    if (frame) {
      for (const beatUs of frame.beat_markers_us) {
        const x = playheadX + (beatUs - smoothPlayheadUs) * pxPerUs;
        if (x < timelineLeft - 1 || x > timelineRight + 1) {
          continue;
        }
        const major = Math.round(beatUs / 250_000) % 4 === 0;
        ctx.strokeStyle = major ? 'rgba(255, 197, 67, 0.5)' : 'rgba(139, 255, 171, 0.16)';
        ctx.lineWidth = major ? 1.8 : 1;
        ctx.beginPath();
        ctx.moveTo(x, timelineTop);
        ctx.lineTo(x, timelineBottom);
        ctx.stroke();
      }

      const barPad = Math.max(1, noteHeight * 0.15);
      for (const bar of visibleBars) {
        const startX = playheadX + (bar.start_us - smoothPlayheadUs) * pxPerUs;
        const endX = playheadX + (bar.end_us - smoothPlayheadUs) * pxPerUs;
        if (endX < playheadX || startX > timelineRight) {
          continue;
        }
        const x = clamp(startX, playheadX, timelineRight);
        const widthPx = Math.max(1, clamp(endX - x, 1, timelineRight - x));
        const pitch = clamp(Math.round(bar.pitch), noteRange.min, noteRange.max);
        const y = timelineTop + (noteRange.max - pitch) * noteHeight + barPad;
        const h = Math.max(2, noteHeight - barPad * 2);
        const active = activeBarIds.has(Math.round(bar.id));
        const color = resolveBarColor(bar, scene.colorMode, { active });

        const base = ctx.createLinearGradient(x, y, x + widthPx, y);
        base.addColorStop(0, active ? hexToRgba(color, 0.9) : hexToRgba(color, 0.5));
        base.addColorStop(1, active ? hexToRgba(color, 0.45) : hexToRgba(color, 0.2));
        ctx.fillStyle = base;
        ctx.fillRect(Math.floor(x), Math.floor(y), Math.ceil(widthPx), Math.ceil(h));

        ctx.strokeStyle = active ? 'rgba(247, 255, 223, 0.85)' : 'rgba(165, 225, 173, 0.52)';
        ctx.lineWidth = active ? 1.5 : 1;
        ctx.strokeRect(Math.floor(x), Math.floor(y), Math.ceil(widthPx), Math.ceil(h));

        if (active) {
          ghosts.push({
            x,
            y,
            w: Math.max(2, widthPx * 0.35),
            h,
            life: 0.5,
            color,
          });
        }
      }
    }

    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    for (let idx = ghosts.length - 1; idx >= 0; idx -= 1) {
      const ghost = ghosts[idx];
      ghost.life -= 0.02;
      ghost.x -= 1.2;
      if (ghost.life <= 0 || ghost.x + ghost.w < timelineLeft) {
        ghosts.splice(idx, 1);
        continue;
      }
      ctx.fillStyle = hexToRgba(ghost.color, ghost.life * 0.5);
      ctx.fillRect(ghost.x, ghost.y, ghost.w, ghost.h);
    }
    ctx.restore();

    ctx.strokeStyle = 'rgba(255, 201, 77, 0.95)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();

    ctx.fillStyle = 'rgba(255, 221, 137, 0.75)';
    ctx.fillRect(playheadX - 1, top - 8, 2, 8);

    ctx.strokeStyle = 'rgba(172, 255, 187, 0.3)';
    ctx.lineWidth = 1;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = 'rgba(226, 255, 197, 0.88)';
      ctx.font = `600 ${clamp(Math.round(width * 0.021), 13, 21)}px "Courier Prime", monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
