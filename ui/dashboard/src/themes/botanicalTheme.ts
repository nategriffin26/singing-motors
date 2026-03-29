import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

export const botanicalTheme: ViewerTheme = {
  id: 'botanical',
  label: 'Botanical',
  summary: 'Earthy greens, natural tones, and organic curves.',
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

    // Earthy moss green background
    ctx.fillStyle = '#ebf0e6';
    ctx.fillRect(0, 0, width, height);

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(92, 112, 85, 0.1)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? 'rgba(100, 120, 90, 0.3)' : 'rgba(100, 120, 90, 0.1)';
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
      
      // Warm ivory and soft brown for keys
      ctx.fillStyle = black ? '#d2d9cc' : '#fdfdfa';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);

      if (activeColor) {
        // Desaturate and warm up active key colors slightly
        ctx.fillStyle = hexToRgba(activeColor, 0.3);
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);
        ctx.fillStyle = '#6e8561'; // Deep sage green edge
        ctx.fillRect(left + geometry.pianoWidth - 4, y, 4, noteHeight + 0.5);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#3d4d35' : '#819675';
        ctx.font = `italic ${clamp(Math.round(noteHeight * 0.65), 10, 13)}px Georgia, serif`;
        ctx.textBaseline = 'middle';
        drawUprightText(midiLabel(note), left + 7, y + noteHeight * 0.52);
      }
    }

    // Helper for rounded leaf-like ends
    const drawLeafBar = (x: number, y: number, w: number, h: number) => {
        const radius = Math.min(h / 2, w / 2);
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.lineTo(x + w - radius, y);
        ctx.quadraticCurveTo(x + w, y, x + w, y + h / 2); // Leaf tip right
        ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
        ctx.lineTo(x + radius, y + h);
        ctx.quadraticCurveTo(x, y + h, x, y + h / 2); // Leaf tip left
        ctx.quadraticCurveTo(x, y, x + radius, y);
        ctx.closePath();
    };

    if (frame) {
      for (const beatUs of frame.beat_markers_us) {
        const x = playheadX + (beatUs - smoothPlayheadUs) * pxPerUs;
        if (x < timelineLeft - 1 || x > timelineRight + 1) {
          continue;
        }
        const major = Math.round(beatUs / 250_000) % 4 === 0;
        ctx.strokeStyle = major ? 'rgba(120, 140, 110, 0.35)' : 'rgba(120, 140, 110, 0.15)';
        ctx.lineWidth = major ? 1.5 : 1;
        ctx.beginPath();
        ctx.moveTo(x, timelineTop);
        ctx.lineTo(x, timelineBottom);
        ctx.stroke();
      }

      const pad = Math.max(1, noteHeight * 0.2); // slightly more padding for leaf shape
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

        drawLeafBar(x, y, widthPx, h);

        if (active) {
            ctx.fillStyle = hexToRgba(color, 0.85);
            ctx.fill();
            ctx.strokeStyle = '#3d4d35'; // Dark sage outline
            ctx.lineWidth = 1.5;
            ctx.stroke();
        } else {
            ctx.fillStyle = hexToRgba(color, 0.4);
            ctx.fill();
            ctx.strokeStyle = hexToRgba(color, 0.6);
            ctx.lineWidth = 1;
            ctx.stroke();
        }
      }
    }

    // Playhead vine
    ctx.strokeStyle = '#6e8561';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();

    // Little leaf on playhead
    ctx.fillStyle = '#6e8561';
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop - 8);
    ctx.quadraticCurveTo(playheadX + 6, timelineTop - 8, playheadX + 8, timelineTop - 2);
    ctx.quadraticCurveTo(playheadX + 2, timelineTop, playheadX, timelineTop);
    ctx.fill();

    ctx.strokeStyle = 'rgba(140, 160, 130, 0.3)';
    ctx.lineWidth = 1;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = '#5c7055';
      ctx.font = `italic 600 ${clamp(Math.round(width * 0.02), 14, 22)}px Georgia, serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
