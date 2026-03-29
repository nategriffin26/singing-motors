import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

export const terminalTheme: ViewerTheme = {
  id: 'terminal',
  label: 'Terminal',
  summary: 'Classic hacker / vintage CRT monitor wireframe.',
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

    // Pure black background
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, width, height);

    const greenBase = '#00ff00';
    const greenDim = 'rgba(0, 255, 0, 0.2)';
    const greenFaint = 'rgba(0, 255, 0, 0.08)';

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(0, 30, 0, 0.4)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? greenDim : greenFaint;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(timelineLeft, y);
      ctx.lineTo(timelineRight, y);
      ctx.stroke();
    }

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const activeColor = activePitchColors.get(note);
      
      ctx.fillStyle = '#000000';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);
      
      // Wireframe piano keys
      ctx.strokeStyle = greenDim;
      ctx.lineWidth = 1;
      ctx.strokeRect(left, y, geometry.pianoWidth, noteHeight);

      if (activeColor) {
        ctx.fillStyle = hexToRgba(activeColor, 0.28);
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);
        ctx.fillStyle = activeColor;
        ctx.fillRect(left + geometry.pianoWidth - 4, y, 4, noteHeight);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = greenBase;
        ctx.font = `${clamp(Math.round(noteHeight * 0.7), 10, 14)}px monospace`;
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
        ctx.strokeStyle = major ? greenDim : greenFaint;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, timelineTop);
        ctx.lineTo(x, timelineBottom);
        ctx.stroke();
      }

      const pad = Math.max(1, noteHeight * 0.1);
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

        // Wireframe notes
        ctx.fillStyle = '#000000';
        ctx.fillRect(x, y, widthPx, h);
        
        ctx.strokeStyle = active ? color : hexToRgba(color, 0.72);
        ctx.lineWidth = active ? 2 : 1;
        ctx.strokeRect(x, y, widthPx, h);

        if (active) {
            // Fill with a faint colorized scanline effect.
            ctx.fillStyle = hexToRgba(color, 0.24);
            ctx.fillRect(x, y, widthPx, h);
        }
      }
    }

    // Playhead
    ctx.strokeStyle = greenBase;
    ctx.lineWidth = 1;
    // Dashed playhead
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();
    ctx.setLineDash([]); // reset

    ctx.fillStyle = greenBase;
    ctx.fillRect(playheadX - 4, timelineTop - 8, 8, 8);

    ctx.strokeStyle = greenBase;
    ctx.lineWidth = 2;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = greenBase;
      ctx.font = `bold ${clamp(Math.round(width * 0.02), 13, 20)}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(`> ${scene.idleHint}_`, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
