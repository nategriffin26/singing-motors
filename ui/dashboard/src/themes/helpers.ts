import {
  ColorModeId,
  ViewerSession,
  ViewerTimelineBar,
  ViewerTimelineFrameV1,
  ViewerTimelineV1,
} from '../types';

export const CHANNEL_COLORS = [
  '#00f5ff',
  '#ff4f9e',
  '#ffd166',
  '#8bff7a',
  '#52a8ff',
  '#ff9e4f',
  '#d197ff',
  '#4dffd7',
  '#ff7ab8',
  '#f5ff6f',
  '#7dd3fc',
  '#22d3a2',
  '#fb7185',
  '#f59e0b',
  '#a78bfa',
  '#67e8f9',
] as const;

export const COLOR_MODE_IDS = [
  'monochrome_accent',
  'channel',
  'octave_bands',
  'frequency_bands',
  'motor_slot',
  'velocity_intensity',
] as const satisfies readonly ColorModeId[];

const MONOCHROME_COLORS = {
  active: '#59b8dc',
  inactive: '#748290',
} as const;
const MIN_VISIBLE_BAR_WIDTH_PX = 2.25;

const OCTAVE_BAND_COLORS = ['#3b82f6', '#06b6d4', '#22c55e', '#eab308', '#f97316', '#ef4444'] as const;
const FREQUENCY_BAND_COLORS = ['#2563eb', '#0ea5e9', '#14b8a6', '#84cc16', '#f59e0b', '#ef4444'] as const;
const MOTOR_SLOT_COLORS = ['#3a86ff', '#ff7f11', '#2fbf71', '#e63946', '#9b5de5', '#f4d35e', '#00b8d9', '#ff4fa3'] as const;

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'] as const;

export interface CanvasViewport {
  cssWidth: number;
  cssHeight: number;
  dpr: number;
}

export interface SceneGeometry {
  width: number;
  height: number;
  dpr: number;
  top: number;
  bottom: number;
  left: number;
  right: number;
  pianoWidth: number;
  timelineLeft: number;
  timelineRight: number;
  timelineTop: number;
  timelineBottom: number;
  timelineWidth: number;
  timelineHeight: number;
  historyUs: number;
  lookaheadUs: number;
  totalWindowUs: number;
  playheadX: number;
  pxPerUs: number;
  noteCount: number;
  noteHeight: number;
}

export interface SceneModel {
  session: ViewerSession | null;
  timeline: ViewerTimelineV1 | null;
  frame: ViewerTimelineFrameV1 | null;
  colorMode: ColorModeId;
  smoothPlayheadUs: number;
  barsById: Map<number, ViewerTimelineBar>;
  visibleBars: ViewerTimelineBar[];
  activeBarIds: Set<number>;
  activePitchColors: Map<number, string>;
  noteRange: { min: number; max: number };
  geometry: SceneGeometry;
  idleHint: string | null;
}

export interface BuildSceneModelParams {
  session: ViewerSession | null;
  timeline: ViewerTimelineV1 | null;
  frame: ViewerTimelineFrameV1 | null;
  colorMode: ColorModeId;
  smoothPlayheadUs: number;
  barsById: Map<number, ViewerTimelineBar>;
  viewport: CanvasViewport;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function hexToRgba(hex: string, alpha: number): string {
  const normalized = hex.replace('#', '');
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${clamp(alpha, 0, 1)})`;
}

export function isBlackKey(note: number): boolean {
  const mod = ((note % 12) + 12) % 12;
  return mod === 1 || mod === 3 || mod === 6 || mod === 8 || mod === 10;
}

export function midiLabel(note: number): string {
  const name = NOTE_NAMES[((note % 12) + 12) % 12];
  const octave = Math.floor(note / 12) - 1;
  return `${name}${octave}`;
}

export function listColorModeIds(): ColorModeId[] {
  return [...COLOR_MODE_IDS];
}

function pitchToFrequencyHz(pitch: number): number {
  const clamped = clamp(Math.round(pitch), 0, 127);
  return 440.0 * Math.pow(2.0, (clamped - 69) / 12.0);
}

function hslToHex(h: number, sPct: number, lPct: number): string {
  const hNorm = ((h % 360) + 360) % 360;
  const s = clamp(sPct, 0, 100) / 100;
  const l = clamp(lPct, 0, 100) / 100;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = hNorm / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let r1 = 0;
  let g1 = 0;
  let b1 = 0;
  if (hp >= 0 && hp < 1) {
    r1 = c;
    g1 = x;
  } else if (hp < 2) {
    r1 = x;
    g1 = c;
  } else if (hp < 3) {
    g1 = c;
    b1 = x;
  } else if (hp < 4) {
    g1 = x;
    b1 = c;
  } else if (hp < 5) {
    r1 = x;
    b1 = c;
  } else {
    r1 = c;
    b1 = x;
  }
  const m = l - c / 2;
  const r = Math.round((r1 + m) * 255);
  const g = Math.round((g1 + m) * 255);
  const b = Math.round((b1 + m) * 255);
  const toHex = (value: number): string => value.toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function octaveBandColor(pitch: number): string {
  const octave = Math.floor(clamp(Math.round(pitch), 0, 127) / 12) - 1;
  let idx = 0;
  if (octave <= 2) {
    idx = 0;
  } else if (octave <= 3) {
    idx = 1;
  } else if (octave <= 4) {
    idx = 2;
  } else if (octave <= 5) {
    idx = 3;
  } else if (octave <= 6) {
    idx = 4;
  } else {
    idx = 5;
  }
  return OCTAVE_BAND_COLORS[idx];
}

function frequencyBandColor(freqHz: number): string {
  const minHz = 30;
  const maxHz = 650;
  const f = clamp(freqHz, minHz, maxHz);
  const t = (Math.log(f) - Math.log(minHz)) / (Math.log(maxHz) - Math.log(minHz));
  const idx = clamp(Math.floor(t * FREQUENCY_BAND_COLORS.length), 0, FREQUENCY_BAND_COLORS.length - 1);
  return FREQUENCY_BAND_COLORS[idx];
}

function velocityIntensityColor(velocity: number, active: boolean): string {
  const velocityNorm = clamp(velocity / 127, 0, 1);
  const saturation = 24 + velocityNorm * 40;
  const lightness = (active ? 44 : 34) + velocityNorm * 22;
  return hslToHex(198, saturation, lightness);
}

export function resolveBarColor(
  bar: ViewerTimelineBar,
  colorMode: ColorModeId,
  options: { active: boolean },
): string {
  const { active } = options;
  if (colorMode === 'monochrome_accent') {
    return active ? MONOCHROME_COLORS.active : MONOCHROME_COLORS.inactive;
  }
  if (colorMode === 'channel') {
    return CHANNEL_COLORS[Math.abs(Math.round(bar.channel)) % CHANNEL_COLORS.length];
  }
  if (colorMode === 'octave_bands') {
    return octaveBandColor(bar.pitch);
  }
  if (colorMode === 'frequency_bands') {
    const frequencyHz = Number.isFinite(bar.frequency_hz) && (bar.frequency_hz ?? 0) > 0
      ? Number(bar.frequency_hz)
      : pitchToFrequencyHz(bar.pitch);
    return frequencyBandColor(frequencyHz);
  }
  if (colorMode === 'motor_slot') {
    return MOTOR_SLOT_COLORS[Math.abs(Math.round(bar.motor_slot)) % MOTOR_SLOT_COLORS.length];
  }
  return velocityIntensityColor(bar.velocity, active);
}

export function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const r = clamp(radius, 0, Math.min(width, height) * 0.5);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function resolveNoteRange(
  session: ViewerSession | null,
  timeline: ViewerTimelineV1 | null,
): { min: number; max: number } {
  if (session) {
    const min = clamp(Math.round(session.note_range.min_note), 0, 127);
    const max = clamp(Math.round(session.note_range.max_note), 0, 127);
    if (max > min) {
      return { min, max };
    }
  }

  if (timeline) {
    const min = clamp(Math.round(timeline.note_range.min_note), 0, 127);
    const max = clamp(Math.round(timeline.note_range.max_note), 0, 127);
    if (max > min) {
      return { min, max };
    }

    if (timeline.bars_static.length > 0) {
      let minPitch = 127;
      let maxPitch = 0;
      for (const bar of timeline.bars_static) {
        const pitch = clamp(Math.round(bar.pitch), 0, 127);
        if (pitch < minPitch) {
          minPitch = pitch;
        }
        if (pitch > maxPitch) {
          maxPitch = pitch;
        }
      }
      if (maxPitch > minPitch) {
        return { min: minPitch, max: maxPitch };
      }
    }
  }

  return { min: 36, max: 96 };
}

function isRenderableTimelineBar(bar: ViewerTimelineBar, pxPerUs: number): boolean {
  const startUs = Math.round(bar.start_us);
  const endUs = Math.round(bar.end_us);
  const durationUs = Math.max(0, endUs - startUs);
  if (durationUs <= 0) {
    return false;
  }
  return durationUs * pxPerUs >= MIN_VISIBLE_BAR_WIDTH_PX;
}

export function buildSceneModel({
  session,
  timeline,
  frame,
  colorMode,
  smoothPlayheadUs,
  barsById,
  viewport,
}: BuildSceneModelParams): SceneModel {
  const width = viewport.cssWidth;
  const height = viewport.cssHeight;
  const dpr = viewport.dpr;

  const basePadding = clamp(Math.round(width * 0.02), 8, 28);
  const top = basePadding;
  const bottom = height - basePadding;
  const left = basePadding;
  const right = width - basePadding;

  const pianoWidth = clamp(Math.round(width * 0.11), 72, 120);
  const timelineLeft = left + pianoWidth;
  const timelineRight = right;
  const timelineTop = top;
  const timelineBottom = bottom;
  const timelineWidth = Math.max(1, timelineRight - timelineLeft);
  const timelineHeight = Math.max(1, timelineBottom - timelineTop);

  let historyUs = 350_000;
  let lookaheadUs = 3_000_000;
  if (session && session.window) {
    historyUs = Math.max(1, session.window.history_us);
    lookaheadUs = Math.max(1, session.window.lookahead_us);
  }

  const totalWindowUs = historyUs + lookaheadUs;
  const playheadX = timelineLeft + (historyUs / totalWindowUs) * timelineWidth;

  const noteRange = resolveNoteRange(session, timeline);
  const noteCount = Math.max(1, noteRange.max - noteRange.min + 1);
  const noteHeight = timelineHeight / noteCount;
  const pxPerUs = timelineWidth / totalWindowUs;

  const visibleBars: ViewerTimelineBar[] = [];
  const activeBarIds = new Set<number>();
  if (frame) {
    for (const noteId of frame.active_note_ids) {
      activeBarIds.add(Math.round(noteId));
    }
    for (const barId of frame.visible_bar_ids) {
      const bar = barsById.get(Math.round(barId));
      if (bar && isRenderableTimelineBar(bar, pxPerUs)) {
        visibleBars.push(bar);
      }
    }
  }

  const activePitchColors = new Map<number, string>();
  for (const bar of visibleBars) {
    if (activeBarIds.has(Math.round(bar.id))) {
      const p = clamp(Math.round(bar.pitch), 0, 127);
      const c = resolveBarColor(bar, colorMode, { active: true });
      activePitchColors.set(p, c);
    }
  }
  if (activePitchColors.size === 0 && frame) {
    for (const activeNoteId of frame.active_note_ids) {
      const activeBar = barsById.get(Math.round(activeNoteId));
      if (!activeBar || !isRenderableTimelineBar(activeBar, pxPerUs)) continue;
      const p = clamp(Math.round(activeBar.pitch), 0, 127);
      const c = resolveBarColor(activeBar, colorMode, { active: true });
      activePitchColors.set(p, c);
    }
  }

  const geometry: SceneGeometry = {
    width,
    height,
    dpr,
    top,
    bottom,
    left,
    right,
    pianoWidth,
    timelineLeft,
    timelineRight,
    timelineTop,
    timelineBottom,
    timelineWidth,
    timelineHeight,
    historyUs,
    lookaheadUs,
    totalWindowUs,
    playheadX,
    pxPerUs,
    noteCount,
    noteHeight,
  };

  return {
    session,
    timeline,
    frame,
    colorMode,
    smoothPlayheadUs,
    barsById,
    visibleBars,
    activeBarIds,
    activePitchColors,
    noteRange,
    geometry,
    idleHint: frame
      ? null
      : timeline
        ? 'Timeline has no playable frames.'
        : 'Loading precomputed timeline...',
  };
}
