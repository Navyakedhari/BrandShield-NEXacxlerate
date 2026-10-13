import React, { useMemo, useState } from 'react';
import { Download } from 'lucide-react';
import { Header } from '../components/layout/Header';
import { Empty, ErrorBox, Loading, NoBrand, Notice, SampleNote } from '../components/common/States';
import { ApiError } from '../api/client';
import { fmtBytes, fmtDay, platformLabel, statusChip, statusLabel, typeLabel } from '../api/format';
import { sm } from '../api/sm';
import type { DetectionRow, DetectionType, Report } from '../api/types';
import { useApi } from '../hooks/useApi';
import { useBrand } from '../hooks/useBrand';
import { useMe } from '../hooks/useMe';
import { bounds, rangeFor, rangeLabel, rangeText } from '../utils/dates';
import type { RangeKey } from '../utils/dates';
import './ReportsPage.css';

type StatusFilter = 'all' | 'new' | 'reviewing' | 'confirmed';
type Format = 'html' | 'csv';

/** The name shown in the report table: the handle for social accounts, the app's display name for apps (not the package id). */
export function reportName(det: Pick<DetectionRow, 'category' | 'handle' | 'name'> | undefined, fallback: string): string {
  if (!det) return fallback;
  return det.category === 'social' ? det.handle || det.name : det.name;
}

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export const ReportsPage: React.FC = () => {
  const { brand, missing } = useBrand();
  const { canWrite } = useMe();

  const [rangeKey, setRangeKey] = useState<RangeKey>('7d');
  const [includeProfiles, setIncludeProfiles] = useState(true);
  const [includeBrandPages, setIncludeBrandPages] = useState(true);
  const [includeScamAccounts, setIncludeScamAccounts] = useState(true);
  const [includeApps, setIncludeApps] = useState(true);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [includeEvidence, setIncludeEvidence] = useState(true);
  const [exportFormat, setExportFormat] = useState<Format>('html');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [previewHtml, setPreviewHtml] = useState<{ id: number; html: string } | null>(null);

  const range = useMemo(() => rangeFor(rangeKey), [rangeKey]);

  // Which detections the form selects: all listed detections (never cleared / marked-official ones), filtered by the form.
  const all = useApi((signal) => sm.detections({ limit: 200, sort: 'score_desc' }, signal), []);
  const types = useMemo(() => {
    const t: DetectionType[] = [];
    if (includeProfiles) t.push('fake_profile');
    if (includeBrandPages) t.push('fake_brand_page');
    if (includeScamAccounts) t.push('scam_account');
    if (includeApps) t.push('fake_app');
    return t;
  }, [includeProfiles, includeBrandPages, includeScamAccounts, includeApps]);

  const selected = useMemo(() => {
    const { start, end } = bounds(range);
    return (all.data?.items ?? []).filter((d) => {
      if (!types.includes(d.type)) return false;
      if (statusFilter !== 'all' && d.status !== statusFilter) return false;
      const t = new Date(d.found_at).getTime();
      return t >= start && t <= end;
    });
  }, [all.data, types, statusFilter, range]);
  const ids = useMemo(() => selected.map((d) => d.id), [selected]);
  const byId = useMemo(() => new Map(selected.map((d) => [d.id, d])), [selected]);

  const idsKey = ids.join(',');
  const preview = useApi(
    (signal) => sm.reportPreview({ ids, from: range.fromIso, to: range.toIso, include_evidence: includeEvidence }, signal),
    [idsKey, range.fromIso, range.toIso, includeEvidence],
    !!all.data && ids.length > 0
  );

  const reports = useApi((signal) => sm.reports(10, signal), []);

  const handleGenerate = async () => {
    if (ids.length === 0) {
      setError('No detections match these filters, so there is nothing to export.');
      return;
    }
    setGenerating(true);
    setError(null);
    setInfo(null);
    try {
      const r = await sm.createReport({
        detection_ids: ids,
        from: range.fromIso,
        to: range.toIso,
        types,
        status: statusFilter === 'all' ? null : statusFilter,
        include_evidence: includeEvidence,
        format: exportFormat,
      });
      reports.setData((d) => ({ total: (d?.total ?? 0) + 1, items: [r, ...(d?.items ?? [])].slice(0, 10) }));
      setInfo(`Draft created: ${r.filename}. A person approves it before it is shared. Nothing was sent anywhere.`);
    } catch (e) {
      setError(errText(e, 'Could not create the report.')); // e.g. 422 when nothing matches
    } finally {
      setGenerating(false);
    }
  };

  const replaceReport = (r: Report) =>
    reports.setData((d) => (d ? { ...d, items: d.items.map((x) => (x.id === r.id ? r : x)) } : d));

  const handleDownload = async (r: Report, kind: Format) => {
    setBusyId(r.id);
    setError(null);
    try {
      await sm.downloadReport(r.id, kind, r.filename.replace(/\.[a-z]+$/i, `.${kind}`));
    } catch (e) {
      setError(errText(e, 'Download failed.'));
    } finally {
      setBusyId(null);
    }
  };

  const handlePreview = async (r: Report) => {
    setBusyId(r.id);
    setError(null);
    try {
      setPreviewHtml({ id: r.id, html: await sm.reportHtml(r.id) });
    } catch (e) {
      setError(errText(e, 'Could not load the preview.'));
    } finally {
      setBusyId(null);
    }
  };

  const handleApprove = async (r: Report) => {
    setBusyId(r.id);
    setError(null);
    const before = r;
    replaceReport({ ...r, status: 'approved' }); // optimistic
    try {
      replaceReport(await sm.approveReport(r.id));
    } catch (e) {
      replaceReport(before);
      setError(errText(e, 'Could not approve the report.'));
    } finally {
      setBusyId(null);
    }
  };

  const header = <Header title="Reports" subtitle="Export detections with reasons and evidence for your team or the platforms" />;
  if (missing) {
    return (
      <>
        {header}
        <div className="panel-content">
          <NoBrand />
        </div>
      </>
    );
  }

  const pv = ids.length > 0 ? preview.data : null;
  const stats = pv?.stats ?? { detections: ids.length === 0 ? 0 : selected.length, social: 0, apps: 0, confirmed: 0, official_excluded: brand?.official_assets ?? 0 };
  const rows = pv?.rows ?? [];
  const showSampleChip = !!pv && (pv.is_sample || pv.includes_sample);

  return (
    <>
      {header}

      <div className="panel-content">
        <div className="pdf-reports-wrap">
          {error && <Notice onClose={() => setError(null)}>{error}</Notice>}
          {info && <Notice kind="info" onClose={() => setInfo(null)}>{info}</Notice>}

          {/* Top 2-Column Split */}
          <div className="reports-top-split">
            {/* Left: Export Form */}
            <div className="card export-form-card">
              <h3 className="panel-card-title">Export detections</h3>

              <div className="export-field">
                <label className="export-lbl" htmlFor="rp-range">Date range</label>
                <select
                  id="rp-range"
                  className="export-select"
                  value={rangeKey}
                  onChange={(e) => setRangeKey(e.target.value as RangeKey)}
                >
                  <option value="7d">{rangeLabel('7d')}</option>
                  <option value="30d">{rangeLabel('30d')}</option>
                  <option value="qtd">{rangeLabel('qtd')}</option>
                </select>
              </div>

              {/* Include checkboxes */}
              <div className="export-field">
                <label className="export-lbl">Include</label>
                <div className="checkbox-column">
                  <label className="chk-label">
                    <input type="checkbox" checked={includeProfiles} onChange={(e) => setIncludeProfiles(e.target.checked)} className="chk-input" />
                    <span>Fake profiles</span>
                  </label>

                  <label className="chk-label">
                    <input type="checkbox" checked={includeBrandPages} onChange={(e) => setIncludeBrandPages(e.target.checked)} className="chk-input" />
                    <span>Fake brand pages</span>
                  </label>

                  <label className="chk-label">
                    <input type="checkbox" checked={includeScamAccounts} onChange={(e) => setIncludeScamAccounts(e.target.checked)} className="chk-input" />
                    <span>Scam accounts</span>
                  </label>

                  <label className="chk-label">
                    <input type="checkbox" checked={includeApps} onChange={(e) => setIncludeApps(e.target.checked)} className="chk-input" />
                    <span>Fake apps</span>
                  </label>
                </div>
              </div>

              {/* Status */}
              <div className="export-field">
                <label className="export-lbl" htmlFor="rp-status">Status</label>
                <select
                  id="rp-status"
                  className="export-select"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
                >
                  <option value="all">All statuses</option>
                  <option value="new">New only</option>
                  <option value="reviewing">Reviewing only</option>
                  <option value="confirmed">Confirmed only</option>
                </select>
              </div>

              {/* Evidence checkbox */}
              <div className="export-field">
                <label className="chk-label">
                  <input type="checkbox" checked={includeEvidence} onChange={(e) => setIncludeEvidence(e.target.checked)} className="chk-input" />
                  <span>Include evidence (screenshots, URLs, hashes)</span>
                </label>
              </div>

              {/* Format pills */}
              <div className="export-field">
                <label className="export-lbl">Format</label>
                <div className="format-toggle-row">
                  <button
                    type="button"
                    className={`format-toggle-btn ${exportFormat === 'html' ? 'active' : ''}`}
                    onClick={() => setExportFormat('html')}
                    title="A printable page. Use your browser's Print, then Save as PDF."
                  >
                    Printable report
                  </button>
                  <button
                    type="button"
                    className={`format-toggle-btn ${exportFormat === 'csv' ? 'active' : ''}`}
                    onClick={() => setExportFormat('csv')}
                  >
                    CSV data
                  </button>
                </div>
                {exportFormat === 'html' && <div className="helper-note">Opens as a printable page: use Print, then Save as PDF. A built-in PDF file is not available.</div>}
              </div>

              <button
                type="button"
                className="btn-primary generate-btn"
                onClick={() => void handleGenerate()}
                disabled={generating || !canWrite || ids.length === 0}
                title={canWrite ? undefined : 'Your role can view but not create reports'}
              >
                {generating ? 'Generating…' : 'Generate report'}
              </button>
              <div className="helper-note">Creates a draft. A person approves it; nothing is sent automatically.</div>
            </div>

            {/* Right: Preview Dossier */}
            <div className="card report-preview-card">
              <div className="preview-card-header">
                <div className="preview-title-row">
                  <div className="report-doc-badge">{(brand?.name ?? 'A').charAt(0).toUpperCase()}</div>
                  <div>
                    <h3 className="preview-doc-title">Brand Protection Report</h3>
                    <div className="preview-doc-sub">
                      {brand?.name ?? ''} · {rangeText(range)} · Preview
                    </div>
                  </div>
                </div>

                {showSampleChip && <span className="sample-data-chip" data-testid="preview-sample-chip">Sample data</span>}
              </div>

              {/* Top stats bar */}
              <div className="report-stats-grid">
                <div className="report-stat-cell">
                  <span className="stat-value">{stats.detections}</span>
                  <span className="stat-desc">Detections</span>
                </div>
                <div className="report-stat-cell">
                  <span className="stat-value">{stats.social}</span>
                  <span className="stat-desc">Social</span>
                </div>
                <div className="report-stat-cell">
                  <span className="stat-value">{stats.apps}</span>
                  <span className="stat-desc">Apps</span>
                </div>
                <div className="report-stat-cell">
                  <span className="stat-value">{stats.confirmed}</span>
                  <span className="stat-desc">Confirmed</span>
                </div>
                <div className="report-stat-cell">
                  <span className="stat-value">{stats.official_excluded}</span>
                  <span className="stat-desc">Official, excluded</span>
                </div>
              </div>

              {/* Detections Preview Table */}
              <div className="preview-table-wrap">
                {(all.loading || preview.loading) && <Loading what="Loading preview" />}
                {all.error && <ErrorBox error={all.error} onRetry={all.reload} compact />}
                {preview.error && <ErrorBox error={preview.error} onRetry={preview.reload} compact />}
                {all.data && ids.length === 0 && <Empty>No detections match these filters.</Empty>}
                <table className="preview-table">
                  <thead>
                    <tr>
                      <th style={{ width: '36px' }}>#</th>
                      <th>Name or handle</th>
                      <th>Platform</th>
                      <th>Type</th>
                      <th>Score</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.id}>
                        <td className="row-num-cell">{row.n}</td>
                        <td className="row-name-cell">{reportName(byId.get(row.id), row.name)}</td>
                        <td className="cell-muted">{platformLabel(row.platform)}</td>
                        <td className="cell-muted">{typeLabel(row.type)}</td>
                        <td className="row-score-cell">{row.score}</td>
                        <td>
                          <span className={`chip ${statusChip(row.status)}`}>{statusLabel(row.status)}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Bottom Recent Exports */}
          <div className="card recent-exports-card">
            <h3 className="panel-card-title">Recent exports</h3>

            <div className="recent-exports-list">
              {reports.loading && <Loading />}
              {reports.error && <ErrorBox error={reports.error} onRetry={reports.reload} compact />}
              {reports.data && reports.data.items.length === 0 && <Empty>No reports yet. Generate one from the form above.</Empty>}
              {(reports.data?.items ?? []).map((r) => (
                <div key={r.id} className="recent-export-item" data-testid="report-item">
                  <div className="export-file-info">
                    <span className="export-filename">{r.filename}</span>
                    <span className="export-filemeta">
                      {r.format.toUpperCase()} · {fmtBytes(r.size_bytes)} · {r.created_by} · {fmtDay(r.created_at)} · {r.status === 'approved' ? 'Approved' : 'Draft'}
                      {r.includes_sample ? ' · Sample data' : ''}
                    </span>
                  </div>

                  <div className="cases-header-actions">
                    <button type="button" className="btn-secondary btn-sm" onClick={() => void handlePreview(r)} disabled={busyId === r.id}>
                      Preview
                    </button>
                    {r.status === 'draft' && (
                      <button type="button" className="btn-secondary btn-sm" onClick={() => void handleApprove(r)} disabled={busyId === r.id || !canWrite}>
                        Approve
                      </button>
                    )}
                    <button type="button" className="btn-secondary btn-sm download-btn" onClick={() => void handleDownload(r, r.format)} disabled={busyId === r.id}>
                      <Download size={13} />
                      <span>Download</span>
                    </button>
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      onClick={() => void handleDownload(r, r.format === 'csv' ? 'html' : 'csv')}
                      disabled={busyId === r.id}
                    >
                      As {r.format === 'csv' ? 'HTML' : 'CSV'}
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {previewHtml && (
              <div style={{ marginTop: 14 }}>
                <div className="cases-header-actions" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
                  <span className="export-filename">Report preview</span>
                  <button type="button" className="btn-secondary btn-sm" onClick={() => setPreviewHtml(null)}>
                    Close preview
                  </button>
                </div>
                {/* sandbox="" : no scripts, no same-origin access. The HTML is also escaped by the server. */}
                <iframe title="Report preview" sandbox="" srcDoc={previewHtml.html} style={{ width: '100%', height: 420, border: '1px solid var(--border-card)', borderRadius: 12, background: '#fff' }} />
              </div>
            )}
          </div>

          <SampleNote brandName={brand?.name} />
        </div>
      </div>
    </>
  );
};
