import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './services/api';

const mocks = vi.hoisted(() => ({
  getMusicStems: vi.fn(),
  getMusicValidation: vi.fn(),
  getLoopValidation: vi.fn(),
  getPs1Validation: vi.fn(),
  runPs1Validation: vi.fn(),
  getMusicJob: vi.fn(),
  generateMusicFromSpecification: vi.fn(),
  waitForMusicJob: vi.fn(),
  downloadJobPackage: vi.fn(),
  createMusicSpecification: vi.fn(),
}));

vi.mock('./services/musicApi', async () => {
  const actual = await vi.importActual<typeof import('./services/musicApi')>('./services/musicApi');
  return {
    ...actual,
    getMusicStems: mocks.getMusicStems,
    getMusicValidation: mocks.getMusicValidation,
    getLoopValidation: mocks.getLoopValidation,
    getPs1Validation: mocks.getPs1Validation,
    runPs1Validation: mocks.runPs1Validation,
    getMusicJob: mocks.getMusicJob,
    generateMusicFromSpecification: mocks.generateMusicFromSpecification,
    waitForMusicJob: mocks.waitForMusicJob,
    downloadJobPackage: mocks.downloadJobPackage,
  };
});

vi.mock('./services/specificationApi', async () => {
  const actual = await vi.importActual<typeof import('./services/specificationApi')>('./services/specificationApi');
  return {
    ...actual,
    createMusicSpecification: mocks.createMusicSpecification,
  };
});

import App, { AudioPlayer, Studio, Validation } from './App';

describe('Studio Module 7 rendering', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.getMusicStems.mockResolvedValue({
      job_id: 'job-123',
      stems: [
        { name: 'drums', filename: 'drums.wav', url: '/api/v1/music/jobs/job-123/stems/drums' },
        { name: 'bass', filename: 'bass.wav', url: '/api/v1/music/jobs/job-123/stems/bass' },
        { name: 'harmony', filename: 'harmony.wav', url: '/api/v1/music/jobs/job-123/stems/harmony' },
        { name: 'melody', filename: 'melody.wav', url: '/api/v1/music/jobs/job-123/stems/melody' },
      ],
      mix_url: '/api/v1/music/jobs/job-123/mix',
    });
    mocks.getMusicValidation.mockResolvedValue({
      job_id: 'job-123',
      overall_status: 'PASS',
      audio_validation: { sample_rate: 44100 },
      stems: {},
    });
    mocks.getLoopValidation.mockResolvedValue({
      job_id: 'job-123',
      loop: {
        status: 'PASS',
        loop_start_s: 5,
        loop_end_s: 17,
        loop_duration_s: 12,
      },
    });
    mocks.getPs1Validation.mockResolvedValue({
      job_id: 'job-123',
      overall_status: 'FAIL',
      passed_count: 1,
      warning_count: 0,
      failed_count: 1,
      percentage: 50,
      validated_at: '2026-01-01T00:00:00Z',
      requirements: [
        {
          requirement_id: 'M1',
          name: 'Exact Length',
          category: 'MUST_HAVE',
          status: 'PASS',
          summary: 'Requested 45.00 sec, actual 45.00 sec, tolerance ±0.05 sec.',
          evidence: { requested_duration_seconds: 45, actual_duration_seconds: 45, duration_tolerance_seconds: 0.05 },
          associated_job_id: 'job-123',
        },
        {
          requirement_id: 'A5',
          name: 'Seamless Looping',
          category: 'ADVANCED',
          status: 'FAIL',
          summary: 'Loop 5.00–17.00 sec, duration 12.00 sec, discontinuity 0.312500.',
          evidence: {
            loop_start_s: 5,
            loop_end_s: 17,
            loop_duration_s: 12,
            bpm: 128,
            beats_per_bar: 4,
            start_aligned_to_bar: true,
            end_aligned_to_bar: true,
            boundary_discontinuity: 0.3125,
            status: 'FAIL',
          },
          associated_job_id: 'loop-job-123',
        },
      ],
    });
    mocks.runPs1Validation.mockResolvedValue({
      job_id: 'job-123',
      overall_status: 'PASS',
      passed_count: 2,
      warning_count: 0,
      failed_count: 0,
      percentage: 100,
      validated_at: '2026-01-01T00:05:00Z',
      requirements: [
        {
          requirement_id: 'M1',
          name: 'Exact Length',
          category: 'MUST_HAVE',
          status: 'PASS',
          summary: 'Requested 45.00 sec, actual 45.00 sec, tolerance ±0.05 sec.',
          evidence: { requested_duration_seconds: 45, actual_duration_seconds: 45, duration_tolerance_seconds: 0.05 },
          associated_job_id: 'job-123',
        },
        {
          requirement_id: 'A5',
          name: 'Seamless Looping',
          category: 'ADVANCED',
          status: 'PASS',
          summary: 'Loop 5.00–17.00 sec, duration 12.00 sec, discontinuity 0.012500.',
          evidence: {
            loop_start_s: 5,
            loop_end_s: 17,
            loop_duration_s: 12,
            bpm: 128,
            beats_per_bar: 4,
            start_aligned_to_bar: true,
            end_aligned_to_bar: true,
            boundary_discontinuity: 0.0125,
            status: 'PASS',
          },
          associated_job_id: 'loop-job-123',
        },
      ],
    });
    mocks.generateMusicFromSpecification.mockResolvedValue({ job_id: 'job-37', status: 'queued' });
    mocks.waitForMusicJob.mockImplementation(async (_jobId: string, onUpdate?: (job: any) => void) => {
      const completed = {
        job_id: 'job-37',
        status: 'succeeded',
        progress: 100,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        prompt: 'demo',
        duration_s: 37,
        error: null,
        result: {
          manifest: {
            duration_s: 37,
            genre: 'Documentary',
            mood: ['Focused'],
            instruments: ['Drums', 'Bass', 'Piano'],
            stem_count: 3,
            bpm: 73,
            key: 'F# minor',
            beats_per_bar: 4,
            beat_duration_s: 0.821918,
            bar_duration_s: 3.287671,
            sections: [
              { name: 'INTRO', start_s: 0, end_s: 8.14, duration_s: 8.14 },
              { name: 'BUILD', start_s: 8.14, end_s: 20.35, duration_s: 12.21 },
              { name: 'MAIN', start_s: 20.35, end_s: 32.56, duration_s: 12.21 },
              { name: 'OUTRO', start_s: 32.56, end_s: 37, duration_s: 4.44 },
            ],
          },
          stems: [],
          mix: { name: 'mix', filename: 'mix.wav', url: '/api/v1/music/jobs/job-37/mix' },
        },
      };
      onUpdate?.(completed);
      return completed;
    });
    mocks.createMusicSpecification.mockImplementation(async (request: any) => ({
      specification: {
        genre: request.genre,
        mood: request.mood,
        tempo: request.tempo,
        key: request.key,
        instruments: request.instruments,
        sections: request.sections,
      },
    }));
    mocks.downloadJobPackage.mockResolvedValue('incubrix_job-123_package.zip');
  });

  it('renders BPM, key, section timings, and loop metadata from the backend manifest and loop API', async () => {
    const job = {
      job_id: 'job-123',
      status: 'succeeded',
      progress: 100,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      prompt: 'demo',
      duration_s: 45,
      error: null,
      result: {
        manifest: {
          duration_s: 45,
          bpm: 128,
          key: 'C major',
          beats_per_bar: 4,
          beat_duration_s: 0.46875,
          bar_duration_s: 1.875,
          sections: [
            { name: 'INTRO', start_s: 0, end_s: 10, duration_s: 10 },
            { name: 'BUILD', start_s: 10, end_s: 25, duration_s: 15 },
            { name: 'MAIN', start_s: 25, end_s: 40, duration_s: 15 },
            { name: 'OUTRO', start_s: 40, end_s: 45, duration_s: 5 },
          ],
        },
        bpm: 128,
        key: 'C major',
        loop: {
          status: 'PASS',
          loop_start_s: 5,
          loop_end_s: 17,
          loop_duration_s: 12,
        },
        looped_mix: { url: '/api/v1/music/jobs/job-123/loop' },
      },
    } as any;

    render(<Studio job={job} />);

    await waitFor(() => expect(screen.getByText('12.00 sec')).toBeInTheDocument());
    expect(screen.getByText('128')).toBeInTheDocument();
    expect(screen.getByText('C major')).toBeInTheDocument();
    expect(screen.getByText('INTRO')).toBeInTheDocument();
    expect(screen.getByText('BUILD')).toBeInTheDocument();
    expect(screen.getByText('MAIN')).toBeInTheDocument();
    expect(screen.getByText('OUTRO')).toBeInTheDocument();
    expect(screen.getAllByText('Loop validation').length).toBeGreaterThan(0);
    expect(screen.getByText('12.00 sec')).toBeInTheDocument();
  });

  it('downloads the backend package once and shows a loading state while it is being prepared', async () => {
    const job = {
      job_id: 'job-123',
      status: 'succeeded',
      progress: 100,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      prompt: 'demo',
      duration_s: 45,
      error: null,
      result: { manifest: { duration_s: 45, sections: [], instruments: [], stem_count: 4 } },
    } as any;

    let resolveDownload: () => void = () => { throw new Error('Download has not started'); };
    mocks.downloadJobPackage.mockImplementationOnce(() => new Promise<string>((resolve) => {
      resolveDownload = () => resolve('incubrix_job-123_package.zip');
    }));

    render(<Studio job={job} />);

    await waitFor(() => expect(mocks.getMusicStems).toHaveBeenCalledWith('job-123'));
    const button = screen.getByRole('button', { name: /Download Package/i });
    fireEvent.click(button);

    expect(mocks.downloadJobPackage).toHaveBeenCalledWith('job-123');
    expect(screen.getByRole('button', { name: 'Preparing Package...' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Preparing Package...' }));
    expect(mocks.downloadJobPackage).toHaveBeenCalledTimes(1);

    resolveDownload?.();
    await waitFor(() => expect(screen.getByRole('button', { name: /Download Package/i })).not.toBeDisabled());
  });

  it('shows a clear error when the package download fails', async () => {
    const job = {
      job_id: 'job-123',
      status: 'succeeded',
      progress: 100,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      prompt: 'demo',
      duration_s: 45,
      error: null,
      result: { manifest: { duration_s: 45, sections: [], instruments: [], stem_count: 4 } },
    } as any;

    mocks.downloadJobPackage.mockRejectedValueOnce(new ApiError(409, 'PACKAGE_NOT_READY', 'Unable to download the package yet.'));

    render(<Studio job={job} />);

    await waitFor(() => expect(mocks.getMusicStems).toHaveBeenCalledWith('job-123'));
    fireEvent.click(screen.getByRole('button', { name: /Download Package/i }));

    await waitFor(() => expect(screen.getByText('Unable to download the package yet.')).toBeInTheDocument());
  });
});

describe('AudioPlayer loop behavior', () => {
  it('restarts from the validated loop start when playback reaches the loop end', async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});

    render(<AudioPlayer src="/loop.wav" label="looped mix" loopRange={{ start: 5, end: 17 }} />);

    expect(screen.getByText('Seamless looping active')).toBeInTheDocument();

    const audio = document.querySelector('audio') as HTMLAudioElement;
    Object.defineProperty(audio, 'duration', { configurable: true, value: 20 });
    let currentTime = 0;
    Object.defineProperty(audio, 'currentTime', {
      configurable: true,
      get: () => currentTime,
      set: (value: number) => {
        currentTime = value;
      },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Play looped mix' }));
    expect(audio.currentTime).toBe(5);
    currentTime = 17;

    fireEvent.timeUpdate(audio);
    expect(audio.currentTime).toBe(5);
    expect(play).toHaveBeenCalledTimes(1);

    const endedAudio = document.querySelector('audio') as HTMLAudioElement;
    Object.defineProperty(endedAudio, 'currentTime', {
      configurable: true,
      value: 17,
      writable: true,
    });
    fireEvent.ended(endedAudio);
    expect(endedAudio.currentTime).toBe(5);

    play.mockRestore();
    pause.mockRestore();
    load.mockRestore();
  });
});

describe('Validation page', () => {
  it('loads backend validation, opens evidence, exports the backend report, and loads associated jobs', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const onLoadJob = vi.fn();
    const job = {
      job_id: 'job-123',
      status: 'succeeded',
      progress: 100,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      prompt: 'demo',
      duration_s: 45,
      error: null,
      result: {},
    } as any;

    render(<Validation job={job} onLoadJob={onLoadJob} />);

    await waitFor(() => expect(mocks.getPs1Validation).toHaveBeenCalledWith('job-123'));
    expect(screen.getByText('Loop 5.00–17.00 sec, duration 12.00 sec, discontinuity 0.312500.')).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'View Evidence' })[1]);
    expect(screen.getAllByText('Seamless Looping').length).toBeGreaterThan(1);
    expect(screen.getByText(/boundary_discontinuity/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Close' })[1]);
    fireEvent.click(screen.getByText('loop-job-123'));
    expect(onLoadJob).toHaveBeenCalledWith('loop-job-123');

    fireEvent.click(screen.getByRole('button', { name: 'Export Report' }));
    expect(openSpy).toHaveBeenCalledWith('http://localhost:8000/api/v1/music/jobs/job-123/ps1-validation/report', '_blank', 'noopener,noreferrer');

    openSpy.mockRestore();
  });

  it('reruns backend validation and refreshes the rendered results', async () => {
    const job = {
      job_id: 'job-123',
      status: 'succeeded',
      progress: 100,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      prompt: 'demo',
      duration_s: 45,
      error: null,
      result: {},
    } as any;

    render(<Validation job={job} onLoadJob={() => undefined} />);

    await waitFor(() => expect(screen.getByText('Loop 5.00–17.00 sec, duration 12.00 sec, discontinuity 0.312500.')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Run Validation' }));

    await waitFor(() => expect(mocks.runPs1Validation).toHaveBeenCalledWith('job-123'));
    await waitFor(() => expect(screen.getByText('Loop 5.00–17.00 sec, duration 12.00 sec, discontinuity 0.012500.')).toBeInTheDocument());
  });
});

describe('Create flow', () => {
  it('propagates explicit duration, BPM, key, and three selected instruments through the specification request', async () => {
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Custom' }));
    fireEvent.change(screen.getByLabelText('Custom duration seconds'), { target: { value: '37' } });
    fireEvent.change(screen.getByLabelText('Tempo BPM'), { target: { value: '73' } });
    fireEvent.change(screen.getByLabelText('Musical key'), { target: { value: 'F# minor' } });
    fireEvent.click(screen.getByText('Pads').closest('button') as HTMLButtonElement);
    fireEvent.click(screen.getAllByRole('button', { name: 'Documentary' })[1]);
    fireEvent.click(screen.getByRole('button', { name: /Generate Background Music/ }));

    await waitFor(() => expect(mocks.createMusicSpecification).toHaveBeenCalled());
    const request = mocks.createMusicSpecification.mock.calls[0][0];
    expect(request.genre).toBe('Documentary');
    expect(request.tempo).toBe(73);
    expect(request.key).toBe('F# minor');
    expect(request.instruments).toEqual(['Drums', 'Bass', 'Piano']);
    expect(request.sections.at(-1)?.end).toBe(37);

    await waitFor(() => expect(mocks.generateMusicFromSpecification).toHaveBeenCalledWith(expect.objectContaining({
      genre: 'Documentary',
      tempo: 73,
      key: 'F# minor',
      instruments: ['Drums', 'Bass', 'Piano'],
    })));
  });
});
