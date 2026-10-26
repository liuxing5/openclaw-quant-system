/** 平台 Logo：渐变底 + K 线/趋势线，替代原来的「量」字图标。 */
export default function Logo({ size = 32, className = '' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="量化投研平台"
      className={className}
    >
      <defs>
        <linearGradient id="qd-logo-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#4f46e5" />
          <stop offset="55%" stopColor="#2563eb" />
          <stop offset="100%" stopColor="#0ea5e9" />
        </linearGradient>
        <linearGradient id="qd-logo-line" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#fbbf24" />
          <stop offset="100%" stopColor="#fde68a" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="62" height="62" rx="16" fill="url(#qd-logo-bg)" />
      <rect x="1" y="1" width="62" height="62" rx="16" fill="none" stroke="rgba(255,255,255,.18)" />
      <path
        d="M11 45.5 L22 35 L30 39.5 L41 24.5 L53 28.5"
        fill="none"
        stroke="url(#qd-logo-line)"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <rect x="15" y="39" width="5" height="12" rx="1.2" fill="#fb7185" />
      <rect x="28" y="31" width="5" height="15" rx="1.2" fill="#34d399" />
      <rect x="41" y="21" width="5" height="17" rx="1.2" fill="#fbbf24" opacity=".9" />
      <circle cx="53" cy="28.5" r="2.5" fill="#fff" />
      <circle cx="53" cy="28.5" r="5.5" fill="none" stroke="rgba(255,255,255,.45)" />
    </svg>
  )
}
