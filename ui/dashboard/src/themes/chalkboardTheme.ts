import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

// Helper to jitter coordinates for a hand-drawn feel
function jitter(val: number, amount: number): number {
  return val + (Math.random() - 0.5) * amount;
}

export const chalkboardTheme: ViewerTheme = {
  id: 'chalkboard',
  label: 'Chalkboard',
  summary: 'Academic, lo-fi, and organic chalk aesthetic.',
  dprCap: 2.0,
  createRuntimeState: () => ({}),
  draw: ({ ctx, viewport, scene, drawUprightText }: ThemeDrawParams) => {
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

    // Chalkboard background
    ctx.fillStyle = '#2f3e36'; // Slate green
    ctx.fillRect(0, 0, width, height);

    // Subtle noise or dust could go here, but keeping it simple for performance
    ctx.fillStyle = 'rgba(255, 255, 255, 0.02)';
    for (let i = 0; i < 50; i++) {
        ctx.fillRect(Math.random() * width, Math.random() * height, Math.random() * 4, Math.random() * 4);
    }

    const chalkWhite = 'rgba(230, 240, 230, 0.8)';
    const chalkDim = 'rgba(230, 240, 230, 0.2)';

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(30, 40, 35, 0.4)'; // darker chalk dust
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? chalkWhite : chalkDim;
      ctx.lineWidth = note % 12 === 0 ? 2 : 1;
      
      // Rough line
      ctx.beginPath();
      ctx.moveTo(timelineLeft, jitter(y, 1));
      ctx.lineTo(timelineLeft + timelineWidth * 0.5, jitter(y, 1));
      ctx.lineTo(timelineRight, jitter(y, 1));
      ctx.stroke();
    }

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);
      const activeColor = activePitchColors.get(note);
      
      ctx.fillStyle = black ? '#25302a' : '#38483f';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);

      if (activeColor) {
        ctx.fillStyle = chalkDim; // Highlight
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);
        ctx.fillStyle = chalkWhite;
        ctx.fillRect(left + geometry.pianoWidth - 4, y, 4, noteHeight);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#ffffff' : chalkWhite;
        ctx.font = `${clamp(Math.round(noteHeight * 0.65), 10, 14)}px "Comic Sans MS", "Chalkboard SE", cursive`;
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
        ctx.strokeStyle = major ? chalkWhite : chalkDim;
        ctx.lineWidth = major ? 2 : 1;
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

        // Pastel adjustment for chalk
        ctx.fillStyle = active ? hexToRgba(color, 0.9) : hexToRgba(color, 0.4);
        
        // Slightly wobbly rect
        ctx.beginPath();
        ctx.moveTo(jitter(x, 1), jitter(y, 1));
        ctx.lineTo(jitter(x + widthPx, 1), jitter(y, 1));
        ctx.lineTo(jitter(x + widthPx, 1), jitter(y + h, 1));
        ctx.lineTo(jitter(x, 1), jitter(y + h, 1));
        ctx.closePath();
        ctx.fill();
        
        ctx.strokeStyle = active ? '#ffffff' : hexToRgba(color, 0.7);
        ctx.lineWidth = active ? 2.5 : 1.5;
        ctx.stroke();
      }
    }

    // Hand-drawn playhead
    ctx.strokeStyle = chalkWhite;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(jitter(playheadX, 1), timelineTop);
    ctx.lineTo(jitter(playheadX, 2), timelineTop + timelineHeight * 0.5);
    ctx.lineTo(jitter(playheadX, 1), timelineBottom);
    ctx.stroke();

    ctx.fillStyle = chalkWhite;
    ctx.beginPath();
    ctx.arc(playheadX, timelineTop - 5, 5, 0, Math.PI * 2);
    ctx.fill();

    // Chalk border
    ctx.strokeStyle = chalkDim;
    ctx.lineWidth = 4;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = chalkWhite;
      ctx.font = `600 ${clamp(Math.round(width * 0.02), 14, 22)}px "Comic Sans MS", "Chalkboard SE", cursive`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
