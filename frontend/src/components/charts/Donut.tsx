import React from 'react';

interface DonutSegment {
  label: string;
  percentage: number;
  color: string;
}

interface DonutProps {
  data: DonutSegment[];
  totalLabel?: string;
  totalValue?: string;
  size?: number;
}

export const Donut: React.FC<DonutProps> = ({
  data,
  totalLabel = 'Total Threats',
  totalValue = '2,487',
  size = 180
}) => {
  const strokeWidth = 24;
  const radius = (size - strokeWidth) / 2;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;

  // Total percentage sum
  const totalPct = data.reduce((sum, d) => sum + d.percentage, 0) || 100;

  // Gaps between segments in pixels along circumference
  const gapPx = 3;
  const gapLength = gapPx;

  let accumulatedPercent = 0;

  return (
    <div style={{ position: 'relative', width: size, height: size, margin: '0 auto' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {data.map((seg, idx) => {
          const segLength = (seg.percentage / totalPct) * circumference;
          const strokeDasharray = `${Math.max(0, segLength - gapLength)} ${circumference - Math.max(0, segLength - gapLength)}`;
          const strokeDashoffset = -((accumulatedPercent / totalPct) * circumference) + circumference / 4; // Start at top

          accumulatedPercent += seg.percentage;

          return (
            <circle
              key={idx}
              cx={center}
              cy={center}
              r={radius}
              fill="transparent"
              stroke={seg.color}
              strokeWidth={strokeWidth}
              strokeDasharray={strokeDasharray}
              strokeDashoffset={strokeDashoffset}
              strokeLinecap="round"
            />
          );
        })}
      </svg>
      {/* Centre 2,487 / Total Threats */}
      <div
        style={{
          position: 'absolute',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          textAlign: 'center',
          pointerEvents: 'none'
        }}
      >
        <div style={{ fontSize: '22px', fontWeight: 800, color: 'var(--text-main)', lineHeight: 1 }}>
          {totalValue}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-dim)', marginTop: '4px', fontWeight: 500 }}>
          {totalLabel}
        </div>
      </div>
    </div>
  );
};
