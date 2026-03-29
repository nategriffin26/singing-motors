import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

export const sunsetTheme: ViewerTheme = {
  id: 'sunset',
  label: 'Sunset',
  summary: 'Warm, atmospheric, and nostalgic twilight vibes.',
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

    // Sunset gradient
    const bg = ctx.createLinearGradient(0, 0, 0, height);
    bg.addColorStop(0, '#2d1b4e'); // Deep purple
    bg.addColorStop(0.5, '#8c2f5d'); // Magenta
    bg.addColorStop(1, '#e37346'); // Burnt orange
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? 'rgba(255, 200, 150, 0.3)' : 'rgba(255, 255, 255, 0.05)';
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
      
      ctx.fillStyle = black ? 'rgba(40, 20, 60, 0.8)' : 'rgba(90, 40, 90, 0.6)';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);

      if (activeColor) {
        ctx.fillStyle = 'rgba(255, 180, 100, 0.4)'; // Golden glow
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight + 0.5);
        ctx.fillStyle = '#ffcc66'; // Golden edge
        ctx.fillRect(left + geometry.pianoWidth - 3, y, 3, noteHeight + 0.5);
      }

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#ffffff' : '#ffb380';
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
        ctx.strokeStyle = major ? 'rgba(255, 150, 100, 0.3)' : 'rgba(255, 255, 255, 0.1)';
        ctx.lineWidth = major ? 1.5 : 0.8;
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
        
        // Warm up the colors for the sunset vibe
        const active = activeBarIds.has(Math.round(bar.id));
        const color = resolveBarColor(bar, scene.colorMode, { active });

        ctx.fillStyle = active ? color : hexToRgba(color, 0.4);
        ctx.fillRect(x, y, widthPx, h);
        
        ctx.strokeStyle = active ? '#ffffff' : hexToRgba(color, 0.8);
        ctx.lineWidth = active ? 1.5 : 1;
        ctx.strokeRect(x, y, widthPx, h);
      }
    }

    // Playhead glowing sunbeam
    const phGrad = ctx.createLinearGradient(playheadX - 10, 0, playheadX + 10, 0);
    phGrad.addColorStop(0, 'rgba(255, 230, 150, 0)');
    phGrad.addColorStop(0.5, 'rgba(255, 230, 150, 0.8)');
    phGrad.addColorStop(1, 'rgba(255, 230, 150, 0)');
    
    ctx.fillStyle = phGrad;
    ctx.fillRect(playheadX - 10, timelineTop, 20, timelineHeight);

    ctx.strokeStyle = '#ffe696';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();

    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(playheadX, timelineTop - 6, 6, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = 'rgba(255, 200, 150, 0.3)';
    ctx.lineWidth = 1;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = 'rgba(255, 200, 150, 0.8)';
      ctx.font = `italic 600 ${clamp(Math.round(width * 0.02), 13, 20)}px serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
