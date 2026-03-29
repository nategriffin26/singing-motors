export type ConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'disconnected';

export type ThemeId = 'neon' | 'retro' | 'minimal' | 'oceanic' | 'terminal' | 'sunset' | 'chalkboard' | 'blueprint' | 'holographic' | 'botanical';
export type ColorModeId =
  | 'monochrome_accent'
  | 'channel'
  | 'octave_bands'
  | 'frequency_bands'
  | 'motor_slot'
  | 'velocity_intensity';

export interface HealthResponse {
  status: string;
}

export interface ViewerSong {
  file_name: string;
  duration_us: number;
  duration_s: number;
  note_count: number;
  max_polyphony: number;
  transpose_semitones: number;
}

export interface ViewerNoteRange {
  min_note: number;
  max_note: number;
}

export interface ViewerWindow {
  history_us: number;
  lookahead_us: number;
}

export interface ViewerAllocation {
  policy: string;
  stolen_notes: number;
  dropped_notes: number;
  playable_notes: number;
  retained_ratio: number;
}

export interface ViewerSession {
  song: ViewerSong;
  program: {
    mode_id: string;
    display_name: string;
    section_count: number;
    total_duration_us: number;
    sections: Array<Record<string, unknown>>;
  };
  note_range: ViewerNoteRange;
  connected_motors: number;
  lanes: number;
  allocation: ViewerAllocation;
  window: ViewerWindow;
  render_mode?: 'live' | 'prerender_30fps';
  fps?: number;
  timeline_version?: string;
  timeline_ready?: boolean;
  timeline_url?: string;
  theme_default?: ThemeId;
  themes_available?: ThemeId[];
  color_mode_default?: ColorModeId;
  color_modes_available?: ColorModeId[];
  show_controls?: boolean;
  sync_offset_ms?: number;
  sync_strategy?: 'scheduled_start_v1' | 'legacy_poll_v1';
  scheduled_start_unix_ms?: number | null;
  scheduled_start_device_us?: number | null;
  drift_rebase_threshold_ms?: number;
  generated_at_unix_ms: number;
}

export interface ViewerBar {
  id: number;
  pitch: number;
  start_us: number;
  end_us: number;
  velocity: number;
  frequency_hz?: number;
  channel: number;
  motor_slot: number;
  active: boolean;
}

export interface ViewerTimelineBar {
  id: number;
  pitch: number;
  start_us: number;
  end_us: number;
  velocity: number;
  frequency_hz?: number;
  channel: number;
  motor_slot: number;
}

export interface ViewerTimelineFrameV1 {
  playhead_us: number;
  window_start_us: number;
  window_end_us: number;
  active_note_ids: number[];
  visible_bar_ids: number[];
  beat_markers_us: number[];
}

export interface ViewerTimelineV1 {
  version: 'v1';
  fps: number;
  duration_us: number;
  frame_count: number;
  note_range: ViewerNoteRange;
  bars_static: ViewerTimelineBar[];
  frames: ViewerTimelineFrameV1[];
  style_hints: Record<string, unknown>;
  generated_at_unix_ms: number;
}

export interface ViewerFrameState {
  playing: boolean;
  stream_open: boolean;
  stream_end_received: boolean;
}

export interface ViewerFrameMessage {
  type: 'frame';
  seq: number;
  playhead_us: number;
  window_start_us: number;
  window_end_us: number;
  duration_us: number;
  bars: ViewerBar[];
  active_note_ids: number[];
  beat_markers_us: number[];
  state: ViewerFrameState;
}

export interface ViewerHelloMessage {
  type: 'hello';
  protocol?: string;
  server_time_unix_ms?: number;
  [key: string]: unknown;
}

export interface ViewerHeartbeatMessage {
  type: 'heartbeat';
  unix_ms?: number;
  [key: string]: unknown;
}

export type ViewerSocketMessage =
  | ViewerHelloMessage
  | ViewerHeartbeatMessage
  | ViewerFrameMessage;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isString(value: unknown): value is string {
  return typeof value === 'string';
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === 'boolean';
}

export function isThemeId(value: unknown): value is ThemeId {
  return value === 'neon' || value === 'retro' || value === 'minimal' || value === 'oceanic' || value === 'terminal' || value === 'sunset' || value === 'chalkboard' || value === 'blueprint' || value === 'holographic' || value === 'botanical';
}

export function isColorModeId(value: unknown): value is ColorModeId {
  return (
    value === 'monochrome_accent' ||
    value === 'channel' ||
    value === 'octave_bands' ||
    value === 'frequency_bands' ||
    value === 'motor_slot' ||
    value === 'velocity_intensity'
  );
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every(isFiniteNumber);
}

function isViewerBar(value: unknown): value is ViewerBar {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isFiniteNumber(value.id) &&
    isFiniteNumber(value.pitch) &&
    isFiniteNumber(value.start_us) &&
    isFiniteNumber(value.end_us) &&
    isFiniteNumber(value.velocity) &&
    (value.frequency_hz === undefined || isFiniteNumber(value.frequency_hz)) &&
    isFiniteNumber(value.channel) &&
    isFiniteNumber(value.motor_slot) &&
    isBoolean(value.active)
  );
}

function isViewerTimelineBar(value: unknown): value is ViewerTimelineBar {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isFiniteNumber(value.id) &&
    isFiniteNumber(value.pitch) &&
    isFiniteNumber(value.start_us) &&
    isFiniteNumber(value.end_us) &&
    isFiniteNumber(value.velocity) &&
    (value.frequency_hz === undefined || isFiniteNumber(value.frequency_hz)) &&
    isFiniteNumber(value.channel) &&
    isFiniteNumber(value.motor_slot)
  );
}

function isViewerTimelineFrameV1(value: unknown): value is ViewerTimelineFrameV1 {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isFiniteNumber(value.playhead_us) &&
    isFiniteNumber(value.window_start_us) &&
    isFiniteNumber(value.window_end_us) &&
    isNumberArray(value.active_note_ids) &&
    isNumberArray(value.visible_bar_ids) &&
    isNumberArray(value.beat_markers_us)
  );
}

function isViewerFrameState(value: unknown): value is ViewerFrameState {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isBoolean(value.playing) &&
    isBoolean(value.stream_open) &&
    isBoolean(value.stream_end_received)
  );
}

export function isViewerSession(value: unknown): value is ViewerSession {
  if (!isRecord(value)) {
    return false;
  }

  if (!isRecord(value.song) || !isRecord(value.program) || !isRecord(value.note_range) || !isRecord(value.window)) {
    return false;
  }
  if (!isRecord(value.allocation)) {
    return false;
  }

  const themeDefaultValid = value.theme_default === undefined || isThemeId(value.theme_default);
  const themesAvailableValid =
    value.themes_available === undefined ||
    (Array.isArray(value.themes_available) && value.themes_available.every(isThemeId));
  const colorModeDefaultValid = value.color_mode_default === undefined || isColorModeId(value.color_mode_default);
  const colorModesAvailableValid =
    value.color_modes_available === undefined ||
    (Array.isArray(value.color_modes_available) && value.color_modes_available.every(isColorModeId));
  const showControlsValid = value.show_controls === undefined || isBoolean(value.show_controls);
  const syncOffsetValid = value.sync_offset_ms === undefined || isFiniteNumber(value.sync_offset_ms);
  const syncStrategyValid =
    value.sync_strategy === undefined ||
    value.sync_strategy === 'scheduled_start_v1' ||
    value.sync_strategy === 'legacy_poll_v1';
  const scheduledStartUnixValid =
    value.scheduled_start_unix_ms === undefined ||
    value.scheduled_start_unix_ms === null ||
    isFiniteNumber(value.scheduled_start_unix_ms);
  const scheduledStartDeviceValid =
    value.scheduled_start_device_us === undefined ||
    value.scheduled_start_device_us === null ||
    isFiniteNumber(value.scheduled_start_device_us);
  const driftRebaseThresholdValid =
    value.drift_rebase_threshold_ms === undefined || isFiniteNumber(value.drift_rebase_threshold_ms);

  return (
    isString(value.song.file_name) &&
    isFiniteNumber(value.song.duration_us) &&
    isFiniteNumber(value.song.duration_s) &&
    isFiniteNumber(value.song.note_count) &&
    isFiniteNumber(value.song.max_polyphony) &&
    isFiniteNumber(value.song.transpose_semitones) &&
    isString(value.program.mode_id) &&
    isString(value.program.display_name) &&
    isFiniteNumber(value.program.section_count) &&
    isFiniteNumber(value.program.total_duration_us) &&
    Array.isArray(value.program.sections) &&
    isFiniteNumber(value.note_range.min_note) &&
    isFiniteNumber(value.note_range.max_note) &&
    isFiniteNumber(value.connected_motors) &&
    isFiniteNumber(value.lanes) &&
    isString(value.allocation.policy) &&
    isFiniteNumber(value.allocation.stolen_notes) &&
    isFiniteNumber(value.allocation.dropped_notes) &&
    isFiniteNumber(value.allocation.playable_notes) &&
    isFiniteNumber(value.allocation.retained_ratio) &&
    isFiniteNumber(value.window.history_us) &&
    isFiniteNumber(value.window.lookahead_us) &&
    themeDefaultValid &&
    themesAvailableValid &&
    colorModeDefaultValid &&
    colorModesAvailableValid &&
    showControlsValid &&
    syncOffsetValid &&
    syncStrategyValid &&
    scheduledStartUnixValid &&
    scheduledStartDeviceValid &&
    driftRebaseThresholdValid &&
    isFiniteNumber(value.generated_at_unix_ms)
  );
}

export function isViewerTimelineV1(value: unknown): value is ViewerTimelineV1 {
  if (!isRecord(value)) {
    return false;
  }

  if (!isRecord(value.note_range) || !isRecord(value.style_hints)) {
    return false;
  }

  return (
    value.version === 'v1' &&
    isFiniteNumber(value.fps) &&
    value.fps > 0 &&
    isFiniteNumber(value.duration_us) &&
    isFiniteNumber(value.frame_count) &&
    isFiniteNumber(value.note_range.min_note) &&
    isFiniteNumber(value.note_range.max_note) &&
    Array.isArray(value.bars_static) &&
    value.bars_static.every(isViewerTimelineBar) &&
    Array.isArray(value.frames) &&
    value.frames.every(isViewerTimelineFrameV1) &&
    value.frame_count === value.frames.length &&
    isFiniteNumber(value.generated_at_unix_ms)
  );
}

export function isViewerFrameMessage(value: unknown): value is ViewerFrameMessage {
  if (!isRecord(value)) {
    return false;
  }

  return (
    value.type === 'frame' &&
    isFiniteNumber(value.seq) &&
    isFiniteNumber(value.playhead_us) &&
    isFiniteNumber(value.window_start_us) &&
    isFiniteNumber(value.window_end_us) &&
    isFiniteNumber(value.duration_us) &&
    Array.isArray(value.bars) &&
    value.bars.every(isViewerBar) &&
    isNumberArray(value.active_note_ids) &&
    isNumberArray(value.beat_markers_us) &&
    isViewerFrameState(value.state)
  );
}

export function isViewerHelloMessage(value: unknown): value is ViewerHelloMessage {
  return isRecord(value) && value.type === 'hello';
}

export function isViewerHeartbeatMessage(value: unknown): value is ViewerHeartbeatMessage {
  return isRecord(value) && value.type === 'heartbeat';
}

export function parseHealthResponse(payload: unknown): HealthResponse {
  if (!isRecord(payload)) {
    return { status: 'unknown' };
  }

  return {
    status: isString(payload.status) ? payload.status : 'unknown',
  };
}

export function parseViewerSession(payload: unknown): ViewerSession {
  if (!isViewerSession(payload)) {
    throw new Error('Unexpected /api/viewer/session response shape');
  }
  return payload;
}

export function parseViewerTimeline(payload: unknown): ViewerTimelineV1 {
  if (!isViewerTimelineV1(payload)) {
    throw new Error('Unexpected /api/viewer/timeline response shape');
  }
  return payload;
}

export function parseViewerSocketMessage(
  data: string | Blob | ArrayBuffer,
): ViewerSocketMessage | null {
  if (typeof data !== 'string') {
    return null;
  }

  let payload: unknown;

  try {
    payload = JSON.parse(data) as unknown;
  } catch {
    return null;
  }

  if (isViewerFrameMessage(payload)) {
    return payload;
  }
  if (isViewerHelloMessage(payload)) {
    return payload;
  }
  if (isViewerHeartbeatMessage(payload)) {
    return payload;
  }

  return null;
}
