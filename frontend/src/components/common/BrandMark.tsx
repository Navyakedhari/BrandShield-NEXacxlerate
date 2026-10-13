import React from 'react';

/** The ScamMind AI mark of the design: a solid periwinkle shield holding a small three-node triangle. */
export const BrandMark: React.FC<{ size?: number }> = ({ size = 26 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="brand-mark">
    <path d="M12 2.2 20 5.4v6c0 4.9-3.3 8.3-8 10.4-4.7-2.1-8-5.5-8-10.4v-6l8-3.2Z" fill="#6b7cff" />
    <path d="M12 8.2 9.2 14.2h5.6L12 8.2Z" stroke="#0c0c10" strokeWidth="1.5" strokeLinejoin="round" />
    <circle cx="12" cy="8.2" r="1.5" fill="#0c0c10" />
    <circle cx="9.2" cy="14.2" r="1.5" fill="#0c0c10" />
    <circle cx="14.8" cy="14.2" r="1.5" fill="#0c0c10" />
  </svg>
);

/** "ScamMind" in white and "AI" in the accent colour. */
export const Wordmark: React.FC<{ upper?: boolean }> = ({ upper }) => (
  <>
    {upper ? 'SCAMMIND' : 'ScamMind'} <span className="wordmark-ai">AI</span>
  </>
);
