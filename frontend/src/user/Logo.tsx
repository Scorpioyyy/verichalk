/** 标识：靛蓝渐变圆角方块里一个粉笔勾——"核验过的题"。 */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id="vc-logo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6b88ff" />
          <stop offset="0.6" stopColor="#3d5af1" />
          <stop offset="1" stopColor="#5a3df1" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#vc-logo)" />
      <path
        d="M9 16.5l4.8 4.8L23.5 11"
        fill="none"
        stroke="#fff"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24.5" cy="7.5" r="2.4" fill="#ffc46b" />
    </svg>
  );
}
