import { describe, expect, it } from 'vitest';
import { ageWords, fmtBytes, humanize, platformLabel, sourceLabel, statusChip, timeAgo, typeLabel } from './format';
import { noteSampleFlag, isSampleNow, resetSampleFlag } from './sample';
import { checkDimensions, sniffImageFormat } from '../utils/logoCheck';
import { rangeFor, rangeLabel, rangeText } from '../utils/dates';
import { describeRetry, eventsFromScans } from '../pages/SourceHealthPage';
import { reportName } from '../pages/ReportsPage';
import type { Scan } from './types';

describe('labels', () => {
  it('replaces EVERY underscore (the old type.replace bug printed "fake brand_page")', () => {
    expect(humanize('fake_brand_page')).toBe('fake brand page');
    expect(typeLabel('fake_brand_page')).toBe('Fake brand page');
    expect(typeLabel('fake_brand_page', true)).toBe('Fake page');
    expect(typeLabel('scam_account')).toBe('Scam account');
    expect(typeLabel('some_new_type')).toBe('Some new type');
  });

  it('uses App Store for detections and Apple App Store for sources', () => {
    expect(platformLabel('app_store')).toBe('App Store');
    expect(sourceLabel('app_store')).toBe('Apple App Store');
    expect(platformLabel('third_party_apk')).toBe('Third-party APK');
    expect(sourceLabel('third_party_apk')).toBe('Third-party APK sites');
    expect(platformLabel('x')).toBe('X');
  });

  it('colours statuses as the design does', () => {
    expect(statusChip('new')).toBe('chip-blue');
    expect(statusChip('reviewing')).toBe('chip-orange');
    expect(statusChip('confirmed')).toBe('chip-red');
    expect(statusChip('dismissed')).toBe('chip-green');
    expect(statusChip('marked_official')).toBe('chip-green');
  });

  it('formats time and sizes', () => {
    const now = Date.parse('2026-10-12T12:00:00Z');
    expect(timeAgo('2026-10-12T11:58:00Z', now)).toBe('2 minutes ago');
    expect(timeAgo('2026-10-12T11:00:00Z', now)).toBe('1 hour ago');
    expect(timeAgo('2026-10-09T12:00:00Z', now)).toBe('3 days ago');
    expect(ageWords(7200)).toBe('2 hours');
    expect(fmtBytes(86016)).toBe('84 KB');
  });
});

describe('sample flag', () => {
  it('follows is_sample and the data-mode fields of API answers', () => {
    resetSampleFlag();
    noteSampleFlag({ is_sample: true });
    expect(isSampleNow()).toBe(true);
    noteSampleFlag({ is_sample: false });
    expect(isSampleNow()).toBe(false);
    noteSampleFlag({ effective_mode: 'sample' });
    expect(isSampleNow()).toBe(true);
    noteSampleFlag({ data_mode: 'live' });
    expect(isSampleNow()).toBe(false);
    noteSampleFlag({ data_mode: 'auto', effective_data_mode: 'sample' });
    expect(isSampleNow()).toBe(true);
    noteSampleFlag(null);
    noteSampleFlag({ unrelated: 1 });
    expect(isSampleNow()).toBe(true); // answers without any mode information change nothing
    resetSampleFlag();
  });
});

describe('logo pre-check mirrors the server rules', () => {
  it('requires 256px and a roughly square image', () => {
    expect(checkDimensions(512, 512)).toBe('');
    expect(checkDimensions(255, 512)).toBe('Logo must be at least 256 x 256 pixels');
    expect(checkDimensions(512, 300)).toBe('Logo must be roughly square');
    expect(checkDimensions(512, 410)).toBe(''); // aspect 1.249
  });

  it('recognises formats by their bytes, not the file name', () => {
    expect(sniffImageFormat(Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]))).toBe('png');
    expect(sniffImageFormat(Uint8Array.from([0xff, 0xd8, 0xff, 0xe0]))).toBe('jpg');
    expect(sniffImageFormat(new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"/>'))).toBeNull();
  });
});

describe('report helpers', () => {
  it('shows handles for social rows and the app display name (not the package id) for apps', () => {
    expect(reportName({ category: 'social', handle: '@acrne_bank_support', name: 'Acme Bank Support Care' }, 'x')).toBe('@acrne_bank_support');
    expect(reportName({ category: 'apps', handle: null, name: 'Acme Bnak Mobile' }, 'x')).toBe('Acme Bnak Mobile');
    expect(reportName(undefined, 'fallback')).toBe('fallback');
  });

  it('computes date ranges from today instead of hard-coded dates', () => {
    const now = new Date(2026, 9, 12, 15, 0);
    expect(rangeLabel('7d', now)).toBe('Last 7 days (6 to 12 Oct 2026)');
    expect(rangeFor('7d', now).fromIso).toBe('2026-10-06');
    expect(rangeFor('qtd', now).fromIso).toBe('2026-10-01');
    expect(rangeText(rangeFor('30d', now))).toBe('13 Sep to 12 Oct 2026');
  });
});

describe('source health helpers', () => {
  it('describes the real retry back-off from the API', () => {
    expect(describeRetry('1m,5m,15m,1h,6h')).toMatch(/after 1, 5 and 15 minutes, then hourly, backing off to every 6 hours/);
    expect(describeRetry(null)).toMatch(/1, 5 and 15 minutes/);
  });

  it('builds source events from scan reports and keeps non-online sources', () => {
    const scans = [
      {
        id: 2, status: 'partial', result: { new_detections: 1 }, message: 'Scan finished with some sources unavailable.', finished_at: '2026-10-12T11:00:00Z', started_at: null, created_at: '2026-10-12T10:59:00Z',
        source_report: [{ collector: 'tavily', status: 'unavailable', reason: 'no_key', message: 'No key', data_source: 'live' }, { collector: 'sample', status: 'online', reason: null, message: 'ok', data_source: 'sample' }],
      },
    ] as unknown as Scan[];
    const ev = eventsFromScans(scans);
    expect(ev.map((e) => e.title)).toEqual(['Scan partial, 1 new detection, Scan finished with some sources unavailable.', 'tavily unavailable, No key']);
  });
});
