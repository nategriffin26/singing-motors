import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

export const blueprintTheme: ViewerTheme = {
  id: 'blueprint',
  label: 'Blueprint',
  summary: 'Architectural or engineering draft.',
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

    // Solid blueprint blue background
    const blueprintBlue = '#14386b';
    const paperGrid = 'rgba(255, 255, 255, 0.05)';
    const whiteLines = 'rgba(255, 255, 255, 0.8)';
    const softLines = 'rgba(255, 255, 255, 0.3)';

    ctx.fillStyle = blueprintBlue;
    ctx.fillRect(0, 0, width, height);

    // Grid overlay
    ctx.strokeStyle = paperGrid;
    ctx.lineWidth = 1;
    for (let x = 0; x < width; x += 20) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
    }
    for (let y = 0; y < height; y += 20) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
    }

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(15, 45, 90, 0.6)'; // slightly darker blue
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? whiteLines : softLines;
      ctx.lineWidth = note % 12 === 0 ? 1 : 0.5;
      ctx.beginPath();
      ctx.moveTo(timelineLeft, y);
      ctx.lineTo(timelineRight, y);
      ctx.stroke();
    }

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);
      const activeColor = activePitchColors.get(note);
      
      ctx.fillStyle = black ? '#102c54' : '#14386b';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);
      
      // Crisp outline for piano keys
      ctx.strokeStyle = softLines;
      ctx.lineWidth = 1;
      ctx.strokeRect(left, y, geometry.pianoWidth, noteHeight);

      if (activeColor) {
        ctx.fillStyle = softLines; 
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);
        ctx.fillStyle = whiteLines;
        ctx.fillRect(left + geometry.pianoWidth - 2, y, 2, noteHeight);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#ffffff' : whiteLines;
        ctx.font = `${clamp(Math.round(noteHeight * 0.6), 10, 12)}px monospace`;
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
        ctx.strokeStyle = major ? whiteLines : softLines;
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

        // Outline only with white or hatched fill for blueprint
        ctx.strokeStyle = active ? '#ffffff' : hexToRgba(color, 0.65);
        ctx.lineWidth = active ? 2 : 1;
        ctx.strokeRect(x, y, widthPx, h);

        if (active) {
          // Add a tinted hatch/fill while keeping blueprint contrast.
          ctx.fillStyle = hexToRgba(color, 0.24);
          ctx.fillRect(x, y, widthPx, h);
          
          // Corner ticks
          ctx.beginPath();
          ctx.moveTo(x - 2, y); ctx.lineTo(x + 2, y);
          ctx.moveTo(x, y - 2); ctx.lineTo(x, y + 2);
          ctx.stroke();
        }
      }
    }

    // Playhead line as a red/orange measurement line
    const measureLine = '#ff6b6b';
    ctx.strokeStyle = measureLine;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop - 10);
    ctx.lineTo(playheadX, timelineBottom + 10);
    ctx.stroke();
    
    // Playhead arrow/marker
    ctx.fillStyle = measureLine;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX - 6, timelineTop - 8);
    ctx.lineTo(playheadX + 6, timelineTop - 8);
    ctx.closePath();
    ctx.fill();

    // Border
    ctx.strokeStyle = whiteLines;
    ctx.lineWidth = 2;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);
    
    // Little measurement markings on the border
    ctx.beginPath();
    ctx.moveTo(left - 5, timelineTop); ctx.lineTo(left + 5, timelineTop);
    ctx.moveTo(left - 5, timelineBottom); ctx.lineTo(left + 5, timelineBottom);
    ctx.stroke();

    if (scene.idleHint) {
      ctx.fillStyle = whiteLines;
      ctx.font = `bold ${clamp(Math.round(width * 0.02), 13, 20)}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(`[ ${scene.idleHint.toUpperCase()} ]`, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
