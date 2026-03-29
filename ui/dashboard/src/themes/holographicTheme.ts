import { ThemeDrawParams, ViewerTheme } from './themeTypes';
import {
  clamp,
  hexToRgba,
  isBlackKey,
  midiLabel,
  resolveBarColor,
} from './helpers';

export const holographicTheme: ViewerTheme = {
  id: 'holographic',
  label: 'Holographic',
  summary: 'Translucent, iridescent cyber-glass aesthetic.',
  dprCap: 2.0,
  createRuntimeState: () => ({ pulse: 0 }),
  draw: ({ ctx, viewport, scene, runtime: runtimeBag, drawUprightText }: ThemeDrawParams) => {
    const runtime = runtimeBag as { pulse: number };
    if (typeof runtime.pulse !== 'number') runtime.pulse = 0;

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

    // Iridescent background gradient
    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, '#e0f7fa');   // light cyan
    bg.addColorStop(0.33, '#f3e5f5'); // light magenta
    bg.addColorStop(0.66, '#e8eaf6'); // light purple
    bg.addColorStop(1, '#ffffff');    // white
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    runtime.pulse = (runtime.pulse + 0.02) % (Math.PI * 2);

    for (let note = noteRange.min; note <= noteRange.max; note += 1) {
      const y = timelineTop + (noteRange.max - note) * noteHeight;
      const black = isBlackKey(note);

      if (black) {
        ctx.fillStyle = 'rgba(200, 210, 230, 0.4)';
        ctx.fillRect(timelineLeft, y, timelineWidth, noteHeight);
      }

      ctx.strokeStyle = note % 12 === 0 ? 'rgba(150, 180, 220, 0.6)' : 'rgba(200, 210, 230, 0.4)';
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
      
      ctx.fillStyle = black ? 'rgba(210, 220, 240, 0.8)' : 'rgba(255, 255, 255, 0.8)';
      ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);

      if (activeColor) {
        // Holographic sheen
        const shimmer = ctx.createLinearGradient(left, y, left + geometry.pianoWidth, y + noteHeight);
        shimmer.addColorStop(0, hexToRgba(activeColor, 0.4));
        shimmer.addColorStop(0.5, 'rgba(255, 255, 255, 0.8)');
        shimmer.addColorStop(1, hexToRgba(activeColor, 0.6));
        
        ctx.fillStyle = shimmer;
        ctx.fillRect(left, y, geometry.pianoWidth, noteHeight);
        
        ctx.fillStyle = activeColor;
        ctx.fillRect(left + geometry.pianoWidth - 3, y, 3, noteHeight);
      }

      ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
      ctx.strokeRect(left, y, geometry.pianoWidth, noteHeight);

      if (note % 12 === 0 && noteHeight >= 6) {
        ctx.fillStyle = activeColor ? '#ffffff' : '#607d8b';
        ctx.font = `500 ${clamp(Math.round(noteHeight * 0.65), 10, 13)}px sans-serif`;
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
        ctx.strokeStyle = major ? 'rgba(100, 180, 255, 0.4)' : 'rgba(150, 200, 255, 0.2)';
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

        if (active) {
            // Iridescent glass feel for active notes
            const activeGrad = ctx.createLinearGradient(x, y, x, y + h);
            activeGrad.addColorStop(0, hexToRgba(color, 0.8));
            activeGrad.addColorStop(0.3, 'rgba(255, 255, 255, 0.6)');
            activeGrad.addColorStop(1, hexToRgba(color, 0.9));
            ctx.fillStyle = activeGrad;
            
            ctx.shadowColor = color;
            ctx.shadowBlur = 10;
        } else {
            ctx.fillStyle = hexToRgba(color, 0.3);
        }
        
        ctx.fillRect(x, y, widthPx, h);
        
        ctx.strokeStyle = active ? '#ffffff' : hexToRgba(color, 0.5);
        ctx.lineWidth = active ? 2 : 1;
        ctx.strokeRect(x, y, widthPx, h);
        ctx.shadowBlur = 0; // reset
      }
    }

    // Holo playhead line
    const playheadColor = '#00e5ff';
    ctx.shadowColor = playheadColor;
    ctx.shadowBlur = 12;
    ctx.strokeStyle = playheadColor;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop);
    ctx.lineTo(playheadX, timelineBottom);
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Diamond shaped playhead marker
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.moveTo(playheadX, timelineTop - 10);
    ctx.lineTo(playheadX + 5, timelineTop - 5);
    ctx.lineTo(playheadX, timelineTop);
    ctx.lineTo(playheadX - 5, timelineTop - 5);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = 'rgba(150, 180, 220, 0.4)';
    ctx.lineWidth = 1;
    ctx.strokeRect(left, timelineTop, right - left, timelineHeight);

    if (scene.idleHint) {
      ctx.fillStyle = 'rgba(100, 150, 200, 0.8)';
      ctx.font = `600 ${clamp(Math.round(width * 0.02), 14, 22)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawUprightText(scene.idleHint, timelineLeft + timelineWidth * 0.5, timelineTop + timelineHeight * 0.5);
      ctx.textAlign = 'left';
    }
  },
};
